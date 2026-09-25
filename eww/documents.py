"""Document rows: canonical URLs, idempotent inserts, retrieval provenance and the 60-day purge (M3).

A document is a link plus metadata: never an article body (text_excerpt is at most
config.EXCERPT_MAX_CHARS), never media bytes (media_url is a reference). `UNIQUE (source_id,
url_canonical)` makes every re-run a no-op: `upsert_document()` inserts a new URL and only refreshes the
metadata of a known one. `record_retrieval()` remembers which per-event query fetched a document, which
is the evidence behind event_document.method = 'query' and the query prior of attach_document().
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import re

from eww import config
from eww.clock import now_iso, now_utc, to_iso
from eww.ids import new_id

log = logging.getLogger(__name__)

KINDS = ("article", "report", "post", "video")
_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}(?::\d{1,5})?$")


def valid_host(host: str | None) -> bool:
    """A dotted host name with a top-level label (no spaces, no bare words)."""
    return bool(host) and bool(_HOST_RE.match(host))


# ----------------------------------------------------------------------------- canonical URLs
def canonical_url(url: str) -> str:
    """https, lower-case host without a default port, tracking parameters stripped, no fragment.

    The path is kept as it is (paths are case-sensitive), the remaining query parameters are re-emitted in
    their original order, and a trailing '?' disappears with the last parameter.
    """
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    query = urlencode(kept, doseq=True) if kept else ""
    path = parts.path or "/"
    return urlunsplit(("https", host, path, query, ""))


def _is_tracking(key: str) -> bool:
    lower = key.lower()
    return lower in config.TRACKING_PARAMS or lower.startswith(config.TRACKING_PARAM_PREFIXES)


def domain_of(url: str) -> str | None:
    host = urlsplit(url if "://" in url else "https://" + url).hostname
    return host.lower() if host else None


def excerpt(text: str | None, limit: int | None = None) -> str | None:
    """Whitespace-collapsed text cut to the excerpt limit; None for nothing."""
    if not text:
        return None
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return None
    return collapsed[: (limit or config.EXCERPT_MAX_CHARS)]


# ----------------------------------------------------------------------------- rows
@dataclass
class UpsertResult:
    document_id: str
    created: bool


def upsert_document(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    kind: str,
    url: str,
    title: str | None,
    text_excerpt: str | None = None,
    language: str | None = None,
    author: str | None = None,
    publisher: str | None = None,
    published_at: str | None = None,
    media_url: str | None = None,
    media_kind: str | None = None,
    payload: dict | None = None,
    external_id: str | None = None,
    fetched_at: str | None = None,
) -> UpsertResult:
    """Insert the document, or refresh the metadata of the row that already holds its canonical URL."""
    if kind not in KINDS:
        raise ValueError(f"unknown document kind {kind!r}")
    canonical = canonical_url(url)
    if not canonical or not valid_host(urlsplit(canonical).netloc):
        raise ValueError(f"not a URL: {url!r}")
    values = {
        "source_id": source_id,
        "kind": kind,
        "url": url.strip(),
        "url_canonical": canonical,
        "external_id": external_id,
        "title": (title or "").strip() or None,
        "text_excerpt": excerpt(text_excerpt),
        "language": (language or "").strip() or None,
        "author": (author or "").strip() or None,
        "publisher": (publisher or domain_of(canonical) or "").strip().lower() or None,
        "published_at": published_at,
        "fetched_at": fetched_at or now_iso(),
        "media_url": (media_url or "").strip() or None,
        "media_kind": media_kind or ("image" if media_url else "none"),
        "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else None,
    }
    row = conn.execute("SELECT document_id FROM document WHERE source_id = ? AND url_canonical = ?", (source_id, canonical)).fetchone()
    if row is not None:
        conn.execute(
            """
            UPDATE document SET title = COALESCE(:title, title), text_excerpt = COALESCE(:text_excerpt, text_excerpt),
                language = COALESCE(:language, language), author = COALESCE(:author, author),
                publisher = COALESCE(:publisher, publisher), published_at = COALESCE(:published_at, published_at),
                media_url = COALESCE(:media_url, media_url),
                media_kind = CASE WHEN :media_url IS NOT NULL THEN :media_kind ELSE media_kind END,
                external_id = COALESCE(:external_id, external_id), payload = COALESCE(:payload, payload)
            WHERE document_id = :document_id
            """,
            {**values, "document_id": row["document_id"]},
        )
        return UpsertResult(row["document_id"], False)
    document_id = new_id()
    columns = ["document_id", *values]
    conn.execute(
        f"INSERT INTO document ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})",
        {"document_id": document_id, **values},
    )
    return UpsertResult(document_id, True)


def record_retrieval(conn: sqlite3.Connection, document_id: str, event_id: str, source_id: str, retrieved_at: str | None = None) -> bool:
    """Remember that the query built for `event_id` returned this document. True when the row is new."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO document_retrieval (document_id, event_id, source_id, retrieved_at) VALUES (?, ?, ?, ?)",
        (document_id, event_id, source_id, retrieved_at or now_iso()),
    )
    return cursor.rowcount > 0


def retrieval_events(conn: sqlite3.Connection, document_id: str) -> list[str]:
    return [row[0] for row in conn.execute("SELECT event_id FROM document_retrieval WHERE document_id = ? ORDER BY retrieved_at, event_id", (document_id,))]


