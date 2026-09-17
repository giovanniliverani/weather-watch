"""Rebuild the EWW SQLite file from the raw snapshots and, on request, swap it in as the live database.

Identity rules apply when a record is first resolved, so a changed rule or threshold reaches old
data only through a rebuild (docs/architecture.md, §2 and §4 M2): apply the schema to a fresh file,
replay every snapshot the database has not seen (the `data` branch and local data/snapshots),
optionally collect a source the branch does not carry yet, resolve, print the counts that matter,
then back the live file up and replace it with the rebuilt one.

Run from the repository root with the project's environment, so `eww` imports:

    uv run python .cursor/skills/playbook/files/rebuild-database.py [--target PATH] [--collect SOURCE ...]
        [--days N] [--no-fetch] [--swap] [--keep] [--force]

Filled example (what the M2 build did on 2026-09-17, 8 seconds for 3,855 records):

    uv run python .cursor/skills/playbook/files/rebuild-database.py --collect copernicus --days 30 --swap
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

from eww import config, db, ingest, resolve
from eww.clock import now_utc, snapshot_stamp
from eww.collect import collect_source


def count(conn: sqlite3.Connection, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def copy_database(source: Path, destination: Path) -> None:
    """Copy through SQLite's backup API, so a WAL that has not been checkpointed is included."""
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", type=Path, default=config.DATA_DIR / "eww-rebuild.sqlite", help="the file to build (default: data/eww-rebuild.sqlite)")
    parser.add_argument("--collect", action="append", default=[], metavar="SOURCE", help="also fetch this spine source into the new file (repeatable)")
    parser.add_argument("--days", type=int, default=config.DEFAULT_COLLECT_DAYS, help="window for --collect")
    parser.add_argument("--no-fetch", action="store_true", help="do not git fetch the data branch first")
    parser.add_argument("--swap", action="store_true", help=f"back up {config.DB_PATH.name} and replace it with the rebuilt file")
    parser.add_argument("--keep", action="store_true", help="with --swap: keep the rebuilt file instead of deleting it")
    parser.add_argument("--force", action="store_true", help="overwrite an existing --target")
    args = parser.parse_args(argv)

    target: Path = args.target
    if target.exists():
        if not args.force:
            print(f"{target} exists; pass --force to rebuild over it", file=sys.stderr)
            return 2
        for suffix in ("", "-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)

    conn = db.connect(target)
    db.init_db(conn)
    print(f"schema applied to {target} (version {db.schema_version(conn)})")

    branch = ingest.ingest_branch(conn, fetch=not args.no_fetch)
    if branch.found:
        print(f"branch {branch.branch}: files={branch.files_listed} snapshots={branch.snapshots_new} records new={branch.records_new} changed={branch.records_changed} runs={branch.runs_inserted}")
    else:
        print(f"branch {branch.branch}: {branch.error}")
    local = ingest.ingest_pending(conn)
    print(f"local snapshots: files={sum(1 for r in local if not r.skipped)} records new={sum(r.new for r in local)} changed={sum(r.changed for r in local)}")

    now = now_utc()
    for source_id in args.collect:
        result = collect_source(conn, source_id, now - timedelta(days=args.days), now)
        status = "failed" if result.failed else f"{result.run['items_seen']} items, {result.ingest.new if result.ingest else 0} new records"
        print(f"collected {source_id}: {status}")

    stats = resolve.resolve(conn)
    print(
        f"resolved {stats.records_resolved} records: events created={stats.events_created} attached={stats.events_attached} "
        f"by key={stats.events_linked} auto-merged={stats.events_merged} proposals={stats.proposals_created}"
    )
    events = count(conn, "SELECT COUNT(*) FROM event")
    live_events = count(conn, "SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NULL")
    open_proposals = count(conn, "SELECT COUNT(*) FROM merge_proposal WHERE status = 'open'")
    print(
        f"events={events} live={live_events} open proposals={open_proposals} "
        f"unresolved={resolve.unresolved_count(conn)} duplicates={len(ingest.duplicates(conn))}"
    )
    conn.close()

    if not args.swap:
        print(f"rebuilt file left at {target}; pass --swap to make it the live database")
        return 0
    live = config.DB_PATH
    if live.exists():
        backup = live.with_name(f"{live.stem}-backup-{snapshot_stamp(now)}{live.suffix}")
        copy_database(live, backup)
        print(f"live database backed up to {backup}")
    copy_database(target, live)
    print(f"live database replaced: {live}")
    if not args.keep:
        for suffix in ("", "-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
