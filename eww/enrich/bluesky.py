"""Bluesky collector: an authenticated searchPosts per active event, and a deletion sweep.

POST https://bsky.social/xrpc/com.atproto.server.createSession with the handle and app password
from .env, then GET app.bsky.feed.searchPosts for each event (place + hazard word, lang en and it,
since the last successful run). The public unauthenticated search returns 403, so this collector
always authenticates. Tokens stay in memory and are never logged.

Deletion: every sync, app.bsky.feed.getPosts for stored uris (25 at a time). A uri the server no
longer returns gets document.removed_at, and the sidebar stops showing it.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from eww import config, documents
from eww import http as http_mod
from eww import ratelimit
from eww.clock import normalise_iso, to_iso
from eww.enrich import EventResult, queries
from eww.enrich.errors import SkipSource
from eww.enrich.store import store_document

log = logging.getLogger(__name__)

SOURCE_ID = "bluesky"
MAX_EVENTS_PER_RUN = 0  # every event active in the last ENRICH_ACTIVE_DAYS; spacing is 1 request/second
_CREATE = f"{config.BLUESKY_PDS}/xrpc/com.atproto.server.createSession"
_REFRESH = f"{config.BLUESKY_PDS}/xrpc/com.atproto.server.refreshSession"
_SEARCH = f"{config.BLUESKY_PDS}/xrpc/app.bsky.feed.searchPosts"
_GET_POSTS = f"{config.BLUESKY_PDS}/xrpc/app.bsky.feed.getPosts"


def available(conn: sqlite3.Connection) -> tuple[bool, str]:
    if not config.BLUESKY_HANDLE or not config.BLUESKY_APP_PASSWORD:
        return False, "BLUESKY_HANDLE or BLUESKY_APP_PASSWORD is not set; skipped."
    return True, ""


def collector(http=None, *, sleep=None) -> "BlueskyCollector":
    return BlueskyCollector(http=http, sleep=sleep)


def web_url(uri: str, handle: str) -> str:
    rkey = uri.rstrip("/").rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def media_of(post: dict) -> str | None:
    """The first embed image fullsize URL, or the external-link thumbnail. Record embeds hold blobs, not URLs."""
    embed = post.get("embed") or {}
    media = embed.get("media") or {}
    for images in (embed.get("images"), media.get("images")):
        if images and isinstance(images, list) and isinstance(images[0], dict) and images[0].get("fullsize"):
            return images[0]["fullsize"]
    for external in (embed.get("external"), media.get("external")):
        thumb = external.get("thumb") if isinstance(external, dict) else None
        if isinstance(thumb, str) and thumb.startswith("http"):
            return thumb
    return None


class BlueskyCollector:
    def __init__(self, http=None, *, sleep=None):
        kwargs = {"sleep": sleep} if sleep else {}
        limiter = ratelimit.MinInterval(config.BLUESKY_MIN_INTERVAL_S, **kwargs)
        self.provider = http_mod.Provider(SOURCE_ID, limiters=[limiter], http=http, timeout=config.HTTP_TIMEOUT_S, retries=2, **kwargs)
        self._access: str | None = None
        self._refresh: str | None = None
        self.blocked: str | None = None

    def close(self) -> None:
        self.provider.close()

    def ensure_session(self) -> None:
        if self.blocked:
            raise SkipSource(self.blocked)
        if self._access:
            return
        try:
            status, body = self.provider.post_json(
                _CREATE, json={"identifier": config.BLUESKY_HANDLE, "password": config.BLUESKY_APP_PASSWORD}
            )
        except Exception:
            self.blocked = "Bluesky session failed; skipped."
            raise SkipSource(self.blocked) from None
        token = body.get("accessJwt") if isinstance(body, dict) else None
        refresh = body.get("refreshJwt") if isinstance(body, dict) else None
        if status != 200 or not token or not refresh:
            self.blocked = "Bluesky session was refused; skipped."
            raise SkipSource(self.blocked)
        self._access = token
        self._refresh = refresh
        self.provider.http.headers["Authorization"] = f"Bearer {self._access}"

    def _refresh_session(self) -> None:
        self.provider.http.headers["Authorization"] = f"Bearer {self._refresh}"
        try:
            response = self.provider.request("POST", _REFRESH)
            body = response.json() if response.content else {}
            status = response.status_code
        except Exception:
            self.blocked = "Bluesky session refresh failed; skipped."
            raise SkipSource(self.blocked) from None
        token = body.get("accessJwt") if isinstance(body, dict) else None
        if status != 200 or not token:
            self.blocked = "Bluesky session refresh was refused; skipped."
            raise SkipSource(self.blocked)
        self._access = token
        if isinstance(body, dict) and body.get("refreshJwt"):
            self._refresh = body["refreshJwt"]
        self.provider.http.headers["Authorization"] = f"Bearer {self._access}"

    def _get(self, url: str, params) -> tuple[int, dict]:
        self.ensure_session()
        status, body = self.provider.get_json(url, params)
        if status == 401:
            self._refresh_session()
            status, body = self.provider.get_json(url, params)
        if not isinstance(body, dict):
            body = {}
        return status, body

    def search(self, query: str, since: datetime, until: datetime, lang: str) -> list[dict]:
        status, body = self._get(
            _SEARCH,
            {
                "q": query,
                "sort": "latest",
                "limit": config.BLUESKY_SEARCH_LIMIT,
                "since": to_iso(since),
                "until": to_iso(until),
                "lang": lang,
            },
        )
        if status == 403:
            self.blocked = "Bluesky authenticated search returned 403; skipped."
            raise SkipSource(self.blocked)
        if status != 200:
            raise ValueError(f"bluesky search status {status}")
        return list(body.get("posts") or [])

    def enrich_event(self, conn: sqlite3.Connection, event: sqlite3.Row, since: datetime, until: datetime, now: datetime) -> EventResult:
        terms = queries.event_terms(conn, event)
        planned = [(lang, queries.social_query(terms, lang)) for lang in config.BLUESKY_LANGS]
        planned = [(lang, query) for lang, query in planned if query]
        if not planned:
            return EventResult(query=None, skipped="no place or storm name to query with")
        result = EventResult(query=" | ".join(query for _, query in planned))
        fetched_at = to_iso(now)
        found: list[tuple[str, dict]] = []
        for lang, query in planned:
            posts = self.search(query, since, until, lang)
            result.items_seen += len(posts)
            found.extend((lang, post) for post in posts)
        seen: set[str] = set()
        with conn:
            for lang, post in found:
                stored = _store(conn, event["event_id"], post, lang, fetched_at)
                if stored is None or stored.document_id in seen:
                    continue
                seen.add(stored.document_id)
                result.documents_seen += 1
                result.documents_new += 1 if stored.created else 0
        return result

    def sweep(self, conn: sqlite3.Connection, now: datetime) -> int:
        """Mark stored posts removed when getPosts no longer returns them. Runs on every sync."""
        if self.blocked:
            return 0
        try:
            self.ensure_session()
        except SkipSource:
            return 0
        rows = conn.execute(
            "SELECT document_id, external_id FROM document WHERE source_id = ? AND removed_at IS NULL AND external_id IS NOT NULL ORDER BY document_id",
            (SOURCE_ID,),
        ).fetchall()
        removed = 0
        when = to_iso(now)
        for start in range(0, len(rows), config.BLUESKY_GET_POSTS_BATCH):
            batch = rows[start : start + config.BLUESKY_GET_POSTS_BATCH]
            status, body = self._get(_GET_POSTS, [("uris", row["external_id"]) for row in batch])
            if status != 200:
                log.warning("bluesky getPosts status=%s; this batch was left unchanged", status)
                continue
            found = {post.get("uri") for post in body.get("posts") or []}
            missing = [row["document_id"] for row in batch if row["external_id"] not in found]
            with conn:
                removed += documents.set_removed(conn, missing, when)
        if removed:
            log.info("bluesky compliance removed=%d", removed)
        return removed


def _store(conn: sqlite3.Connection, event_id: str, post: dict, lang: str, fetched_at: str):
    uri = (post.get("uri") or "").strip()
    handle = ((post.get("author") or {}).get("handle") or "").strip()
    record = post.get("record") or {}
    if not uri or not handle or not uri.startswith("at://"):
        return None
    image = media_of(post)
    try:
        return store_document(
            conn,
            event_id=event_id,
            source_id=SOURCE_ID,
            kind="post",
            url=web_url(uri, handle),
            external_id=uri,
            title=None,
            text_excerpt=documents.excerpt((record.get("text") or ""), config.BLUESKY_EXCERPT_CHARS),
            language=lang,
            author=handle,
            publisher=handle,
            published_at=normalise_iso(record.get("createdAt")),
            media_url=image,
            media_kind="image" if image else "none",
            payload=post,
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        log.debug("bluesky post skipped uri=%s error=%s", uri, exc)
        return None
