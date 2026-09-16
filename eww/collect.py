"""`eww collect`: fetch a source, write its snapshot and run log, then ingest the snapshot.

Byte-for-byte the same steps the scheduled collector (M1) will run, minus the git commit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from eww import collectors, config, heartbeat, ingest, snapshots
from eww.clock import now_iso, now_utc, slot_for, to_iso
from eww.ids import new_id

log = logging.getLogger(__name__)


@dataclass
class CollectResult:
    run: dict
    snapshot: Path | None
    ingest: ingest.IngestStats | None


def collect_source(conn, source_id: str, since: datetime, until: datetime, *, data_dir: Path | None = None) -> CollectResult:
    collector = collectors.get(source_id)
    data_dir = data_dir or config.DATA_DIR
    started = now_utc()
    envelope = {
        "format": config.SNAPSHOT_FORMAT,
        "source_id": source_id,
        "run_id": new_id(),
        "scheduled_for": slot_for(started, config.COLLECT_SLOT_HOURS),
        "started_at": to_iso(started),
        "since": to_iso(since),
        "until": to_iso(until),
    }
    log.info("collect start source=%s since=%s until=%s run=%s", source_id, envelope["since"], envelope["until"], envelope["run_id"])
    try:
        result = collector.fetch(since, until)
    except Exception as exc:  # network, HTTP 4xx, XML/JSON decoding: the run is recorded as failed
        response = getattr(exc, "response", None)
        run = heartbeat.run_from_envelope(
            {
                **envelope,
                "finished_at": now_iso(),
                "status": "failed",
                "http_status": getattr(response, "status_code", None),
                "items_seen": 0,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            },
            None,
        )
        heartbeat.record_run(conn, run)
        heartbeat.append_run_log(run, data_dir / "runs")
        log.error("collect failed source=%s error=%s", source_id, run["error"])
        return CollectResult(run=run, snapshot=None, ingest=None)
    envelope.update(
        finished_at=now_iso(),
        status=result.status,
        http_status=result.http_status,
        error=result.error,
        requests=result.requests,
        items_seen=len(result.items),
        items=result.items,
    )
    path = snapshots.write(envelope, data_dir)
    rel = snapshots.relative_path(path, data_dir)
    run = heartbeat.run_from_envelope(envelope, rel)
    heartbeat.record_run(conn, run)
    heartbeat.append_run_log(run, data_dir / "runs")
    stats = ingest.ingest_snapshot(conn, path, data_dir=data_dir, force=True)
    log.info("collect done source=%s status=%s items=%d new=%d changed=%d snapshot=%s", source_id, result.status, len(result.items), stats.new, stats.changed, rel)
    return CollectResult(run=run, snapshot=path, ingest=stats)
