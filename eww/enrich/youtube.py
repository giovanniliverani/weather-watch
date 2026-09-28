"""YouTube collector: search.list only when a key exists, never by attaching a billing account.

GET https://www.googleapis.com/youtube/v3/search with the key from .env. At most 100 searches per
UTC day (the 2026 search.list bucket). Events are rotated by oldest search first, then severity,
and each search is one collector_run row with items_seen=1 for source 'youtube'. A response that
says a billing account is required stops the collector in one line; quota is not purchased.

Compliance: attached videos whose fetched_at is older than 30 days are refreshed with
videos.list?part=snippet (up to 50 ids, 1 unit). An id the response omits gets removed_at.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from eww import config, documents, heartbeat
from eww import http as http_mod
from eww import ratelimit
from eww.clock import normalise_iso, to_iso
from eww.enrich import EventResult, queries
from eww.enrich.errors import SkipSource
from eww.enrich.store import store_document
from eww.ids import new_id

log = logging.getLogger(__name__)

SOURCE_ID = "youtube"
MAX_EVENTS_PER_RUN = 0  # rotation covers every active event; the daily cap stops the run


def available(conn: sqlite3.Connection) -> tuple[bool, str]:
    if not config.YOUTUBE_API_KEY:
        return False, "YOUTUBE_API_KEY is not set; skipped. No billing account will be attached to create one."
    return True, ""


def collector(http=None, *, sleep=None) -> "YouTubeCollector":
    return YouTubeCollector(http=http, sleep=sleep)


def day_bounds(now: datetime) -> tuple[str, str]:
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    return start, end


def searches_today(conn: sqlite3.Connection, now: datetime) -> int:
    start, end = day_bounds(now)
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(items_seen), 0) FROM collector_run WHERE source_id = ? AND started_at >= ? AND started_at < ?",
            (SOURCE_ID, start, end),
        ).fetchone()[0]
    )


def order_events(conn: sqlite3.Connection, events: list, now: datetime) -> list:
    """Least recently searched first, so the 100 daily searches rotate; severity breaks ties."""
    today = to_iso(now)[:10]
    queried = {
        row["event_id"]: row["queried_at"]
        for row in conn.execute("SELECT event_id, queried_at FROM enrichment_run WHERE source_id = ?", (SOURCE_ID,))
    }

    def key(event):
        stamp = queried.get(event["event_id"]) or ""
        return (stamp[:10] == today, stamp or "0000", -(event["severity_score"] or 0))

    return sorted(events, key=key)


def _billing(body: dict) -> bool:
    error = body.get("error") or {}
    reasons = [str(item.get("reason") or "") for item in error.get("errors") or []]
    message = str(error.get("message") or "").lower()
    if any(reason in {"billingNotEnabled", "accountBillingNotEnabled"} for reason in reasons):
        return True
    return "billing" in message


def _quota(body: dict) -> bool:
    error = body.get("error") or {}
    reasons = [str(item.get("reason") or "") for item in error.get("errors") or []]
    return any(reason in {"quotaExceeded", "dailyLimitExceeded"} for reason in reasons)


class YouTubeCollector:
    def __init__(self, http=None, *, sleep=None):
        kwargs = {"sleep": sleep} if sleep else {}
        limiter = ratelimit.MinInterval(config.YOUTUBE_MIN_INTERVAL_S, **kwargs)
        self.provider = http_mod.Provider(SOURCE_ID, limiters=[limiter], http=http, timeout=config.HTTP_TIMEOUT_S, retries=1, **kwargs)
        self.blocked: str | None = None

    def close(self) -> None:
        self.provider.close()

    def _get(self, url: str, params: dict) -> tuple[int, dict]:
        status, body = self.provider.get_json(url, {**params, "key": config.YOUTUBE_API_KEY})
        if not isinstance(body, dict):
            body = {}
        if status == 200:
            return status, body
        if _billing(body):
            self.blocked = "YouTube skipped: the free quota cannot be used without a billing account, and none will be attached."
            raise SkipSource(self.blocked)
        if _quota(body):
            raise ratelimit.RateLimitExceeded("youtube: the daily search quota is exhausted")
        if status in {400, 403}:
            self.blocked = "YouTube skipped: the key was refused."
            raise SkipSource(self.blocked)
        raise ValueError(f"youtube status {status}")

    def enrich_event(self, conn: sqlite3.Connection, event: sqlite3.Row, since: datetime, until: datetime, now: datetime) -> EventResult:
        if self.blocked:
            raise SkipSource(self.blocked)
        today = to_iso(now)[:10]
        row = conn.execute(
            "SELECT queried_at FROM enrichment_run WHERE event_id = ? AND source_id = ?",
            (event["event_id"], SOURCE_ID),
        ).fetchone()
        if row is not None and (row["queried_at"] or "")[:10] == today:
            return EventResult(query=None, skipped="already searched in this UTC day")
        used = searches_today(conn, now)
        if used >= config.YOUTUBE_SEARCHES_PER_DAY:
            raise ratelimit.RateLimitExceeded(f"youtube: {used} searches already recorded for this UTC day")
        query = queries.social_query(queries.event_terms(conn, event), "en")
        if not query:
            return EventResult(query=None, skipped="no place or storm name to query with")
        try:
            status, body = self._get(
                config.YOUTUBE_SEARCH_URL,
                {
                    "part": "snippet",
                    "type": "video",
                    "q": query,
                    "publishedAfter": event["started_at"],
                    "order": "date",
                    "maxResults": config.YOUTUBE_MAX_RESULTS,
                },
            )
        except ratelimit.RateLimitExceeded:
            _record_search(conn, now, fill_to_cap=True)
            raise
        if status != 200:
            raise ValueError(f"youtube search status {status}")
        _record_search(conn, now)
        result = EventResult(query=query, items_seen=len(body.get("items") or []))
        fetched_at = to_iso(now)
        with conn:
            for item in body.get("items") or []:
                stored = _store(conn, event["event_id"], item, fetched_at)
                if stored is None:
                    continue
                result.documents_seen += 1
                result.documents_new += 1 if stored.created else 0
        return result

    def sweep(self, conn: sqlite3.Connection, now: datetime) -> int:
        """Refresh attached videos last fetched more than 30 days ago. One videos.list call is not a search."""
        if self.blocked or not config.YOUTUBE_API_KEY:
            return 0
        cutoff = to_iso(now - timedelta(days=config.YOUTUBE_REFRESH_DAYS))
        rows = conn.execute(
            """
            SELECT DISTINCT d.document_id, d.external_id FROM document d
            JOIN event_document ed ON ed.document_id = d.document_id AND ed.status = 'attached'
            WHERE d.source_id = ? AND d.kind = 'video' AND d.removed_at IS NULL
              AND d.external_id IS NOT NULL AND d.fetched_at < ?
            ORDER BY d.document_id
            """,
            (SOURCE_ID, cutoff),
        ).fetchall()
        removed = 0
        when = to_iso(now)
        for start in range(0, len(rows), config.YOUTUBE_VIDEOS_BATCH):
            batch = rows[start : start + config.YOUTUBE_VIDEOS_BATCH]
            ids = ",".join(row["external_id"] for row in batch)
            try:
                status, body = self._get(config.YOUTUBE_VIDEOS_URL, {"part": "snippet", "id": ids})
            except ratelimit.RateLimitExceeded:
                log.warning("youtube compliance stopped: quota exhausted; stored videos were left unchanged")
                break
            if status != 200:
                continue
            by_id = {item.get("id"): (item.get("snippet") or {}) for item in body.get("items") or []}
            missing = []
            with conn:
                for row in batch:
                    snippet = by_id.get(row["external_id"])
                    if snippet is None:
                        missing.append(row["document_id"])
                        continue
                    _refresh(conn, row["document_id"], snippet, when)
                removed += documents.set_removed(conn, missing, when)
        if removed:
            log.info("youtube compliance removed=%d", removed)
        return removed


def _record_search(conn: sqlite3.Connection, now: datetime, *, fill_to_cap: bool = False) -> None:
    """One collector_run row per search. fill_to_cap records the rest of today's budget after Google says the quota is spent."""
    used = searches_today(conn, now)
    count = 1 if not fill_to_cap else max(1, config.YOUTUBE_SEARCHES_PER_DAY - used)
    with conn:
        heartbeat.record_run(
            conn,
            {
                "run_id": new_id(),
                "source_id": SOURCE_ID,
                "scheduled_for": now.strftime("%Y-%m-%dT00:00:00Z"),
                "started_at": to_iso(now),
                "finished_at": to_iso(now),
                "status": "ok",
                "http_status": 200,
                "items_seen": count,
                "snapshot_path": None,
                "error": None,
            },
        )


