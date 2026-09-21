"""Enrichment collectors (M3, laptop only): per-event, time-ranged queries that yield documents.

Unlike the spine collectors (eww.collectors), these never create events. For every event active in the
last config.ENRICH_ACTIVE_DAYS days, best severity first and capped at config.ENRICH_MAX_EVENTS_PER_RUN
per provider, a query built from the event's countries, admin1 names, storm name and hazard keywords is
sent to the provider for the window since the last successful run (`enrichment_run`), or from the event's
start minus config.ENRICH_BACKFILL_DAYS the first time. Each provider module exposes

    SOURCE_ID
    available(conn) -> (bool, reason)                     credentials present, budget left
    collector(http=None) -> object with .enrich_event(conn, event, since, until, now) -> EventResult
                                                            and .close()

A RateLimitExceeded from a provider ends that provider's run (the next `eww sync` continues where the
enrichment_run rows say); any other error skips the event and logs it.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from eww import config, ratelimit
from eww.clock import now_utc, parse_iso, to_iso

log = logging.getLogger(__name__)


@dataclass
class EventResult:
    query: str | None
    items_seen: int = 0
    documents_new: int = 0
    documents_seen: int = 0
    skipped: str | None = None  # why nothing was asked (no query terms, ...)


@dataclass
class EnrichStats:
    source_id: str
    events_considered: int = 0
    events_queried: int = 0
    events_skipped: int = 0
    items_seen: int = 0
    documents_new: int = 0
    documents_seen: int = 0
    errors: int = 0
    stopped: str | None = None  # a rate limit or a missing credential ended the run early
    queries: list[str] = field(default_factory=list)


def active_events(conn: sqlite3.Connection, *, days: int | None = None, now: datetime | None = None, limit: int | None = None, min_severity: float | None = None) -> list[sqlite3.Row]:
    """Live events observed (or ended) in the last `days` days, highest severity first."""
    now = now or now_utc()
    days = config.ENRICH_ACTIVE_DAYS if days is None else days
    since = to_iso(now - timedelta(days=days))
    rows = conn.execute(
        """
        SELECT e.* FROM event e
        WHERE e.merged_into_event_id IS NULL AND e.status IN ('active', 'ended')
          AND COALESCE(e.ended_at, e.last_observed_at) >= :since
          AND COALESCE(e.severity_score, 0) >= :min_severity
        ORDER BY COALESCE(e.severity_score, 0) DESC, e.last_observed_at DESC, e.event_id
        """,
        {"since": since, "min_severity": config.ENRICH_MIN_SEVERITY if min_severity is None else min_severity},
    ).fetchall()
    limit = config.ENRICH_MAX_EVENTS_PER_RUN if limit is None else limit
    return rows[:limit] if limit else rows


def window_for(conn: sqlite3.Connection, event: sqlite3.Row, source_id: str, now: datetime, *, lookback_days: int | None = None) -> tuple[datetime, datetime]:
    """[since, until]: from the last successful run for (event, source), else the event's start minus the backfill."""
    row = conn.execute("SELECT last_success_at FROM enrichment_run WHERE event_id = ? AND source_id = ?", (event["event_id"], source_id)).fetchone()
    if row is not None:
        since = parse_iso(row["last_success_at"])
    else:
        since = parse_iso(event["started_at"]) - timedelta(days=config.ENRICH_BACKFILL_DAYS)
    if lookback_days:
        since = max(since, now - timedelta(days=lookback_days))
    return min(since, now), now


def record_run(conn: sqlite3.Connection, event_id: str, source_id: str, until: datetime, result: EventResult, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO enrichment_run (event_id, source_id, last_success_at, queried_at, query, items_seen, documents_new)
        VALUES (:event_id, :source_id, :last_success_at, :queried_at, :query, :items_seen, :documents_new)
        ON CONFLICT(event_id, source_id) DO UPDATE SET
            last_success_at = excluded.last_success_at, queried_at = excluded.queried_at, query = excluded.query,
            items_seen = excluded.items_seen, documents_new = excluded.documents_new
        """,
        {
            "event_id": event_id,
            "source_id": source_id,
            "last_success_at": to_iso(until),
            "queried_at": to_iso(now),
            "query": result.query,
            "items_seen": result.items_seen,
            "documents_new": result.documents_new,
        },
    )


def modules() -> dict:
    from eww.enrich import gdelt, reliefweb

    return {gdelt.SOURCE_ID: gdelt, reliefweb.SOURCE_ID: reliefweb}


def run(
    conn: sqlite3.Connection,
    sources: list[str] | None = None,
    *,
    now: datetime | None = None,
    days: int | None = None,
    max_events: int | None = None,
    http=None,
) -> dict[str, EnrichStats]:
    """Query every configured provider for the active events. Returns one EnrichStats per source."""
    now = now or now_utc()
    registry = modules()
    out: dict[str, EnrichStats] = {}
    for source_id in sources or config.ENRICH_SOURCES:
        module = registry[source_id]
        stats = EnrichStats(source_id=source_id)
        out[source_id] = stats
        ok, reason = module.available(conn)
        if not ok:
            stats.stopped = reason
            log.warning("%s skipped: %s", source_id, reason)
            continue
        events = active_events(conn, days=days, now=now, limit=max_events)
        stats.events_considered = len(events)
        collector = module.collector(http=http)
        try:
            for event in events:
                since, until = window_for(conn, event, source_id, now, lookback_days=getattr(module, "LOOKBACK_DAYS", None))
                try:
                    result = collector.enrich_event(conn, event, since, until, now)
                except ratelimit.RateLimitExceeded as exc:
                    stats.stopped = str(exc)
                    log.warning("%s stopped for this run: %s", source_id, exc)
                    break
                except Exception as exc:  # one event's failure must not end the run
                    stats.errors += 1
                    log.warning("%s event=%s failed error=%s: %s", source_id, event["event_id"], type(exc).__name__, str(exc)[:300])
                    continue
                if result.skipped:
                    stats.events_skipped += 1
                    log.info("%s event=%s skipped: %s", source_id, event["event_id"], result.skipped)
                    continue
                stats.events_queried += 1
                stats.items_seen += result.items_seen
                stats.documents_new += result.documents_new
                stats.documents_seen += result.documents_seen
                if result.query:
                    stats.queries.append(result.query)
                with conn:
                    record_run(conn, event["event_id"], source_id, until, result, now)
                log.info("%s event=%s title=%r items=%d new=%d since=%s", source_id, event["event_id"], event["title"], result.items_seen, result.documents_new, to_iso(since))
        finally:
            collector.close()
    return out


def summary_line(stats: dict[str, EnrichStats]) -> str:
    parts = []
    for source_id, s in stats.items():
        if s.stopped and not s.events_queried:
            parts.append(f"{source_id}=skipped")
        else:
            parts.append(f"{source_id}: events={s.events_queried}/{s.events_considered} items={s.items_seen} new_docs={s.documents_new}" + (f" stopped" if s.stopped else "") + (f" errors={s.errors}" if s.errors else ""))
    return "enrich: " + "; ".join(parts)
