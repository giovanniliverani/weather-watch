"""Raw snapshot files: data/snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json.

The file is the unit of replay. Its envelope is exactly what the scheduled collector (M1) will
commit to the `data` branch, so `ingest` never needs to know whether a file was written here or
in GitHub Actions:

    {
      "format": "eww.snapshot/1",
      "source_id": "gdacs",
      "run_id": "<ULID>",                      minted by the collector, shared by collector_run
      "scheduled_for": "2026-09-16T15:00:00Z",  the 3-hour slot the run served
      "started_at": "...", "finished_at": "...",
      "since": "...", "until": "...",           the window that was requested
      "status": "ok" | "partial",
      "http_status": 200, "error": null,
      "requests": [{"url": ..., "params": {...}, "status": 200, "items": 100}, ...],
      "items_seen": 523,
      "items": [ ...raw feed items, verbatim... ]
    }

`snapshot_path` values stored in the database are relative to the data directory and use
forward slashes ("snapshots/gdacs/2026-09-16T15-10Z.json") so they match the data branch.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from eww import config
from eww.clock import parse_iso, snapshot_stamp


def path_for(source_id: str, started_at: str | datetime, data_dir: Path | None = None) -> Path:
    dt = parse_iso(started_at) if isinstance(started_at, str) else started_at
    root = (data_dir or config.DATA_DIR) / "snapshots" / source_id
    return root / f"{snapshot_stamp(dt)}.json"


def relative_path(path: Path, data_dir: Path | None = None) -> str:
    root = (data_dir or config.DATA_DIR).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def absolute_path(rel: str, data_dir: Path | None = None) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute():
        return candidate
    return (data_dir or config.DATA_DIR) / candidate


def write(envelope: dict, data_dir: Path | None = None) -> Path:
    """Write the envelope for its source and start time; an existing file for the same minute is replaced."""
    if envelope.get("format") != config.SNAPSHOT_FORMAT:
        raise ValueError(f"unexpected snapshot format {envelope.get('format')!r}")
    path = path_for(envelope["source_id"], envelope["started_at"], data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=None), encoding="utf-8")
    tmp.replace(path)
    return path


def read(path: Path) -> dict:
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    if envelope.get("format") != config.SNAPSHOT_FORMAT:
        raise ValueError(f"{path}: unexpected snapshot format {envelope.get('format')!r}")
    return envelope


def list_all(data_dir: Path | None = None, source_id: str | None = None) -> list[Path]:
    root = (data_dir or config.DATA_DIR) / "snapshots"
    if not root.exists():
        return []
    sources = [root / source_id] if source_id else sorted(p for p in root.iterdir() if p.is_dir())
    files: list[Path] = []
    for folder in sources:
        if folder.exists():
            files.extend(sorted(folder.glob("*.json")))
    return files


def pending(conn: sqlite3.Connection, data_dir: Path | None = None, source_id: str | None = None) -> list[Path]:
    """Snapshot files on disk that `snapshot_ingest` has not recorded yet, oldest first."""
    seen = {row[0] for row in conn.execute("SELECT snapshot_path FROM snapshot_ingest")}
    return [p for p in list_all(data_dir, source_id) if relative_path(p, data_dir) not in seen]
