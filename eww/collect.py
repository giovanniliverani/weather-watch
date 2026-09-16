"""`eww collect`: fetch a source, write its snapshot and run log, then (locally) ingest the snapshot.

Byte-for-byte the same steps in GitHub Actions and on the laptop; only the output directory differs.
With `--out <dir>` (Actions) there is no database: snapshots/ and runs/ are written under <dir> and
the workflow commits them to the `data` branch. Without it, files go to the local data directory
and are ingested straight away, as in M0.
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
    source_id: str
    run: dict
    snapshot: Path | None
    ingest: ingest.IngestStats | None

    @property
    def failed(self) -> bool:
        return self.run.get("status") == "failed"


def collect_source(
    conn,
    source_id: str,
    since: datetime,
    until: datetime,
    *,
    data_dir: Path | None = None,
    ingest_into_db: bool | None = None,
) -> CollectResult:
    """Fetch one source into <data_dir>. With a connection, record the run and ingest the snapshot.

    `conn` may be None (the Actions path): then nothing touches a database and only files are written.
    """
    collector = collectors.get(source_id)
    data_dir = data_dir or config.DATA_DIR
    do_ingest = (conn is not None) if ingest_into_db is None else (ingest_into_db and conn is not None)
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
    log.info("collect start source=%s since=%s until=%s run=%s out=%s", source_id, envelope["since"], envelope["until"], envelope["run_id"], data_dir)
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
        heartbeat.append_run_log(run, data_dir / "runs")
        if conn is not None:
            with conn:
                heartbeat.record_run(conn, run)
        log.error("collect failed source=%s error=%s", source_id, run["error"])
        return CollectResult(source_id=source_id, run=run, snapshot=None, ingest=None)
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
    heartbeat.append_run_log(run, data_dir / "runs")
    stats = None
    if conn is not None:
        with conn:
            heartbeat.record_run(conn, run)
        if do_ingest:
            stats = ingest.ingest_snapshot(conn, path, data_dir=data_dir, force=True)
    log.info(
        "collect done source=%s status=%s items=%d snapshot=%s ingested=%s",
        source_id, result.status, len(result.items), rel, "yes" if stats else "no",
    )
    return CollectResult(source_id=source_id, run=run, snapshot=path, ingest=stats)


def summary_line(results: list[CollectResult]) -> str:
    """The last stdout line of `eww collect`; the workflow puts it in the commit message."""
    parts = [f"{r.source_id}={'failed' if r.failed else r.run.get('items_seen', 0)}" for r in results]
    return "collect summary: " + " ".join(parts)
