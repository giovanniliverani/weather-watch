"""ReliefWeb reports collector (API v2). Skipped, with one warning, without an approved appname.

POST https://api.reliefweb.int/v2/reports?appname=<RELIEFWEB_APPNAME> with a JSON body filtering by
disaster.glide when the event carries a GLIDE number, else by country.iso3 plus the disaster type names
of config.RELIEFWEB_DISASTER_TYPES, and date.original since the last successful run. Fields: title, url,
date.original, source.shortname, disaster.glide, country.iso3, body (cut to config.EXCERPT_MAX_CHARS,
inside the stored payload too: never a full body). At most config.RELIEFWEB_DAILY_BUDGET calls a day
(the service allows 1,000). A 403 means the appname is not approved: one warning, then the run stops.
Terms: personal, non-commercial use (https://reliefweb.int/terms-conditions).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from eww import config, countries, documents, ratelimit
from eww import http as http_mod
from eww.clock import normalise_iso, to_iso
from eww.enrich import EventResult

log = logging.getLogger(__name__)

SOURCE_ID = "reliefweb"


def available(conn: sqlite3.Connection) -> tuple[bool, str]:
    if not config.RELIEFWEB_APPNAME:
        return False, "RELIEFWEB_APPNAME is not set (request an appname at https://apidoc.reliefweb.int/parameters#appname)"
    return True, ""


def collector(http=None, *, sleep=None) -> "ReliefWebCollector":
    return ReliefWebCollector(http=http, sleep=sleep)


def build_filter(event: sqlite3.Row, since: datetime) -> dict:
    conditions: list[dict] = []
    if event["glide_number"]:
        conditions.append({"field": "disaster.glide", "value": event["glide_number"]})
    else:
        if event["country_iso3"]:
            conditions.append({"field": "country.iso3", "value": event["country_iso3"].lower()})
        types = config.RELIEFWEB_DISASTER_TYPES.get(event["hazard_type"]) or []
        if types:
            conditions.append({"field": "disaster_type.name", "value": types, "operator": "OR"})
    conditions.append({"field": "date.original", "value": {"from": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}})
    return {"operator": "AND", "conditions": conditions}


def build_body(event: sqlite3.Row, since: datetime, *, limit: int | None = None, offset: int = 0) -> dict:
    return {
        "filter": build_filter(event, since),
        "fields": {"include": list(config.RELIEFWEB_FIELDS)},
        "limit": limit or config.RELIEFWEB_PAGE_LIMIT,
        "offset": offset,
        "sort": ["date.original:desc"],
    }


class ReliefWebCollector:
    def __init__(self, http=None, *, sleep=None):
        kwargs = {"sleep": sleep} if sleep else {}
        daily = ratelimit.SlidingWindow(config.RELIEFWEB_DAILY_LIMIT, 86400, budget=config.RELIEFWEB_DAILY_BUDGET, name=SOURCE_ID, **kwargs)
        daily.preload(ratelimit.recent_call_ages(SOURCE_ID, 86400))
        self.provider = http_mod.Provider(SOURCE_ID, limiters=[ratelimit.MinInterval(config.RELIEFWEB_MIN_INTERVAL_S, **kwargs), daily], http=http, retries=2, **kwargs)
        self.forbidden = False

    def close(self) -> None:
        self.provider.close()

    def fetch(self, event: sqlite3.Row, since: datetime) -> list[dict]:
        status, body = self.provider.post_json(config.RELIEFWEB_REPORTS_URL, params={"appname": config.RELIEFWEB_APPNAME}, json=build_body(event, since))
        if status == 403:
            self.forbidden = True
            raise ratelimit.RateLimitExceeded("reliefweb answered 403: the appname is not approved; skipping ReliefWeb for this run")
        if status >= 400:
            raise ValueError(f"reliefweb answered {status}: {str(body)[:200]}")
        return list((body or {}).get("data") or [])

    def enrich_event(self, conn: sqlite3.Connection, event: sqlite3.Row, since: datetime, until: datetime, now: datetime) -> EventResult:
        if not event["glide_number"] and not event["country_iso3"]:
            return EventResult(query=None, skipped="neither a GLIDE number nor a country to filter by")
        items = self.fetch(event, since)
        query = f"glide={event['glide_number']}" if event["glide_number"] else f"country={event['country_iso3']} types={','.join(config.RELIEFWEB_DISASTER_TYPES.get(event['hazard_type']) or [])}"
        result = EventResult(query=query, items_seen=len(items))
        fetched_at = to_iso(now)
        with conn:
            for item in items:
                fields = item.get("fields") or {}
                url = (fields.get("url") or "").strip()
                if not url:
                    continue
                body = documents.excerpt(fields.get("body"))
                trimmed = {**item, "fields": {**fields, "body": body}}  # never store a full body, not even raw
                sources = fields.get("source") or []
                publisher = (sources[0].get("shortname") if sources and isinstance(sources[0], dict) else None) or "reliefweb.int"
                languages = fields.get("language") or []
                language = languages[0].get("code") if languages and isinstance(languages[0], dict) else None
                upsert = documents.upsert_document(
                    conn,
                    source_id=SOURCE_ID,
                    kind="report",
                    url=url,
                    title=fields.get("title"),
                    text_excerpt=body,
                    language=language,
                    publisher=publisher,
                    published_at=normalise_iso((fields.get("date") or {}).get("original")),
                    media_url=None,
                    media_kind="none",
                    payload=trimmed,
                    external_id=str(item.get("id")) if item.get("id") is not None else None,
                    fetched_at=fetched_at,
                )
                result.documents_seen += 1
                result.documents_new += 1 if upsert.created else 0
                documents.record_retrieval(conn, upsert.document_id, event["event_id"], SOURCE_ID, fetched_at)
        return result


def country_of(item: dict) -> str | None:
    for country in (item.get("fields") or {}).get("country") or []:
        iso3 = (country or {}).get("iso3")
        if iso3 and countries.name_for(str(iso3).upper()):
            return str(iso3).upper()
    return None
