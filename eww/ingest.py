"""Ingest: replay snapshot files into source_record and run logs into collector_run.

Two places hold snapshots: the local data directory (`eww collect` on this machine) and the `data`
branch (`eww collect --out` in GitHub Actions). Both are replayed through the same functions; the
branch is read with git plumbing (eww.gitdata), never checked out.

Idempotent on the natural key (source_id, external_id, external_episode) plus payload_hash: an
unseen key inserts, a changed payload updates the row, an unchanged one only bumps last_seen_at.
Every processed file is recorded in snapshot_ingest under its path inside the data layout
("snapshots/gdacs/2026-09-16T15-07Z.json", "runs/2026-09-16.jsonl"); a snapshot already there is
skipped, a runs file is re-read only when it has grown (its lines are INSERT OR IGNORE anyway).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from eww import collectors, config, gitdata, heartbeat, snapshots
from eww.clock import now_iso
from eww.ids import new_id

log = logging.getLogger(__name__)

RECORD_COLUMNS = (
    "hazard_type",
    "title",
    "observed_at",
    "started_at",
    "ended_at",
    "lat",
    "lon",
    "severity_raw",
    "glide_number",
)


@dataclass
class IngestStats:
    snapshot_path: str
    source_id: str
    kind: str = "snapshot"  # 'snapshot' | 'runs'
    items_seen: int = 0
    records: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class BranchIngest:
    branch: str
    fetched: bool | None = None  # None when fetching was not attempted
    found: bool = False
    head: str | None = None
    files_listed: int = 0
    results: list[IngestStats] = field(default_factory=list)
    error: str | None = None

    @property
    def snapshots_new(self) -> int:
        return sum(1 for r in self.results if r.kind == "snapshot" and not r.skipped)

    @property
    def records_new(self) -> int:
        return sum(r.new for r in self.results if r.kind == "snapshot")

    @property
    def records_changed(self) -> int:
        return sum(r.changed for r in self.results if r.kind == "snapshot")

    @property
    def runs_inserted(self) -> int:
        return sum(r.new for r in self.results if r.kind == "runs")


# ----------------------------------------------------------------------------- records
def payload_text(payload: dict) -> str:
    """Canonical JSON: sorted keys, no whitespace, so equal payloads hash equal."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def upsert_record(conn: sqlite3.Connection, rec: dict, seen_at: str) -> str:
    """Insert or update one normalised record. Returns 'new', 'changed' or 'unchanged'."""
    if not rec.get("observed_at"):
        raise ValueError(f"record {rec.get('source_id')}/{rec.get('external_id')} has no observed_at")
    text = payload_text(rec["payload"])
    digest = payload_hash(text)
    key = (rec["source_id"], str(rec["external_id"]), str(rec.get("external_episode") or ""))
    row = conn.execute(
        "SELECT source_record_id, payload_hash, last_seen_at FROM source_record "
        "WHERE source_id = ? AND external_id = ? AND external_episode = ?",
        key,
    ).fetchone()
    columns = {name: rec.get(name) for name in RECORD_COLUMNS}
    if row is None:
        conn.execute(
            """
            INSERT INTO source_record (source_record_id, source_id, external_id, external_episode, event_id,
                hazard_type, title, observed_at, started_at, ended_at, lat, lon, severity_raw, glide_number,
                payload, payload_hash, first_seen_at, last_seen_at)
            VALUES (:source_record_id, :source_id, :external_id, :external_episode, NULL,
                :hazard_type, :title, :observed_at, :started_at, :ended_at, :lat, :lon, :severity_raw, :glide_number,
                :payload, :payload_hash, :seen_at, :seen_at)
            """,
            {
                "source_record_id": new_id(),
                "source_id": key[0],
                "external_id": key[1],
                "external_episode": key[2],
                **columns,
                "payload": text,
                "payload_hash": digest,
                "seen_at": seen_at,
            },
        )
        return "new"
    if row["payload_hash"] != digest:
        conn.execute(
            """
            UPDATE source_record SET hazard_type = :hazard_type, title = :title, observed_at = :observed_at,
                started_at = :started_at, ended_at = :ended_at, lat = :lat, lon = :lon,
                severity_raw = :severity_raw, glide_number = :glide_number,
                payload = :payload, payload_hash = :payload_hash,
                last_seen_at = MAX(last_seen_at, :seen_at)
            WHERE source_record_id = :source_record_id
            """,
            {**columns, "payload": text, "payload_hash": digest, "seen_at": seen_at, "source_record_id": row["source_record_id"]},
        )
        return "changed"
    if row["last_seen_at"] < seen_at:
        conn.execute(
            "UPDATE source_record SET last_seen_at = ? WHERE source_record_id = ?",
            (seen_at, row["source_record_id"]),
        )
    return "unchanged"


