"""GDACS collector: the JSON SEARCH API, with the RSS feed as a fallback.

Raw items are GeoJSON Features exactly as the API returns them (properties: eventtype, eventid,
episodeid, eventname, glide, alertlevel, alertscore, fromdate, todate, datemodified, country,
iso3, severitydata, url, iscurrent, ...). RSS items are converted to the same Feature shape,
marked with properties.source_format = "rss", so `normalise` has one input format.

Identity: external_id = eventid, external_episode = episodeid. The API returns one Feature per
event carrying its current episode, so a new episode of a running cyclone arrives as a new
source_record under the same external_id and `resolve` attaches it to the existing event.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from eww import config, geo
from eww import http as http_mod
from eww import severity as severity_mod
from eww.collectors.base import FetchResult
from eww.clock import normalise_iso, rfc2822_to_iso

log = logging.getLogger(__name__)

SOURCE_ID = "gdacs"
LINK_TARGETS: tuple[str, ...] = ()  # GDACS points at no other feed; EONET and Copernicus point at it
FOOTPRINT_PRECISION = "admin1"  # a bounding box is an area, not a surveyed outline
GDACS_NS = "http://www.gdacs.org"
GEORSS_NS = "http://www.georss.org/georss"


# ----------------------------------------------------------------------------- fetch
def fetch(since: datetime, until: datetime, *, event_types: list[str] | None = None, http: httpx.Client | None = None):
    """Page the SEARCH API one event type at a time; fall back to RSS for any type that failed."""
    own_client = http is None
    http = http or http_mod.client()
    result = FetchResult(items=[])
    seen: dict[tuple[str, str], dict] = {}
    failures: list[str] = []
    try:
        for event_type in event_types or config.GDACS_EVENT_TYPES:
            page = 1
            while True:
                params = {
                    "eventlist": event_type,
                    "alertlevel": ";".join(config.GDACS_ALERT_LEVELS),
                    "fromDate": since.date().isoformat(),
                    "toDate": until.date().isoformat(),
                    "pageSize": config.GDACS_PAGE_SIZE,
                    "pageNumber": page,
                }
                try:
                    status, body = http_mod.get_json(http, config.GDACS_SEARCH_URL, params)
                except httpx.HTTPError as exc:
                    failures.append(f"{event_type} page {page}: {exc}")
                    log.warning("gdacs fetch failed type=%s page=%d error=%s", event_type, page, exc)
                    break
                features = (body or {}).get("features") or []
                result.requests.append(
                    {"url": config.GDACS_SEARCH_URL, "params": params, "status": status, "items": len(features)}
                )
                if status != 204 or result.http_status is None:  # 204 only marks the end of paging
                    result.http_status = status
                if not features:
                    break
                for feature in features:
                    props = feature.get("properties") or {}
                    seen[(str(props.get("eventid")), str(props.get("episodeid") or ""))] = feature
                if len(features) < config.GDACS_PAGE_SIZE:
                    break
                page += 1
        if failures:
            result.status = "partial"
            result.error = "; ".join(failures)
            try:
                rss_items, rss_request = fetch_rss(http)
                result.requests.append(rss_request)
                for feature in rss_items:
                    props = feature["properties"]
                    seen.setdefault((str(props.get("eventid")), str(props.get("episodeid") or "")), feature)
            except (httpx.HTTPError, ET.ParseError) as exc:
                result.error += f"; rss fallback failed: {exc}"
                if not seen:
                    raise
    finally:
        if own_client:
            http.close()
    result.items = sorted(seen.values(), key=lambda f: (str(f["properties"].get("eventtype")), _sort_key(f)))
    return result


def _sort_key(feature: dict) -> tuple[int, int]:
    props = feature.get("properties") or {}
    return (_int(props.get("eventid")), _int(props.get("episodeid")))


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def fetch_rss(http: httpx.Client) -> tuple[list[dict], dict]:
    """The RSS feed lists current events only; each <item> becomes a Feature-shaped dict."""
    response = http_mod.get(http, config.GDACS_RSS_URL)
    root = ET.fromstring(response.content)
    items = [rss_item_to_feature(item) for item in root.findall("./channel/item")]
    request = {"url": config.GDACS_RSS_URL, "params": {}, "status": response.status_code, "items": len(items)}
    return items, request


def rss_item_to_feature(item: ET.Element) -> dict:
    def g(tag: str) -> str:
        return (item.findtext(f"{{{GDACS_NS}}}{tag}") or "").strip()

    geometry = None
    bbox = None
    point = (item.findtext(f"{{{GEORSS_NS}}}point") or "").split()
    if len(point) == 2:
        lat, lon = float(point[0]), float(point[1])
        geometry = {"type": "Point", "coordinates": [lon, lat]}
        bbox = [lon, lat, lon, lat]
    severity = item.find(f"{{{GDACS_NS}}}severity")
    severity_value = severity.get("value") if severity is not None else None
    props = {
        "eventtype": g("eventtype"),
        "eventid": _int(g("eventid")),
        "episodeid": _int(g("episodeid")),
        "eventname": g("eventname"),
        "glide": g("glide"),
        "name": (item.findtext("title") or "").strip(),
        "description": (item.findtext("description") or "").strip(),
        "alertlevel": g("alertlevel"),
        "alertscore": float(g("alertscore") or 0),
        "episodealertlevel": g("episodealertlevel"),
        "episodealertscore": float(g("episodealertscore") or 0),
        "iscurrent": g("iscurrent"),
        "country": g("country"),
        "iso3": g("iso3"),
        "fromdate": rfc2822_to_iso(g("fromdate")),
        "todate": rfc2822_to_iso(g("todate")),
        "datemodified": rfc2822_to_iso(g("datemodified")),
        "severitydata": {
            "severity": float(severity_value) if severity_value not in (None, "") else None,
            "severitytext": (severity.text or "").strip() if severity is not None else "",
            "severityunit": severity.get("unit", "") if severity is not None else "",
        },
        "url": {"report": (item.findtext("link") or "").strip(), "details": None, "geometry": None},
        "source_format": "rss",
    }
    return {"type": "Feature", "geometry": geometry, "bbox": bbox, "properties": props}


# ----------------------------------------------------------------------------- normalise
def iter_records(item: dict) -> list[dict]:
    return [normalise(item)]


def normalise(item: dict) -> dict:
    """Map one GDACS Feature to the source_record columns (payload stays a dict; ingest serialises it)."""
    props = item.get("properties") or {}
    lon, lat = _point(item)
    event_type = str(props.get("eventtype") or "").upper()
    hazard = config.GDACS_HAZARD.get(event_type, "other")
    event_id = str(props.get("eventid"))
    is_current = str(props.get("iscurrent")).strip().lower() == "true"
    from_date = normalise_iso(props.get("fromdate"))
    to_date = normalise_iso(props.get("todate"))
    modified = normalise_iso(props.get("datemodified"))
    title = (props.get("name") or props.get("description") or "").strip() or f"{hazard.replace('_', ' ').title()} {event_id}"
    severity_raw = {
        "alertlevel": props.get("alertlevel"),
        "alertscore": props.get("alertscore"),
        "episodealertlevel": props.get("episodealertlevel"),
        "episodealertscore": props.get("episodealertscore"),
        "severitydata": props.get("severitydata"),
    }
    return {
        "source_id": SOURCE_ID,
        "external_id": event_id,
        "external_episode": str(props.get("episodeid") or ""),
        "hazard_type": hazard,
        "title": title,
        "observed_at": modified or to_date or from_date,
        "started_at": from_date,
        "ended_at": None if is_current else to_date,
        "lat": lat,
        "lon": lon,
        "severity_raw": json.dumps(severity_raw, ensure_ascii=False, sort_keys=True),
        "glide_number": (props.get("glide") or "").strip() or None,
        "payload": item,
    }


def _point(item: dict) -> tuple[float | None, float | None]:
    geometry = item.get("geometry") or {}
    if geometry.get("type") == "Point" and geometry.get("coordinates"):
        lon, lat = geometry["coordinates"][:2]
        return float(lon), float(lat)
    bbox = item.get("bbox")
    if bbox and len(bbox) == 4:
        return (float(bbox[0]) + float(bbox[2])) / 2, (float(bbox[1]) + float(bbox[3])) / 2
    return None, None


# ----------------------------------------------------------------------------- derived, from a stored payload
def severity(payload: dict) -> tuple[str | None, float | None]:
    """('Orange', 0.675): the alert level as GDACS says it, and its normalised score (eww.severity)."""
    return severity_mod.gdacs_severity(payload.get("properties") or {})


def linked_ids(payload: dict) -> list[tuple[str, str]]:
    """GDACS references no other feed."""
    return []


def storm_name(payload: dict) -> str | None:
    """The named storm ('NORBERT-26') for tropical cyclones; None for every other type or an unnamed one."""
    props = payload.get("properties") or {}
    if str(props.get("eventtype") or "").upper() != "TC":
        return None
    return (props.get("eventname") or "").strip() or None


def country_iso3(payload: dict) -> str | None:
    props = payload.get("properties") or {}
    iso3 = (props.get("iso3") or "").strip().upper()
    if iso3:
        return iso3
    for country in props.get("affectedcountries") or []:
        if country.get("iso3"):
            return str(country["iso3"]).strip().upper()
    return None


def detail_url(payload: dict) -> str | None:
    props = payload.get("properties") or {}
    url = (props.get("url") or {}).get("report")
    if url:
        return url
    if props.get("eventid") and props.get("eventtype"):
        return config.GDACS_REPORT_URL.format(eventtype=props["eventtype"], eventid=props["eventid"])
    return None


def footprint(payload: dict) -> dict | None:
    """The feature's bbox as a Polygon when it is a real box. Every bbox the SEARCH API returned on
    2026-09-17 (2,405 records) was a degenerate point, so in practice GDACS supplies no footprints;
    the outline behind `url.geometry` would cost one request per event and stays out of the spine."""
    return geo.bbox_polygon(payload.get("bbox"))
