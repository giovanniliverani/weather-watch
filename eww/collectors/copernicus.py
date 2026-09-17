"""Copernicus Emergency Management Service (CEMS) Rapid Mapping activations.

Raw items are the objects of GET public-activations-info/ exactly as returned: code, name,
category, countries[], centroid ("POINT (lon lat)"), eventTime, activationTime, lastUpdate,
closed, gdacsId, n_aois, n_products. The endpoint has no date filter, so `fetch` pages through
the whole public list (3 requests of 100 on 2026-09-17) and keeps the activations whose event or
activation time falls in the window, plus every open one.

Identity: external_id = code (EMSR932), no episodes. `gdacsId` ("FL1104124") is the deterministic
cross-source key: `linked_ids` turns it into ("gdacs", "1104124") and resolve attaches the record
to the event holding that GDACS eventid before any scoring. Activations in
config.COPERNICUS_SKIP_CATEGORIES (public events, accidents) yield no source_record.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx

from eww import config, countries, geo
from eww import http as http_mod
from eww import severity as severity_mod
from eww.clock import normalise_iso, parse_iso
from eww.collectors.base import FetchResult

log = logging.getLogger(__name__)

SOURCE_ID = "copernicus"
LINK_TARGETS = ("gdacs",)  # sources this feed points at through linked_ids()
FOOTPRINT_PRECISION = "exact"
_GDACS_ID_RE = re.compile(r"(\d+)\s*$")


# ----------------------------------------------------------------------------- fetch
def fetch(since: datetime, until: datetime, *, http: httpx.Client | None = None) -> FetchResult:
    own_client = http is None
    http = http or http_mod.client()
    result = FetchResult(items=[])
    seen: dict[str, dict] = {}
    offset = 0
    try:
        for _ in range(config.COPERNICUS_MAX_PAGES):
            params = {"limit": config.COPERNICUS_PAGE_SIZE, "offset": offset}
            status, body = http_mod.get_json(http, config.COPERNICUS_ACTIVATIONS_URL, params)
            page = (body or {}).get("results") or []
            result.requests.append({"url": config.COPERNICUS_ACTIVATIONS_URL, "params": params, "status": status, "items": len(page)})
            result.http_status = status
            for item in page:
                if in_window(item, since, until):
                    seen[str(item.get("code"))] = item
            offset += len(page)
            if not page or not (body or {}).get("next"):
                break
    finally:
        if own_client:
            http.close()
    result.items = sorted(seen.values(), key=lambda item: str(item.get("code")))
    return result


def in_window(item: dict, since: datetime, until: datetime) -> bool:
    """Inside [since, until] by activation or event time, or still open."""
    if item.get("closed") is False:
        return True
    for key in ("activationTime", "eventTime"):
        stamp = normalise_iso(item.get(key))
        if stamp and since <= parse_iso(stamp) <= until:
            return True
    return False


# ----------------------------------------------------------------------------- normalise
def iter_records(item: dict) -> list[dict]:
    if str(item.get("category") or "") in config.COPERNICUS_SKIP_CATEGORIES:
        return []
    return [normalise(item)]


def hazard_for(item: dict) -> str:
    category = str(item.get("category") or "")
    hazard = config.COPERNICUS_HAZARD.get(category)
    if hazard is None:
        log.warning("copernicus unknown category=%r code=%s; recorded as 'other'", category, item.get("code"))
        return "other"
    if hazard == "severe_storm" and config.STORM_TITLE_RE.search(str(item.get("name") or "")):
        hazard = "tropical_cyclone"
    return hazard


def normalise(item: dict) -> dict:
    """Map one activation to the source_record columns (payload stays a dict; ingest serialises it).

    observed_at is the activation time (when EMS took the event on); lastUpdate only changes the
    payload, so a new map product does not pull an old activation into the recent window.
    ended_at is lastUpdate once the activation is closed: the closest the feed gets to a closing time.
    """
    code = str(item["code"])
    point = geo.wkt_point(item.get("centroid"))
    lon, lat = point if point else (None, None)
    activation = normalise_iso(item.get("activationTime"))
    event_time = normalise_iso(item.get("eventTime"))
    last_update = normalise_iso(item.get("lastUpdate"))
    started = event_time or activation
    ended = None
    if item.get("closed") is True and last_update:
        ended = max(last_update, started) if started else last_update
    return {
        "source_id": SOURCE_ID,
        "external_id": code,
        "external_episode": "",
        "hazard_type": hazard_for(item),
        "title": (item.get("name") or "").strip().rstrip(".") or code,
        "observed_at": activation or last_update or event_time,
        "started_at": started,
        "ended_at": ended,
        "lat": lat,
        "lon": lon,
        "severity_raw": json.dumps(
            {"category": item.get("category"), "closed": item.get("closed"), "n_aois": item.get("n_aois"), "n_products": item.get("n_products")},
            ensure_ascii=False,
            sort_keys=True,
        ),
        "glide_number": None,
        "payload": item,
    }


# ----------------------------------------------------------------------------- derived, from a stored payload
def severity(payload: dict) -> tuple[str | None, float | None]:
    """(None, None): an activation has no scale; eww.severity.normalise() applies the EMS floor per event."""
    return severity_mod.copernicus_severity(payload)


def country_iso3(payload: dict) -> str | None:
    for name in payload.get("countries") or []:
        iso3 = countries.iso3_for_name(str(name))
        if iso3:
            return iso3
    return None


def detail_url(payload: dict) -> str | None:
    code = payload.get("code")
    return config.COPERNICUS_ACTIVATION_URL.format(code=code) if code else None


def footprint(payload: dict) -> dict | None:
    """The public list carries centroids only; AOI polygons would need one request per activation."""
    return None


def linked_ids(payload: dict) -> list[tuple[str, str]]:
    """[('gdacs', '1104124')] from gdacsId 'FL1104124'; empty when the activation names no GDACS event."""
    match = _GDACS_ID_RE.search(str(payload.get("gdacsId") or ""))
    return [("gdacs", match.group(1))] if match else []


def storm_name(payload: dict) -> str | None:
    """'Tropical Cyclone GEZANI-26 in Madagascar' -> 'Tropical Cyclone GEZANI-26'; None unless the name is a storm."""
    name = str(payload.get("name") or "")
    if not config.STORM_TITLE_RE.search(name):
        return None
    return name.split(" in ")[0].strip() or None


def attribution(payload: dict) -> str:
    """'Copernicus Emergency Management Service (© 2026 European Union), EMSR927'."""
    activation = normalise_iso(payload.get("activationTime")) or normalise_iso(payload.get("eventTime")) or ""
    year = activation[:4] or datetime.now().strftime("%Y")
    return config.COPERNICUS_ATTRIBUTION.format(year=year, code=payload.get("code", ""))
