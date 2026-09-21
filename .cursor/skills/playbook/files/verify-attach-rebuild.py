"""Prove that `eww attach --rebuild` reproduces the live attachment decisions (M3 exit criterion 6).

Copies the live SQLite file through the backup API (so an un-checkpointed WAL is included), runs the
rebuild on the copy (every pipeline event_document row deleted and re-decided in document order; human
rows kept), then diffs event_document between the live file and the copy on every column except
decided_at. An empty diff is the criterion; a non-empty one lists the rows that differ and why they can
(an event that changed since the live decision: new episode, merge, title).

Run from the repository root with the project's environment, so `eww` imports:

    uv run python .cursor/skills/playbook/files/verify-attach-rebuild.py [--source PATH] [--copy PATH] [--keep]

Filled example (what the M3 build ran on 2026-09-21):

    uv run python .cursor/skills/playbook/files/verify-attach-rebuild.py --keep
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from eww import attach, config, db

COLUMNS = ("event_id", "document_id", "status", "score", "score_parts", "method", "decided_by")


def copy_database(source: Path, destination: Path) -> None:
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def rows(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple]:
    out = {}
    for row in conn.execute(f"SELECT {', '.join(COLUMNS)} FROM event_document ORDER BY event_id, document_id"):
        out[(row[0], row[1])] = tuple(row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=config.DB_PATH, help="the live database (default: config.DB_PATH)")
    parser.add_argument("--copy", type=Path, default=config.DATA_DIR / "eww-rebuild-check.sqlite", help="where the copy goes")
    parser.add_argument("--keep", action="store_true", help="keep the copy afterwards")
    args = parser.parse_args(argv)

    for suffix in ("", "-wal", "-shm"):
        Path(str(args.copy) + suffix).unlink(missing_ok=True)
    copy_database(args.source, args.copy)
    live = db.connect(args.source)
    before = rows(live)
    live.close()

    copy = db.connect(args.copy)
    db.init_db(copy)
    stats = attach.run(copy, rebuild=True)
    after = rows(copy)
    copy.close()
    print(f"rebuild on {args.copy}: deleted={stats.rebuilt_rows_deleted} documents={stats.documents} attached={stats.attached} candidates={stats.candidates}")

    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    print(f"live rows={len(before)} rebuilt rows={len(after)} missing={len(missing)} added={len(added)} changed={len(changed)}")
    for label, keys in (("missing after rebuild", missing), ("added by rebuild", added), ("changed", changed)):
        for key in keys[:20]:
            print(f"  {label}: event={key[0]} document={key[1]} live={before.get(key)} rebuilt={after.get(key)}")
    verdict = "PASS" if not (missing or added or changed) else "FAIL"
    print(f"verdict: {verdict} (M3 exit criterion 6: the event_document diff is empty)")
    if not args.keep:
        for suffix in ("", "-wal", "-shm"):
            Path(str(args.copy) + suffix).unlink(missing_ok=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
