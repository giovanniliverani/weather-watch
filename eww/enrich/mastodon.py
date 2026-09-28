"""Mastodon collector: public tag timelines on mastodon.social, no token.

GET https://mastodon.social/api/v1/timelines/tag/<tag>?limit=40 once per hazard tag
(flood, alluvione, wildfire, hurricane, ...). A status is stored as kind='post' only when its
text names the event's storm, admin1 or, failing those, its country, and that retrieval is what
earns the query prior. HTML is stripped; the first media_attachments preview_url is a reference.
At most 300 requests per 5 minutes, and at least one second between calls.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote

from eww import config, documents
from eww import http as http_mod
from eww import ratelimit
from eww.clock import normalise_iso, parse_iso, to_iso
from eww.enrich import EventResult, queries
from eww.enrich.store import store_document

log = logging.getLogger(__name__)

SOURCE_ID = "mastodon"
MAX_EVENTS_PER_RUN = 0  # matching is local; each tag is fetched once per run


def available(conn: sqlite3.Connection) -> tuple[bool, str]:
    return True, ""


def collector(http=None, *, sleep=None) -> "MastodonCollector":
    return MastodonCollector(http=http, sleep=sleep)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")


def strip_html(value: str | None) -> str:
    parser = _Text()
    parser.feed(value or "")
    return " ".join("".join(parser.parts).split())


def preview_of(status: dict) -> str | None:
    for attachment in status.get("media_attachments") or []:
        url = (attachment or {}).get("preview_url")
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


class MastodonCollector:
    def __init__(self, http=None, *, sleep=None):
        kwargs = {"sleep": sleep} if sleep else {}
        interval = ratelimit.MinInterval(config.MASTODON_MIN_INTERVAL_S, **kwargs)
        window = ratelimit.SlidingWindow(config.MASTODON_MAX_CALLS, config.MASTODON_WINDOW_S, name=SOURCE_ID, **kwargs)
        window.preload(ratelimit.recent_call_ages(SOURCE_ID, config.MASTODON_WINDOW_S))
        self.provider = http_mod.Provider(SOURCE_ID, limiters=[interval, window], http=http, timeout=config.HTTP_TIMEOUT_S, retries=2, **kwargs)
        self._timelines: dict[str, list[dict]] = {}

    def close(self) -> None:
        self.provider.close()

    def timeline(self, tag: str) -> list[dict]:
        if tag in self._timelines:
            return self._timelines[tag]
        url = f"{config.MASTODON_BASE}/api/v1/timelines/tag/{quote(tag)}"
        status, body = self.provider.get_json(url, {"limit": config.MASTODON_LIMIT})
        if status != 200 or not isinstance(body, list):
            log.warning("mastodon tag=%s status=%s; that tag was skipped", tag, status)
            self._timelines[tag] = []
            return []
        self._timelines[tag] = body
        return body

    def enrich_event(self, conn: sqlite3.Connection, event: sqlite3.Row, since: datetime, until: datetime, now: datetime) -> EventResult:
        terms = queries.event_terms(conn, event)
        needles = queries.match_terms(terms)
        tags = list(config.MASTODON_TAGS.get(event["hazard_type"]) or [])
        if not needles or not tags:
            return EventResult(query=None, skipped="no place or storm name to query with")
        result = EventResult(query=" ".join(f"#{tag}" for tag in tags))
        fetched_at = to_iso(now)
        statuses = [status for tag in tags for status in self.timeline(tag)]
        seen: set[str] = set()
        with conn:
            for status in statuses:
                stored = _store(conn, event["event_id"], status, needles, since, fetched_at)
                if stored is None:
                    continue
                result.items_seen += 1
                if stored.document_id in seen:
                    continue
                seen.add(stored.document_id)
                result.documents_seen += 1
                result.documents_new += 1 if stored.created else 0
        return result


def _store(conn, event_id: str, status: dict, needles: list[str], since: datetime, fetched_at: str):
    url = (status.get("url") or "").strip()
    text = strip_html(status.get("content"))
    published = normalise_iso(status.get("created_at"))
    if not url or not queries.mentions(text, needles):
        return None
    if published and parse_iso(published) < since:
        return None
    acct = ((status.get("account") or {}).get("acct") or "").strip() or None
    image = preview_of(status)
    payload = {
        "id": status.get("id"),
        "url": url,
        "acct": acct,
        "created_at": status.get("created_at"),
        "preview_url": image,
    }
    try:
        return store_document(
            conn,
            event_id=event_id,
            source_id=SOURCE_ID,
            kind="post",
            url=url,
            external_id=str(status.get("id") or "") or None,
            title=None,
            text_excerpt=text,
            language=(status.get("language") or None),
            author=acct,
            publisher=acct,
            published_at=published,
            media_url=image,
            media_kind="image" if image else "none",
            payload=payload,
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        log.debug("mastodon status skipped url=%r error=%s", url, exc)
        return None
