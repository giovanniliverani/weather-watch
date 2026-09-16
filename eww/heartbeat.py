"""Heartbeat: collector_run rows, the runs/<date>.jsonl log, and expected-vs-observed slots.

A quiet world shows recent runs with zero new items; a stopped pipeline shows missing slots.
`summary()` feeds the GeoJSON `meta` (last_collector_run_at, missed_runs_7d) and `eww doctor`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from eww import config
from eww.clock import now_utc, parse_iso, slot_for, to_iso

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


def run_from_envelope(envelope: dict, snapshot_path: str | None) -> dict:
    run = {key: envelope.get(key) for key in RUN_KEYS}
    run["snapshot_path"] = snapshot_path
    run["items_seen"] = int(envelope.get("items_seen") or len(envelope.get("items") or []))
    return run


def record_run(conn: sqlite3.Connection, run: dict) -> None:
    """Upsert one (run_id, source_id) row; safe to call from both `collect` and snapshot replay."""
    with conn:
        conn.execute(
            """
            INSERT INTO collector_run (run_id, source_id, scheduled_for, started_at, finished_at, status,
                                       http_status, items_seen, snapshot_path, error)
            VALUES (:run_id, :source_id, :scheduled_for, :started_at, :finished_at, :status,
                    :http_status, :items_seen, :snapshot_path, :error)
            ON CONFLICT(run_id, source_id) DO UPDATE SET
                finished_at = excluded.finished_at,
                status = excluded.status,
                http_status = excluded.http_status,
                items_seen = excluded.items_seen,
                snapshot_path = excluded.snapshot_path,
                error = excluded.error
            """,
            {key: run.get(key) for key in RUN_KEYS} | {"items_seen": int(run.get("items_seen") or 0)},
        )


def append_run_log(run: dict, runs_dir: Path | None = None) -> Path:
    """Append the run as one JSON line to data/runs/<YYYY-MM-DD>.jsonl (the M1 heartbeat format)."""
    folder = runs_dir or config.RUNS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    day = parse_iso(run["started_at"]).date().isoformat()
    path = folder / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
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
    """Compare the 3-hour slots expected since the first run (capped at `days`) with the slots served."""
    now = now or now_utc()
    hours = config.COLLECT_SLOT_HOURS
    row = conn.execute(
        "SELECT MIN(started_at), MAX(started_at) FROM collector_run"
    ).fetchone()
    first_run_at, last_run_at = row[0], row[1]
    result = {
        "last_collector_run_at": last_run_at,
        "first_collector_run_at": first_run_at,
        "expected_runs_7d": 0,
        "observed_runs_7d": 0,
        "missed_runs_7d": 0,
    }
    if first_run_at is None:
        return result
    window_start = max(parse_iso(first_run_at), now - timedelta(days=days))
    first_slot = parse_iso(slot_for(window_start, hours))
    last_slot = parse_iso(slot_for(now, hours))
    expected: list[str] = []
    cursor = first_slot
    while cursor <= last_slot:
        expected.append(to_iso(cursor))
        cursor += timedelta(hours=hours)
    if not expected:  # every run is dated after `now` (clock skew or replayed test data)
        return result
    served = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT scheduled_for FROM collector_run WHERE status IN ('ok', 'partial') AND scheduled_for >= ?",
            (expected[0],),
        )
    }
    result["expected_runs_7d"] = len(expected)
    result["observed_runs_7d"] = sum(1 for slot in expected if slot in served)
    result["missed_runs_7d"] = result["expected_runs_7d"] - result["observed_runs_7d"]
    return result