# ----------------------------------------------------------------------------- snapshot files
def _already_ingested(conn: sqlite3.Connection, rel: str) -> sqlite3.Row | None:
    return conn.execute("SELECT snapshot_path, items_seen FROM snapshot_ingest WHERE snapshot_path = ?", (rel,)).fetchone()


def _record_file(conn: sqlite3.Connection, stats: IngestStats) -> None:
    conn.execute(
        """
        INSERT INTO snapshot_ingest (snapshot_path, ingested_at, items_seen, items_new, items_changed)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_path) DO UPDATE SET ingested_at = excluded.ingested_at,
            items_seen = excluded.items_seen, items_new = excluded.items_new, items_changed = excluded.items_changed
        """,
        (stats.snapshot_path, now_iso(), stats.items_seen, stats.new, stats.changed),
    )


def ingest_envelope(conn: sqlite3.Connection, envelope: dict, rel: str, *, force: bool = False) -> IngestStats:
    """Replay one snapshot envelope (already parsed) inside a single transaction."""
    if envelope.get("format") != config.SNAPSHOT_FORMAT:
        raise ValueError(f"{rel}: unexpected snapshot format {envelope.get('format')!r}")
    source_id = envelope["source_id"]
    stats = IngestStats(snapshot_path=rel, source_id=source_id, items_seen=int(envelope.get("items_seen") or len(envelope.get("items") or [])))
    if _already_ingested(conn, rel) and not force:
        stats.skipped = True
        return stats
    collector = collectors.get(source_id)
    seen_at = envelope.get("finished_at") or envelope.get("started_at") or now_iso()
    with conn:
        for item in envelope.get("items") or []:
            try:
                records = collector.iter_records(item)
            except Exception as exc:  # one malformed item must not sink the snapshot
                stats.errors.append(f"normalise: {exc}")
                log.warning("ingest normalise failed source=%s error=%s", source_id, exc)
                continue
            for rec in records:
                stats.records += 1
                try:
                    outcome = upsert_record(conn, rec, seen_at)
                except (sqlite3.DatabaseError, ValueError) as exc:
                    stats.errors.append(f"{rec.get('external_id')}/{rec.get('external_episode')}: {exc}")
                    log.warning("ingest upsert failed source=%s external_id=%s error=%s", source_id, rec.get("external_id"), exc)
                    continue
                setattr(stats, outcome, getattr(stats, outcome) + 1)
        _record_file(conn, stats)
        if envelope.get("run_id"):
            heartbeat.record_run(conn, heartbeat.run_from_envelope(envelope, rel))
    log.info(
        "ingest snapshot=%s source=%s items=%d records=%d new=%d changed=%d unchanged=%d errors=%d",
        rel, source_id, stats.items_seen, stats.records, stats.new, stats.changed, stats.unchanged, len(stats.errors),
    )
    return stats


def ingest_snapshot(conn: sqlite3.Connection, path: Path, *, data_dir: Path | None = None, force: bool = False) -> IngestStats:
    """Replay one local snapshot file."""
    rel = snapshots.relative_path(Path(path), data_dir)
    return ingest_envelope(conn, snapshots.read(Path(path)), rel, force=force)


