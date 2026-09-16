"""Ingest: replay snapshot files into source_record and collector_run. The only writer of both.

Idempotent on the natural key (source_id, external_id, external_episode) plus payload_hash:
an unseen key inserts, a changed payload updates the row, an unchanged one only bumps
last_seen_at. A snapshot file that `snapshot_ingest` already lists is skipped unless forced.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from eww import collectors, heartbeat, snapshots
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
    items_seen: int = 0
    records: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)


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


def ingest_snapshot(conn: sqlite3.Connection, path: Path, *, data_dir: Path | None = None, force: bool = False) -> IngestStats:
    """Replay one snapshot file inside a single transaction."""
    rel = snapshots.relative_path(Path(path), data_dir)
    envelope = snapshots.read(Path(path))
    source_id = envelope["source_id"]
    stats = IngestStats(snapshot_path=rel, source_id=source_id, items_seen=int(envelope.get("items_seen") or len(envelope.get("items") or [])))
    already = conn.execute("SELECT 1 FROM snapshot_ingest WHERE snapshot_path = ?", (rel,)).fetchone()
    if already and not force:
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
        conn.execute(
            """
            INSERT INTO snapshot_ingest (snapshot_path, ingested_at, items_seen, items_new, items_changed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_path) DO UPDATE SET ingested_at = excluded.ingested_at,
                items_seen = excluded.items_seen, items_new = excluded.items_new, items_changed = excluded.items_changed
            """,
            (rel, now_iso(), stats.items_seen, stats.new, stats.changed),
        )
    if envelope.get("run_id"):
        heartbeat.record_run(conn, heartbeat.run_from_envelope(envelope, rel))
    log.info(
        "ingest snapshot=%s source=%s items=%d records=%d new=%d changed=%d unchanged=%d errors=%d",
        rel, source_id, stats.items_seen, stats.records, stats.new, stats.changed, stats.unchanged, len(stats.errors),
    )
    return stats


def ingest_pending(conn: sqlite3.Connection, *, data_dir: Path | None = None, source_id: str | None = None) -> list[IngestStats]:
    """Replay every snapshot file not yet recorded in snapshot_ingest, oldest first."""
    return [ingest_snapshot(conn, path, data_dir=data_dir) for path in snapshots.pending(conn, data_dir, source_id)]


def duplicates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The invariant query from docs/architecture.md §4: must return no rows."""
    return conn.execute(
        """
        SELECT source_id, external_id, external_episode, COUNT(*) AS n
        FROM source_record GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
        """
    ).fetchall()