# ----------------------------------------------------------------------------- housekeeping
@dataclass
class PurgeStats:
    cutoff: str
    documents: int = 0
    extractions: int = 0
    embeddings: int = 0
    retrievals: int = 0
    mentions: int = 0
    rejected_links: int = 0


def purge_unattached(conn: sqlite3.Connection, days: int | None = None, *, now: datetime | None = None, dry_run: bool = False) -> PurgeStats:
    """Delete documents older than `days` (published, else fetched) that no event holds as attached or candidate.

    Their extraction, embedding, retrieval and mention rows go with them, as do 'rejected' links. Human
    decisions are decisions: an attached or candidate row of any origin keeps the document.
    """
    days = config.PURGE_UNATTACHED_DAYS if days is None else days
    cutoff = to_iso((now or now_utc()) - timedelta(days=days))
    stats = PurgeStats(cutoff=cutoff)
    ids = [
        row[0]
        for row in conn.execute(
            """
            SELECT d.document_id FROM document d
            WHERE COALESCE(d.published_at, d.fetched_at) < ?
              AND NOT EXISTS (SELECT 1 FROM event_document ed WHERE ed.document_id = d.document_id AND ed.status IN ('attached', 'candidate'))
            ORDER BY d.document_id
            """,
            (cutoff,),
        )
    ]
    stats.documents = len(ids)
    if not ids or dry_run:
        for table, attr in (("document_extraction", "extractions"), ("document_embedding", "embeddings"), ("document_retrieval", "retrievals")):
            setattr(stats, attr, _count_in(conn, table, "document_id", ids))
        stats.mentions = _count_in(conn, "event_geometry", "document_id", ids, extra="AND role = 'mention'")
        stats.rejected_links = _count_in(conn, "event_document", "document_id", ids)
        return stats
    with conn:
        for chunk in _chunks(ids):
            marks = ", ".join("?" * len(chunk))
            stats.mentions += conn.execute(f"DELETE FROM event_geometry WHERE role = 'mention' AND document_id IN ({marks})", chunk).rowcount
            stats.rejected_links += conn.execute(f"DELETE FROM event_document WHERE document_id IN ({marks})", chunk).rowcount
            stats.extractions += conn.execute(f"DELETE FROM document_extraction WHERE document_id IN ({marks})", chunk).rowcount
            stats.embeddings += conn.execute(f"DELETE FROM document_embedding WHERE document_id IN ({marks})", chunk).rowcount
            stats.retrievals += conn.execute(f"DELETE FROM document_retrieval WHERE document_id IN ({marks})", chunk).rowcount
            conn.execute(f"DELETE FROM llm_call WHERE document_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM document WHERE document_id IN ({marks})", chunk)
    log.info("purge cutoff=%s documents=%d extractions=%d embeddings=%d retrievals=%d mentions=%d", cutoff, stats.documents, stats.extractions, stats.embeddings, stats.retrievals, stats.mentions)
    return stats


def _count_in(conn: sqlite3.Connection, table: str, column: str, ids: list[str], extra: str = "") -> int:
    total = 0
    for chunk in _chunks(ids):
        marks = ", ".join("?" * len(chunk))
        total += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({marks}) {extra}", chunk).fetchone()[0]
    return total


def _chunks(items: list, size: int = 400):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def stats(conn: sqlite3.Connection) -> dict:
    """Counts `eww doctor` prints: documents by source and kind, the longest excerpt, decisions by status."""
    by_source = {f"{row[0]}/{row[1]}": row[2] for row in conn.execute("SELECT source_id, kind, COUNT(*) FROM document GROUP BY 1, 2 ORDER BY 1, 2")}
    longest = conn.execute("SELECT COALESCE(MAX(length(text_excerpt)), 0) FROM document").fetchone()[0]
    decisions = {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM event_document GROUP BY 1")}
    by_actor = {row[0]: row[1] for row in conn.execute("SELECT decided_by, COUNT(*) FROM event_document GROUP BY 1")}
    return {
        "documents": conn.execute("SELECT COUNT(*) FROM document").fetchone()[0],
        "by_source_kind": by_source,
        "longest_excerpt": longest,
        "with_media": conn.execute("SELECT COUNT(*) FROM document WHERE media_url IS NOT NULL").fetchone()[0],
        "extracted": conn.execute("SELECT COUNT(*) FROM document_extraction").fetchone()[0],
        "classified": conn.execute("SELECT COUNT(*) FROM document_extraction WHERE hazard_type IS NOT NULL").fetchone()[0],
        "located": conn.execute("SELECT COUNT(*) FROM document_extraction WHERE places <> '[]'").fetchone()[0],
        "embedded": conn.execute("SELECT COUNT(*) FROM document_embedding").fetchone()[0],
        "retrievals": conn.execute("SELECT COUNT(*) FROM document_retrieval").fetchone()[0],
        "decisions": decisions,
        "decided_by": by_actor,
        "mentions": conn.execute("SELECT COUNT(*) FROM event_geometry WHERE role = 'mention'").fetchone()[0],
        "unattached": conn.execute(
            "SELECT COUNT(*) FROM document d WHERE NOT EXISTS (SELECT 1 FROM event_document ed WHERE ed.document_id = d.document_id AND ed.status IN ('attached', 'candidate'))"
        ).fetchone()[0],
    }
