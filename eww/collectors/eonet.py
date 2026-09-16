"""NASA EONET v3 collector.

Raw items are EONET event objects exactly as returned: id, title, description, link, closed,
categories[], sources[]{id,url}, geometry[]{date,type,coordinates,magnitudeValue,magnitudeUnit}.
One event yields one source_record per geometry entry: external_id = id, external_episode =
geometry.date. Each record's payload is the event with `geometry` reduced to that one entry, so
a new geometry entry on a running storm does not rewrite the payload of its earlier records.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx

from eww import config, countries
from eww import http as http_mod
from eww.collectors.base import FetchResult
from eww.clock import normalise_iso

log = logging.getLogger(__name__)

SOURCE_ID = "eonet"


# ----------------------------------------------------------------------------- fetch
def fetch(since: datetime, until: datetime, *, http: httpx.Client | None = None):
    own_client = http is None
    http = http or http_mod.client()
    params = {
        "status": "all",
        "start": since.date().isoformat(),
        "end": until.date().isoformat(),
        "category": ",".join(config.EONET_CATEGORIES),
    }
    try:
        status, body = http_mod.get_json(http, config.EONET_EVENTS_URL, params)
    finally:
        if own_client:
            http.close()
    events = (body or {}).get("events") or []
    events = sorted(events, key=lambda e: str(e.get("id")))
    return FetchResult(
        items=events,
        requests=[{"url": config.EONET_EVENTS_URL, "params": params, "status": status, "items": len(events)}],
        http_status=status,
    )


# ----------------------------------------------------------------------------- normalise
def iter_records(item: dict) -> list[dict]:
    geometries = item.get("geometry") or []
    if not geometries:
        return [normalise(item)]
    return [normalise(item, g) for g in geometries]


def hazard_for(item: dict) -> str:
    categories = [c.get("id") for c in item.get("categories") or []]
    category = categories[0] if categories else None
    hazard = config.EONET_HAZARD.get(category or "", "other")
    title = item.get("title") or ""
    if category == "severeStorms" and config.EONET_STORM_TITLE_RE.search(title):
        hazard = "tropical_cyclone"
    if category == "tempExtremes" and config.EONET_COLD_TITLE_RE.search(title):
        hazard = "coldwave"
    return hazard


def normalise(item: dict, geometry: dict | None = None) -> dict:
    """Map one (event, geometry entry) pair to the source_record columns. Default: the latest entry."""
    geometries = item.get("geometry") or []
    if geometry is None:
        geometry = max(geometries, key=lambda g: str(g.get("date") or "")) if geometries else {}
    dates = sorted(normalise_iso(g.get("date")) for g in geometries if g.get("date"))
    observed = normalise_iso(geometry.get("date")) if geometry else None
    corrected = corrected_geometry(geometry) if geometry else None
    lon, lat = centroid(corrected) if corrected else (None, None)
    closed = normalise_iso(item.get("closed"))
    payload = {**item, "geometry": [geometry] if geometry else []}
    severity_raw = {
        "magnitudeValue": geometry.get("magnitudeValue") if geometry else None,
        "magnitudeUnit": geometry.get("magnitudeUnit") if geometry else None,
    }
    return {
        "source_id": SOURCE_ID,
        "external_id": str(item["id"]),
        "external_episode": observed or "",
        "hazard_type": hazard_for(item),
        "title": (item.get("title") or "").strip() or str(item["id"]),
        "observed_at": observed or closed or (dates[-1] if dates else None),
        "started_at": dates[0] if dates else observed,
        "ended_at": closed,
        "lat": lat,
        "lon": lon,
        "severity_raw": json.dumps(severity_raw, ensure_ascii=False, sort_keys=True),
        "glide_number": None,
        "payload": payload,
    }


def corrected_geometry(geometry: dict) -> dict | None:
    """Return a GeoJSON geometry in [lon, lat] order (EONET polygons arrive as [lat, lon])."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not gtype or coords is None:
        return None
    if gtype == "Polygon" and config.EONET_POLYGON_AXES_SWAPPED and _looks_lat_lon(coords):
        coords = [[[float(p[1]), float(p[0])] for p in ring] for ring in coords]
    return {"type": gtype, "coordinates": coords}


def _looks_lat_lon(rings: list) -> bool:
    """True when every vertex reads as a valid [lat, lon] pair; guards the swap against garbage."""
    try:
        return all(-90 <= float(p[0]) <= 90 and -180 <= float(p[1]) <= 180 for ring in rings for p in ring)
    except (TypeError, ValueError, IndexError):
        return False


def centroid(geometry: dict) -> tuple[float | None, float | None]:
    """(lon, lat) of a Point, or the area centroid of a Polygon's outer ring (vertex mean if degenerate)."""
    gtype, coords = geometry.get("type"), geometry.get("coordinates")
    if gtype == "Point" and coords:
        return float(coords[0]), float(coords[1])
    if gtype == "Polygon" and coords and coords[0]:
        ring = [(float(p[0]), float(p[1])) for p in coords[0]]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        area2 = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            cross = x0 * y1 - x1 * y0
            area2 += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if abs(area2) > 1e-12:
            return cx / (3 * area2), cy / (3 * area2)
        return sum(x for x, _ in ring) / len(ring), sum(y for _, y in ring) / len(ring)
    return None, None


# ----------------------------------------------------------------------------- derived, from a stored payload
def severity(payload: dict) -> tuple[str | None, float | None]:
    """EONET carries no alert level: the label is the magnitude when present ('5747 hectare'), score 0.4."""
    geometries = payload.get("geometry") or []
    geometry = geometries[0] if geometries else {}
    value, unit = geometry.get("magnitudeValue"), geometry.get("magnitudeUnit")
    label = None
    if value is not None:
        label = f"{float(value):g} {unit}".strip() if unit else f"{float(value):g}"
    return label, config.EONET_SEVERITY


def country_iso3(payload: dict) -> str | None:
    for source in payload.get("sources") or []:
        iso3 = config.EONET_SOURCE_COUNTRY.get(str(source.get("id")))
        if iso3:
            return iso3
    match = config.EONET_TITLE_COUNTRY_RE.match(payload.get("title") or "")
    if match:
        return countries.iso3_for_name(match.group(1))
    return None


def detail_url(payload: dict) -> str | None:
    return payload.get("link") or None


def footprint(payload: dict) -> dict | None:
    geometries = payload.get("geometry") or []
    if geometries and geometries[0].get("type") == "Polygon":
        return corrected_geometry(geometries[0])
    return None
