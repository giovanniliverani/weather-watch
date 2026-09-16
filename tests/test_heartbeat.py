"""The heartbeat view, its summary, the schema migration that adds it, and the run-log loader."""

import json
import sqlite3
from datetime import timedelta

from eww import db, heartbeat
from eww.clock import now_utc, parse_iso, slot_for, to_iso


def add_run(conn, run_id, source_id, started_at, status="ok"):
    with conn:
        heartbeat.record_run(
            conn,
            {
                "run_id": run_id,
                "source_id": source_id,
                "scheduled_for": slot_for(parse_iso(started_at), 3),
                "started_at": started_at,
                "finished_at": started_at,
                "status": status,
                "http_status": 200,
                "items_seen": 1,
                "snapshot_path": None,
                "error": None,
            },
        )


def test_view_marks_late_and_failed_slots_as_missed(conn):
    now = now_utc()
    first_slot = parse_iso(slot_for(now - timedelta(hours=30), 3))
    on_time = first_slot + timedelta(minutes=7)
    late = first_slot + timedelta(hours=3, minutes=50)  # more than 45 minutes after its slot
    failed_at = first_slot + timedelta(hours=6, minutes=7)
    add_run(conn, "r1", "gdacs", to_iso(on_time))
    add_run(conn, "r1", "eonet", to_iso(on_time))
    add_run(conn, "r2", "gdacs", to_iso(late))
    add_run(conn, "r3", "gdacs", to_iso(failed_at), status="failed")

    rows = {row["scheduled_for"]: row for row in conn.execute("SELECT * FROM heartbeat")}
    assert rows[to_iso(first_slot)]["served"] == 1
    assert rows[to_iso(first_slot)]["sources_ok"] == 2
    assert rows[to_iso(first_slot)]["deadline"] == to_iso(first_slot + timedelta(minutes=45))
    assert rows[to_iso(first_slot + timedelta(hours=3))]["served"] == 0  # late run does not count
    assert rows[to_iso(first_slot + timedelta(hours=6))]["served"] == 0  # failed run does not count
    assert min(rows) == to_iso(first_slot)
    assert max(rows) == slot_for(now, 3)  # the view runs up to the current slot

    summary = heartbeat.summary(conn, now)
    expected_slots = [s for s in rows if rows[s]["deadline"] <= to_iso(now)]
    assert summary["expected_runs_7d"] == len(expected_slots) >= 10
    assert summary["observed_runs_7d"] == 1
    assert summary["missed_runs_7d"] == len(expected_slots) - 1
    assert summary["last_collector_run_at"] == to_iso(late)  # the last run that produced data
    assert summary["last_run_any_status_at"] == to_iso(failed_at)
    missed = heartbeat.missed_slots(conn, now, limit=3)
    assert len(missed) == 3 and to_iso(first_slot) not in missed

    # a slot served inside the current, still-open grace window counts straight away
    current_slot = parse_iso(slot_for(now, 3))
    if now - current_slot < timedelta(minutes=40):
        add_run(conn, "r4", "gdacs", to_iso(current_slot + timedelta(minutes=1)))
        fresh = heartbeat.summary(conn, now)
        assert fresh["observed_runs_7d"] == 2 and fresh["expected_runs_7d"] == summary["expected_runs_7d"] + 1


def test_summary_on_an_empty_database(conn):
    summary = heartbeat.summary(conn)
    assert summary == {
        "last_collector_run_at": None,
        "last_run_any_status_at": None,
        "first_collector_run_at": None,
        "expected_runs_7d": 0,
        "observed_runs_7d": 0,
        "missed_runs_7d": 0,
    }


def test_migration_from_version_1_adds_the_same_view(tmp_path):
    fresh = db.connect(tmp_path / "fresh.sqlite")
    db.init_db(fresh)
    fresh_view = fresh.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'heartbeat'").fetchone()[0]

    old = db.connect(tmp_path / "old.sqlite")
    ddl = db.config.SCHEMA_PATH.read_text(encoding="utf-8")
    v1_ddl = ddl[: ddl.index("CREATE VIEW heartbeat")]
    old.executescript(v1_ddl)
    with old:
        old.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-09-16T00:00:00Z')")
    assert db.schema_version(old) == 1
    assert db.init_db(old) is True
    assert db.schema_version(old) == 2
    assert [r[0] for r in old.execute("SELECT version FROM schema_version ORDER BY 1")] == [1, 2]
    migrated_view = old.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'heartbeat'").fetchone()[0]
    assert migrated_view == fresh_view
    assert db.init_db(old) is False  # nothing more to do


def test_run_log_lines_are_insert_or_ignore(conn):
    lines = [
        json.dumps({"run_id": "01RUN", "source_id": "gdacs", "scheduled_for": "2026-09-16T15:00:00Z", "started_at": "2026-09-16T15:07:10Z", "finished_at": "2026-09-16T15:07:40Z", "status": "ok", "http_status": 200, "items_seen": 5, "snapshot_path": "snapshots/gdacs/2026-09-16T15-07Z.json", "error": None}),
        json.dumps({"run_id": "01RUN", "source_id": "eonet", "scheduled_for": "2026-09-16T15:00:00Z", "started_at": "2026-09-16T15:07:41Z", "finished_at": "2026-09-16T15:07:45Z", "status": "failed", "http_status": None, "items_seen": 0, "snapshot_path": None, "error": "ConnectError: boom"}),
        "",
        "not json",
        json.dumps({"run_id": "01RUN", "source_id": "gdacs", "status": "ok"}),  # missing started_at
    ]
    assert heartbeat.load_run_lines(conn, lines) == (2, 2)
    assert heartbeat.load_run_lines(conn, lines) == (2, 0)
    rows = conn.execute("SELECT source_id, status, error FROM collector_run ORDER BY source_id").fetchall()
    assert [(r["source_id"], r["status"]) for r in rows] == [("eonet", "failed"), ("gdacs", "ok")]
    # a replayed line never overwrites what the collector recorded itself
    with conn:
        heartbeat.record_run(conn, {"run_id": "01RUN", "source_id": "gdacs", "scheduled_for": "2026-09-16T15:00:00Z", "started_at": "2026-09-16T15:07:10Z", "status": "ok", "items_seen": 9})
    heartbeat.load_run_lines(conn, lines[:1])
    assert conn.execute("SELECT items_seen FROM collector_run WHERE source_id = 'gdacs'").fetchone()[0] == 9
