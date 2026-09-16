"""Heartbeat: collector_run rows, the runs/<date>.jsonl log, and expected-versus-observed slots.

The `heartbeat` SQL view (sql/schema.sql, schema version 2) lists every 3-hour slot from the first
collector run to now and whether an 'ok' run started inside the slot's 45-minute grace window.
`summary()` reads it for the GeoJSON `meta`, the viewer's status strip and `eww doctor`: a quiet
world shows served slots with zero new items, a stopped pipeline shows missed ones.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from eww import config
from eww.clock import now_utc, parse_iso, to_iso

log = logging.getLogger(__name__)

RUN_KEYS = (
    "run_id",
    "source_id",
    "scheduled_for",
    "started_at",
    "finished_at",
    "status",
    "http_status",
    "items_seen",
    "snapshot_path",
    "error",
)
RUN_STATUSES = ("ok", "partial", "failed")

_COLUMNS = ", ".join(RUN_KEYS)
_PLACEHOLDERS = ", ".join(f":{key}" for key in RUN_KEYS)
_UPSERT = f"""
    INSERT INTO collector_run ({_COLUMNS}) VALUES ({_PLACEHOLDERS})
    ON CONFLICT(run_id, source_id) DO UPDATE SET
        finished_at = excluded.finished_at,
        status = excluded.status,
        http_status = excluded.http_status,
        items_seen = excluded.items_seen,
        snapshot_path = excluded.snapshot_path,
        error = excluded.error
"""
_INSERT_IGNORE = f"INSERT OR IGNORE INTO collector_run ({_COLUMNS}) VALUES ({_PLACEHOLDERS})"


def run_from_envelope(envelope: dict, snapshot_path: str | None) -> dict:
    run = {key: envelope.get(key) for key in RUN_KEYS}
    run["snapshot_path"] = snapshot_path
    run["items_seen"] = int(envelope.get("items_seen") or len(envelope.get("items") or []))
    return run


def record_run(conn: sqlite3.Connection, run: dict, *, replace: bool = True) -> bool:
    """Write one (run_id, source_id) row inside the caller's transaction.

    replace=True refreshes an existing row (the collector recording its own run); replace=False is
    INSERT OR IGNORE, for replaying runs/*.jsonl. Returns True when a row was written.
    """
    values = {key: run.get(key) for key in RUN_KEYS} | {"items_seen": int(run.get("items_seen") or 0)}
    cursor = conn.execute(_UPSERT if replace else _INSERT_IGNORE, values)
    return cursor.rowcount > 0


def parse_run_line(line: str) -> dict | None:
    """One JSON line of runs/<date>.jsonl, or None when it is blank or malformed."""
    text = line.strip()
    if not text:
        return None
    try:
        run = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("run log line skipped error=%s line=%r", exc, text[:120])
        return None
    if not isinstance(run, dict) or not all(run.get(key) for key in ("run_id", "source_id", "started_at", "status")):
        log.warning("run log line skipped: missing keys line=%r", text[:120])
        return None
    if run["status"] not in RUN_STATUSES:
        log.warning("run log line skipped: unknown status %r", run["status"])
        return None
    return run


def load_run_lines(conn: sqlite3.Connection, lines: Iterable[str]) -> tuple[int, int]:
    """INSERT OR IGNORE every valid line into collector_run. Returns (valid lines, rows inserted)."""
    seen = inserted = 0
    with conn:
        for line in lines:
            run = parse_run_line(line)
            if run is None:
                continue
            seen += 1
            try:
                if record_run(conn, run, replace=False):
                    inserted += 1
            except sqlite3.DatabaseError as exc:  # e.g. a source_id not yet seeded
                log.warning("run log line skipped run=%s source=%s error=%s", run.get("run_id"), run.get("source_id"), exc)
    return seen, inserted


def append_run_log(run: dict, runs_dir: Path | None = None) -> Path:
    """Append the run as one JSON line to <runs_dir>/<YYYY-MM-DD>.jsonl (the data branch format)."""
    folder = runs_dir or config.RUNS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    day = parse_iso(run["started_at"]).date().isoformat()
    path = folder / f"{day}.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({key: run.get(key) for key in RUN_KEYS}, ensure_ascii=False) + "\n")
    return path


def last_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The most recent run per source."""
    return conn.execute(
        """
        SELECT r.* FROM collector_run r
        JOIN (SELECT source_id, MAX(started_at) AS started_at FROM collector_run GROUP BY source_id) m
          ON m.source_id = r.source_id AND m.started_at = r.started_at
        ORDER BY r.source_id
        """
    ).fetchall()


def summary(conn: sqlite3.Connection, now: datetime | None = None, days: int = 7) -> dict:
    """Slots expected and served in the last `days` days, from the `heartbeat` view.

    A slot counts only once its 45-minute deadline has passed. `last_collector_run_at` is the last
    run that produced data (status ok or partial); a run that failed does not refresh it.
    """
    now = now or now_utc()
    now_iso = to_iso(now)
    window_start = to_iso(now - timedelta(days=days))
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(served), 0) FROM heartbeat WHERE deadline <= ? AND scheduled_for >= ?",
        (now_iso, window_start),
    ).fetchone()
    expected, observed = int(row[0]), int(row[1])
    last_ok = conn.execute("SELECT MAX(started_at) FROM collector_run WHERE status IN ('ok', 'partial')").fetchone()[0]
    last_any = conn.execute("SELECT MAX(started_at) FROM collector_run").fetchone()[0]
    first = conn.execute("SELECT MIN(started_at) FROM collector_run").fetchone()[0]
    return {
        "last_collector_run_at": last_ok,
        "last_run_any_status_at": last_any,
        "first_collector_run_at": first,
        "expected_runs_7d": expected,
        "observed_runs_7d": observed,
        "missed_runs_7d": expected - observed,
    }


def missed_slots(conn: sqlite3.Connection, now: datetime | None = None, days: int = 7, limit: int = 12) -> list[str]:
    """The most recent slots in the window with no ok run inside their grace period."""
    now = now or now_utc()
    rows = conn.execute(
        """
        SELECT scheduled_for FROM heartbeat
        WHERE deadline <= ? AND scheduled_for >= ? AND served = 0
        ORDER BY scheduled_for DESC LIMIT ?
        """,
        (to_iso(now), to_iso(now - timedelta(days=days)), int(limit)),
    ).fetchall()
    return [row[0] for row in rows]
