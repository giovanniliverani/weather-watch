"""Reddit collector, off until EWW_REDDIT_ENABLED. An OAuth script app, nothing else.

When the flag is on: POST https://www.reddit.com/api/v1/access_token, then
GET https://oauth.reddit.com/search?q=<query>&sort=new&t=week&limit=50&type=link.
Stored fields are the permalink, title, subreddit, created_utc and the preview image URL.
The User-Agent is the form Reddit requires. At most 60 requests a minute.

Compliance: every sync (which is inside the 48-hour rule), GET /api/info for stored t3_ ids,
100 at a time. An id that is missing or marked removed gets removed_at and leaves the sidebar.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from html import unescape

import httpx

from eww import config, documents
from eww import http as http_mod
from eww import ratelimit
from eww.clock import to_iso
from eww.enrich import EventResult, queries
from eww.enrich.errors import SkipSource
from eww.enrich.store import store_document

log = logging.getLogger(__name__)

SOURCE_ID = "reddit"
MAX_EVENTS_PER_RUN = 0
_TOKEN = "https://www.reddit.com/api/v1/access_token"
_SEARCH = "https://oauth.reddit.com/search"
_INFO = "https://oauth.reddit.com/api/info"


def user_agent() -> str:
    name = config.REDDIT_USERNAME or "unknown"
    return f"windows:eww:v{config.VERSION} (by /u/{name}) (+{config.CONTACT})"


def available(conn: sqlite3.Connection) -> tuple[bool, str]:
    if not config.REDDIT_ENABLED:
        return False, "EWW_REDDIT_ENABLED is not set; skipped until Reddit approves the request."
    missing = [
        name
        for name, value in (
            ("REDDIT_CLIENT_ID", config.REDDIT_CLIENT_ID),
            ("REDDIT_CLIENT_SECRET", config.REDDIT_CLIENT_SECRET),
            ("REDDIT_USERNAME", config.REDDIT_USERNAME),
            ("REDDIT_PASSWORD", config.REDDIT_PASSWORD),
        )
        if not value
    ]
    if missing:
        return False, f"Reddit is enabled but {', '.join(missing)} is not set; skipped."
    return True, ""


def collector(http=None, *, sleep=None) -> "RedditCollector":
    return RedditCollector(http=http, sleep=sleep)


def _preview(data: dict) -> str | None:
    images = ((data.get("preview") or {}).get("images")) or []
    if not images or not isinstance(images[0], dict):
        return None
    url = ((images[0].get("source") or {}).get("url")) or ""
    url = unescape(str(url))
    return url if url.startswith("http") else None


def _removed(data: dict | None) -> bool:
    if not data:
        return True
    if data.get("removed_by_category"):
        return True
    if data.get("banned_by"):
        return True
    return False


class RedditCollector:
    def __init__(self, http=None, *, sleep=None):
        kwargs = {"sleep": sleep} if sleep else {}
        interval = ratelimit.MinInterval(config.REDDIT_MIN_INTERVAL_S, **kwargs)
        window = ratelimit.SlidingWindow(config.REDDIT_PER_MINUTE, 60, name=SOURCE_ID, **kwargs)
        window.preload(ratelimit.recent_call_ages(SOURCE_ID, 60))
        own = http is None
        http = http or httpx.Client(
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
            timeout=config.HTTP_TIMEOUT_S,
            follow_redirects=True,
            verify=config.verify_arg(),
        )
        http.headers["User-Agent"] = user_agent()
        self.provider = http_mod.Provider(SOURCE_ID, limiters=[interval, window], http=http, timeout=config.HTTP_TIMEOUT_S, retries=1, **kwargs)
        self._own_http = own
        self._token: str | None = None
        self.blocked: str | None = None

    def close(self) -> None:
        if self._own_http:
            self.provider.http.close()

    def ensure_token(self) -> None:
        if self.blocked:
            raise SkipSource(self.blocked)
        if self._token:
            return
        try:
            status, body = self.provider.post_form(
                _TOKEN,
                {
                    "grant_type": "password",
                    "username": config.REDDIT_USERNAME,
                    "password": config.REDDIT_PASSWORD,
                },
                auth=httpx.BasicAuth(config.REDDIT_CLIENT_ID or "", config.REDDIT_CLIENT_SECRET or ""),
            )
        except Exception:
            self.blocked = "Reddit token request failed; skipped."
            raise SkipSource(self.blocked) from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if status != 200 or not token:
            self.blocked = "Reddit token request was refused; skipped."
            raise SkipSource(self.blocked)
        self._token = token
        self.provider.http.headers["Authorization"] = f"Bearer {self._token}"

    def enrich_event(self, conn: sqlite3.Connection, event: sqlite3.Row, since: datetime, until: datetime, now: datetime) -> EventResult:
        query = queries.social_query(queries.event_terms(conn, event), "en")
        if not query:
            return EventResult(query=None, skipped="no place or storm name to query with")
        self.ensure_token()
        status, body = self.provider.get_json(
            _SEARCH,
            {"q": query, "sort": "new", "t": "week", "limit": config.REDDIT_LIMIT, "type": "link"},
        )
        if status in {401, 403}:
            self.blocked = "Reddit search was refused; skipped."
            raise SkipSource(self.blocked)
        if status != 200 or not isinstance(body, dict):
            raise ValueError(f"reddit search status {status}")
        children = ((body.get("data") or {}).get("children")) or []
        result = EventResult(query=query, items_seen=len(children))
        fetched_at = to_iso(now)
        with conn:
            for child in children:
                stored = _store(conn, event["event_id"], (child or {}).get("data") or {}, fetched_at)
                if stored is None:
                    continue
                result.documents_seen += 1
                result.documents_new += 1 if stored.created else 0
        return result

    def sweep(self, conn: sqlite3.Connection, now: datetime) -> int:
        if self.blocked:
            return 0
        try:
            self.ensure_token()
        except SkipSource:
            return 0
        rows = conn.execute(
            """
            SELECT document_id, external_id FROM document
            WHERE source_id = ? AND removed_at IS NULL AND external_id LIKE 't3_%'
            ORDER BY document_id
            """,
            (SOURCE_ID,),
        ).fetchall()
        removed = 0
        when = to_iso(now)
        for start in range(0, len(rows), config.REDDIT_INFO_BATCH):
            batch = rows[start : start + config.REDDIT_INFO_BATCH]
            status, body = self.provider.get_json(_INFO, {"id": ",".join(row["external_id"] for row in batch)})
            if status != 200 or not isinstance(body, dict):
                log.warning("reddit info status=%s; this batch was left unchanged", status)
                continue
            found = {}
            for child in ((body.get("data") or {}).get("children")) or []:
                data = (child or {}).get("data") or {}
                name = data.get("name")
                if name:
                    found[name] = data
            missing = [row["document_id"] for row in batch if _removed(found.get(row["external_id"]))]
            with conn:
                removed += documents.set_removed(conn, missing, when)
        if removed:
            log.info("reddit compliance removed=%d", removed)
        return removed


def _store(conn, event_id: str, data: dict, fetched_at: str):
    permalink = (data.get("permalink") or "").strip()
    if not permalink:
        return None
    if permalink.startswith("http"):
        url = permalink
    else:
        url = "https://www.reddit.com" + (permalink if permalink.startswith("/") else "/" + permalink)
    fullname = (data.get("name") or "").strip()
    if not fullname and data.get("id"):
        fullname = f"t3_{data['id']}"
    created = data.get("created_utc")
    published = None
    if isinstance(created, (int, float)):
        published = to_iso(datetime.fromtimestamp(created, timezone.utc))
    image = _preview(data)
    subreddit = (data.get("subreddit") or "").strip() or None
    # permalink, title, subreddit, created_utc, preview: nothing else (no selftext, no author name)
    payload = {
        "name": fullname or None,
        "title": data.get("title"),
        "subreddit": subreddit,
        "created_utc": created,
        "permalink": permalink,
        "preview": image,
    }
    try:
        return store_document(
            conn,
            event_id=event_id,
            source_id=SOURCE_ID,
            kind="post",
            url=url,
            external_id=fullname or None,
            title=(data.get("title") or "").strip() or None,
            text_excerpt=None,
            author=None,
            publisher=subreddit,
            published_at=published,
            media_url=image,
            media_kind="image" if image else "none",
            payload=payload,
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        log.debug("reddit post skipped url=%r error=%s", url, exc)
        return None