def ingest_pending(conn: sqlite3.Connection, *, data_dir: Path | None = None, source_id: str | None = None) -> list[IngestStats]:
    """Replay every local snapshot file not yet recorded in snapshot_ingest, oldest first."""
    return [ingest_snapshot(conn, path, data_dir=data_dir) for path in snapshots.pending(conn, data_dir, source_id)]


# ----------------------------------------------------------------------------- run logs
def ingest_runs_text(conn: sqlite3.Connection, text: str, rel: str, *, force: bool = False) -> IngestStats:
    """Load one runs/<date>.jsonl into collector_run (INSERT OR IGNORE); re-read only when it grew."""
    lines = [line for line in text.splitlines() if line.strip()]
    stats = IngestStats(snapshot_path=rel, source_id="", kind="runs", items_seen=len(lines))
    previous = _already_ingested(conn, rel)
    if previous is not None and previous["items_seen"] >= len(lines) and not force:
        stats.skipped = True
        return stats
    seen, inserted = heartbeat.load_run_lines(conn, lines)
    stats.records, stats.new, stats.unchanged = seen, inserted, seen - inserted
    with conn:
        _record_file(conn, stats)
    log.info("ingest runs=%s lines=%d inserted=%d", rel, seen, inserted)
    return stats


# ----------------------------------------------------------------------------- the data branch
def ingest_branch(
    conn: sqlite3.Connection,
    *,
    branch: str | None = None,
    repo: Path | None = None,
    fetch: bool = True,
    force: bool = False,
) -> BranchIngest:
    """Fetch the data branch, then replay its unseen snapshot files and its run logs, in path order."""
    branch = branch or config.DATA_BRANCH
    result = BranchIngest(branch=branch)
    if fetch:
        result.fetched = gitdata.fetch_branch(branch, repo=repo)
    if not gitdata.branch_exists(branch, repo):
        result.error = f"branch {branch!r} not found locally" + ("" if result.fetched is not False else " and the fetch failed")
        log.warning("ingest branch=%s: %s", branch, result.error)
        return result
    result.found = True
    result.head = gitdata.head_commit(branch, repo)
    try:
        paths = gitdata.list_files(branch, repo=repo)
    except gitdata.GitError as exc:
        result.error = str(exc)
        return result
    result.files_listed = len(paths)
    known = {row[0]: row[1] for row in conn.execute("SELECT snapshot_path, items_seen FROM snapshot_ingest")}
    for path in paths:
        if path.startswith("snapshots/") and path.endswith(".json"):
            if path in known and not force:
                result.results.append(IngestStats(snapshot_path=path, source_id="", skipped=True))
                continue
            try:
                envelope = json.loads(gitdata.read_text(path, branch, repo))
                result.results.append(ingest_envelope(conn, envelope, path, force=force))
            except (gitdata.GitError, ValueError, KeyError) as exc:
                log.warning("ingest branch file skipped path=%s error=%s", path, exc)
                result.results.append(IngestStats(snapshot_path=path, source_id="", skipped=True, errors=[str(exc)]))
        elif path.startswith("runs/") and path.endswith(".jsonl"):
            try:
                result.results.append(ingest_runs_text(conn, gitdata.read_text(path, branch, repo), path, force=force))
            except gitdata.GitError as exc:
                log.warning("ingest branch runs skipped path=%s error=%s", path, exc)
                result.results.append(IngestStats(snapshot_path=path, source_id="", kind="runs", skipped=True, errors=[str(exc)]))
    log.info(
        "ingest branch=%s head=%s files=%d snapshots_new=%d records_new=%d runs_inserted=%d",
        branch, (result.head or "")[:12], result.files_listed, result.snapshots_new, result.records_new, result.runs_inserted,
    )
    return result


def duplicates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The invariant query from docs/architecture.md §4: must return no rows."""
    return conn.execute(
        """
        SELECT source_id, external_id, external_episode, COUNT(*) AS n
        FROM source_record GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
        """
    ).fetchall()
