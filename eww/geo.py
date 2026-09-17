"""Small geometry helpers with no dependencies: great-circle distance, WKT points, bounding boxes."""

from __future__ import annotations

import math
import re

EARTH_RADIUS_KM = 6371.0088
_WKT_POINT = re.compile(r"^\s*POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)\s*$", re.IGNORECASE)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def degree_window(lat: float, km: float) -> tuple[float, float]:
    """(delta_lat, delta_lon) in degrees that surely contain every point within `km` of latitude `lat`."""
    dlat = km / 111.32
    cos_lat = max(math.cos(math.radians(lat)), 0.05)  # never divide by ~0 near the poles
    dlon = min(180.0, km / (111.32 * cos_lat))
    return dlat, dlon


def wkt_point(text: str | None) -> tuple[float, float] | None:
    """'POINT (lon lat)' -> (lon, lat); None for anything else."""
    if not text:
        return None
    match = _WKT_POINT.match(str(text))
    if not match:
        return None
    lon, lat = float(match.group(1)), float(match.group(2))
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lon, lat


def bbox_of(geometry: dict) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon) over every vertex of a GeoJSON geometry."""
    points: list[tuple[float, float]] = []

    def walk(node) -> None:
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            points.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates"))
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), min(lons), max(lats), max(lons)


def bbox_polygon(bbox) -> dict | None:
    """A GeoJSON Polygon for [min_lon, min_lat, max_lon, max_lat]; None when the box is a point or a line."""
    if not bbox or len(bbox) != 4:
        return None
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if max_lon <= min_lon or max_lat <= min_lat:
        return None
    ring = [[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]
    return {"type": "Polygon", "coordinates": [ring]}
