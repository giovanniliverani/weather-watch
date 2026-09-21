"""GDELT DOC 2.0 collector: one ArtList query per active event, at least 5 seconds apart.

GET https://api.gdeltproject.org/api/v2/doc/doc?query=<q>&mode=ArtList&format=json&maxrecords=250
    &sort=DateDesc&startdatetime=YYYYMMDDHHMMSS&enddatetime=YYYYMMDDHHMMSS

Articles carry url, url_mobile, title, seendate, socialimage, domain, language, sourcecountry and become
`document` rows (kind 'article', publisher = domain, media_url = socialimage) plus a `document_retrieval`
row naming the event whose query found them. GDELT signals overuse with HTTP 429 or with a plain-text
notice ("Please limit requests to one every 5 seconds...") under status 200; both raise RateLimitExceeded
and end the GDELT run. No key, no article bodies: headlines and references only.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from eww import config, documents, ratelimit
from eww import http as http_mod
from eww.clock import to_iso
from eww.enrich import EventResult, queries

log = logging.getLogger(__name__)

SOURCE_ID = "gdelt"
LOOKBACK_DAYS = config.GDELT_LOOKBACK_DAYS
_STAMP = "%Y%m%d%H%M%S"


def available(conn: sqlite3.Connection) -> tuple[bool, str]:
    return True, ""


def collector(http=None, *, sleep=None) -> "GdeltCollector":
    return GdeltCollector(http=http, sleep=sleep)


def parse_seendate(value: str | None) -> str | None:
    """'20260921T120000Z' -> '2026-09-21T12:00:00Z'; None when unparseable."""
    text = (value or "").strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"):
        try:
            return to_iso(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


class GdeltCollector:
    def __init__(self, http=None, *, sleep=None):
        limiter = ratelimit.MinInterval(config.GDELT_MIN_INTERVAL_S, **({"sleep": sleep} if sleep else {}))
        self.provider = http_mod.Provider(SOURCE_ID, limiters=[limiter], http=http, timeout=config.GDELT_TIMEOUT_S, retries=2, **({"sleep": sleep} if sleep else {}))

    def close(self) -> None:
        self.provider.close()

    def fetch(self, query: str, since: datetime, until: datetime) -> list[dict]:
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": config.GDELT_MAX_RECORDS,
            "sort": "DateDesc",
            "startdatetime": since.strftime(_STAMP),
            "enddatetime": until.strftime(_STAMP),
        }
        response = self.provider.get(config.GDELT_DOC_URL, params)
        text = response.text.strip()
        if not text:
            return []
        if text.lower().startswith("please limit requests"):
            raise ratelimit.RateLimitExceeded("gdelt asked for more spacing: " + text[:120])
        if not text.startswith("{"):
            raise ValueError(f"gdelt answered {response.status_code} with text: {text[:160]}")
        body = response.json()
        return list(body.get("articles") or [])

    def enrich_event(self, conn: sqlite3.Connection, event: sqlite3.Row, since: datetime, until: datetime, now: datetime) -> EventResult:
        terms = queries.event_terms(conn, event)
        query = queries.gdelt_query(terms)
        if not query:
            return EventResult(query=None, skipped="no place or storm name to query with")
        articles = self.fetch(query, since, until)
        result = EventResult(query=query, items_seen=len(articles))
        fetched_at = to_iso(now)
        with conn:
            for article in articles:
                url = (article.get("url") or "").strip()
                if not url:
                    continue
                try:
                    upsert = documents.upsert_document(
                        conn,
                        source_id=SOURCE_ID,
                        kind="article",
                        url=url,
                        title=article.get("title"),
                        language=article.get("language"),
                        publisher=article.get("domain"),
                        published_at=parse_seendate(article.get("seendate")),
                        media_url=article.get("socialimage") or None,
                        media_kind="image" if article.get("socialimage") else "none",
                        payload=article,
                        fetched_at=fetched_at,
                    )
                except ValueError as exc:
                    log.debug("gdelt article skipped url=%r error=%s", url, exc)
                    continue
                result.documents_seen += 1
                result.documents_new += 1 if upsert.created else 0
                documents.record_retrieval(conn, upsert.document_id, event["event_id"], SOURCE_ID, fetched_at)
        return result