def _video(item: dict) -> dict | None:
    video_id = ((item.get("id") or {}).get("videoId") or item.get("id") or "")
    if isinstance(video_id, dict):
        video_id = ""
    video_id = str(video_id).strip()
    snippet = item.get("snippet") or {}
    if not video_id or video_id.startswith("{"):
        return None
    thumb = ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url")
    return {
        "external_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": snippet.get("title"),
        "author": snippet.get("channelTitle"),
        "published_at": normalise_iso(snippet.get("publishedAt")),
        "media_url": thumb if isinstance(thumb, str) and thumb.startswith("http") else None,
        "payload": {
            "videoId": video_id,
            "title": snippet.get("title"),
            "channelTitle": snippet.get("channelTitle"),
            "publishedAt": snippet.get("publishedAt"),
            "thumbnail": thumb,
        },
    }


def _store(conn, event_id: str, item: dict, fetched_at: str):
    video = _video(item)
    if video is None:
        return None
    try:
        return store_document(
            conn,
            event_id=event_id,
            source_id=SOURCE_ID,
            kind="video",
            url=video["url"],
            external_id=video["external_id"],
            title=video["title"],
            author=video["author"],
            published_at=video["published_at"],
            media_url=video["media_url"],
            media_kind="image" if video["media_url"] else "none",
            payload=video["payload"],
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        log.debug("youtube video skipped id=%s error=%s", video["external_id"], exc)
        return None


def _refresh(conn, document_id: str, snippet: dict, fetched_at: str) -> None:
    thumb = ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url")
    thumb = thumb if isinstance(thumb, str) and thumb.startswith("http") else None
    conn.execute(
        """
        UPDATE document SET title = COALESCE(?, title), author = COALESCE(?, author),
            published_at = COALESCE(?, published_at),
            media_url = COALESCE(?, media_url),
            media_kind = CASE WHEN ? IS NOT NULL THEN 'image' ELSE media_kind END,
            fetched_at = ?
        WHERE document_id = ?
        """,
        (
            (snippet.get("title") or "").strip() or None,
            (snippet.get("channelTitle") or "").strip() or None,
            normalise_iso(snippet.get("publishedAt")),
            thumb,
            thumb,
            fetched_at,
            document_id,
        ),
    )
