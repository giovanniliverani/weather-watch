"""The GeoJSON contract from docs/architecture.md §2, plus (M3) the two other reads the viewer needs:
`event_documents()` for the sidebar's News tab and `attributions()` for the About section. The viewer
imports this module and eww.review and nothing else.

    events_geojson(since, until=None, hazard=None, min_severity=0.0, status=None,
                   bbox=None, include_footprints=False, limit=2000) -> FeatureCollection

Every filter is applied here. Merge pointers (event.merged_into_event_id) are followed here, so
a merged event never appears twice and its records count towards the canonical event. An event
is "in the window" when it was last observed at or after `since` and started at or before
`until`; `since`/`until` accept ISO 8601 or a relative span such as "14d". `limit=0` (or None)
returns everything in the window; the CLI exporter and the viewer use that so the pin count always
equals the SQL count of events observed in the window.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Iterable

from eww import collectors, config, db, embed, events, heartbeat
from eww.clock import now_utc, parse_when, to_iso

HAZARD_TYPES: list[str] = list(config.HAZARD_TYPES)
EMS_SOURCE_ID = "copernicus"  # ems_activation is True when a Copernicus EMS activation sits on the event
DEFAULT_LIMIT = 2000

EVENT_PROPERTIES = (
    "event_id",
    "hazard_type",
    "title",
    "status",
    "started_at",
    "ended_at",
    "last_observed_at",
    "severity_score",
    "severity_label",
    "country_iso3",
    "precision",
    "glide_number",
    "source_ids",
    "ems_activation",
    "doc_count",
    "post_count",
    "video_count",
    "summary",
    "summary_updated_at",
    "thumbnail_url",
    "detail_url",
)
FOOTPRINT_PROPERTIES = ("event_id", "role", "observed_at", "source_id")
FOOTPRINT_ROLES = ("footprint", "track", "impact_area")
META_KEYS = ("data_as_of", "last_collector_run_at", "missed_runs_7d", "expected_runs_7d", "generated_at", "filters_applied")
# Thresholds for the viewer's status strip (the viewer imports only this module).
STATUS_RED_MISSED_RUNS = config.STATUS_RED_MISSED_RUNS
STATUS_RED_STALE_HOURS = config.STATUS_RED_STALE_HOURS

# The SQL definition of "an event observed in the window"; `eww doctor` prints the same count so
# the exporter can be checked against it (exit criterion 3 in docs/architecture.md §4).
WINDOW_SQL = """
    e.merged_into_event_id IS NULL
    AND e.status NOT IN ('merged', 'rejected')
    AND e.last_observed_at >= :since
    AND (:until IS NULL OR e.started_at <= :until)
    AND e.centroid_lat IS NOT NULL AND e.centroid_lon IS NOT NULL
"""


def _as_list(value: str | Iterable[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    values = [v for v in value if v]
    return values or None


def _chunks(items: list, size: int = 400) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _merge_map(conn: sqlite3.Connection) -> dict[str, str]:
    """merged event_id -> canonical event_id, following chains."""
    pointers = {row[0]: row[1] for row in conn.execute("SELECT event_id, merged_into_event_id FROM event WHERE merged_into_event_id IS NOT NULL")}
    canonical: dict[str, str] = {}
    for start in pointers:
        node, seen = start, {start}
        while node in pointers and pointers[node] not in seen:
            node = pointers[node]
            seen.add(node)
        canonical[start] = node
    return canonical


def events_geojson(
    since: str | datetime,
    until: str | datetime | None = None,
    hazard: str | Iterable[str] | None = None,
    min_severity: float = 0.0,
    status: str | Iterable[str] | None = None,
    bbox: Iterable[float] | None = None,
    include_footprints: bool = False,
    limit: int = DEFAULT_LIMIT,
    *,
    conn: sqlite3.Connection | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or now_utc()
    since_dt = parse_when(since, now)
    if since_dt is None:
        raise ValueError("since is required")
    until_dt = parse_when(until, now)
    since_iso, until_iso = to_iso(since_dt), (to_iso(until_dt) if until_dt else None)
    hazards, statuses = _as_list(hazard), _as_list(status)
    bbox_list = [float(v) for v in bbox] if bbox is not None else None
    if bbox_list is not None and len(bbox_list) != 4:
        raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat]")

    own = conn is None
    conn = conn or db.connect()
    try:
        merge_map = _merge_map(conn)
        events = _select_events(conn, since_iso, until_iso, hazards, min_severity, statuses, bbox_list, limit)
        members: dict[str, list[str]] = {e["event_id"]: [e["event_id"]] for e in events}
        for merged, canonical in merge_map.items():
            if canonical in members:
                members[canonical].append(merged)
        records = _records_by_event(conn, members)
        documents = _documents_by_event(conn, members)
        features = [_feature(e, records.get(e["event_id"], []), documents.get(e["event_id"], {})) for e in events]
        if include_footprints:
            features.extend(_footprints(conn, members))
        meta = _meta(conn, now, since_iso, until_iso, hazards, min_severity, statuses, bbox_list, include_footprints, limit)
    finally:
        if own:
            conn.close()
    return {"type": "FeatureCollection", "meta": meta, "features": features}


def _select_events(conn, since_iso, until_iso, hazards, min_severity, statuses, bbox_list, limit) -> list[sqlite3.Row]:
    params: dict = {"since": since_iso, "until": until_iso}
    clauses = [WINDOW_SQL]
    if hazards:
        names = [f":hazard{i}" for i in range(len(hazards))]
        clauses.append(f"e.hazard_type IN ({', '.join(names)})")
        params.update({f"hazard{i}": h for i, h in enumerate(hazards)})
    if min_severity and min_severity > 0:
        clauses.append("COALESCE(e.severity_score, 0) >= :min_severity")
        params["min_severity"] = float(min_severity)
    if statuses:
        names = [f":status{i}" for i in range(len(statuses))]
        clauses.append(f"e.status IN ({', '.join(names)})")
        params.update({f"status{i}": s for i, s in enumerate(statuses)})
    if bbox_list is not None:
        clauses.append("e.centroid_lon BETWEEN :min_lon AND :max_lon AND e.centroid_lat BETWEEN :min_lat AND :max_lat")
        params.update(dict(zip(("min_lon", "min_lat", "max_lon", "max_lat"), bbox_list)))
    sql = f"""
        SELECT e.*, g.precision AS precision
        FROM event e
        LEFT JOIN event_geometry g ON g.event_id = e.event_id AND g.is_primary = 1
        WHERE {' AND '.join(clauses)}
        ORDER BY e.last_observed_at DESC, e.event_id
    """
    if limit:  # 0 or None: everything in the window (the CLI exporter's default)
        sql += " LIMIT :limit"
        params["limit"] = int(limit)
    return conn.execute(sql, params).fetchall()


def _records_by_event(conn, members: dict[str, list[str]]) -> dict[str, list[sqlite3.Row]]:
    owner = {m: canonical for canonical, ms in members.items() for m in ms}
    out: dict[str, list[sqlite3.Row]] = {}
    for chunk in _chunks(list(owner)):
        marks = ", ".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT event_id, source_id, observed_at, payload FROM source_record WHERE event_id IN ({marks}) ORDER BY observed_at, source_record_id",
            chunk,
        ):
            out.setdefault(owner[row["event_id"]], []).append(row)
    return out


def _documents_by_event(conn, members: dict[str, list[str]]) -> dict[str, dict]:
    """Attached document counts by kind and the newest image URL, per canonical event (empty until M3)."""
    owner = {m: canonical for canonical, ms in members.items() for m in ms}
    out: dict[str, dict] = {}
    for chunk in _chunks(list(owner)):
        marks = ", ".join("?" * len(chunk))
        rows = conn.execute(
            f"""
            SELECT ed.event_id, d.kind, d.media_url, d.media_kind, d.published_at
            FROM event_document ed JOIN document d ON d.document_id = ed.document_id
            WHERE ed.status = 'attached' AND d.removed_at IS NULL AND ed.event_id IN ({marks})
            ORDER BY d.published_at DESC
            """,
            chunk,
        )
        for row in rows:
            entry = out.setdefault(owner[row["event_id"]], {"counts": {}, "thumbnail_url": None})
            entry["counts"][row["kind"]] = entry["counts"].get(row["kind"], 0) + 1
            if entry["thumbnail_url"] is None and row["media_url"] and row["media_kind"] == "image":
                entry["thumbnail_url"] = row["media_url"]
    return out


def _detail_url(records: list[sqlite3.Row], primary_event_id: str) -> str | None:
    """The primary source's page (config.PRIMARY_SOURCE_ORDER), latest record first, as for the title and the pin."""
    own = [r for r in records if r["event_id"] == primary_event_id] or records
    for source_id in events.source_order({r["source_id"] for r in own}):
        for row in reversed([r for r in own if r["source_id"] == source_id]):
            url = collectors.get(row["source_id"]).detail_url(json.loads(row["payload"]))
            if url:
                return url
    return None


def _feature(event: sqlite3.Row, records: list[sqlite3.Row], documents: dict) -> dict:
    source_ids = sorted({r["source_id"] for r in records})
    counts = documents.get("counts", {})
    properties = {
        "event_id": event["event_id"],
        "hazard_type": event["hazard_type"],
        "title": event["title"],
        "status": event["status"],
        "started_at": event["started_at"],
        "ended_at": event["ended_at"],
        "last_observed_at": event["last_observed_at"],
        "severity_score": event["severity_score"],
        "severity_label": event["severity_label"],
        "country_iso3": event["country_iso3"],
        "precision": event["precision"] or "unresolved",
        "glide_number": event["glide_number"],
        "source_ids": source_ids,
        "ems_activation": EMS_SOURCE_ID in source_ids,
        "doc_count": counts.get("article", 0) + counts.get("report", 0),
        "post_count": counts.get("post", 0),
        "video_count": counts.get("video", 0),
        "summary": event["summary"],
        "summary_updated_at": event["summary_updated_at"],
        "thumbnail_url": documents.get("thumbnail_url"),
        "detail_url": _detail_url(records, event["event_id"]),
    }
    return {
        "type": "Feature",
        "id": event["event_id"],
        "geometry": {"type": "Point", "coordinates": [event["centroid_lon"], event["centroid_lat"]]},
        "properties": properties,
    }


def _footprints(conn, members: dict[str, list[str]]) -> list[dict]:
    """Footprint rows of the canonical events only: a merge moves the records, and the canonical
    event's refresh rebuilds their polygons under its own id, so a merged member's rows would draw twice."""
    features = []
    for chunk in _chunks(list(members)):
        marks = ", ".join("?" * len(chunk))
        roles = ", ".join(f"'{r}'" for r in FOOTPRINT_ROLES)
        for row in conn.execute(
            f"SELECT event_id, role, geojson, observed_at, source_id FROM event_geometry WHERE role IN ({roles}) AND event_id IN ({marks}) ORDER BY observed_at, geometry_id",
            chunk,
        ):
            features.append(
                {
                    "type": "Feature",
                    "geometry": json.loads(row["geojson"]),
                    "properties": {
                        "event_id": row["event_id"],
                        "role": row["role"],
                        "observed_at": row["observed_at"],
                        "source_id": row["source_id"],
                    },
                }
            )
    return features


def _meta(conn, now, since_iso, until_iso, hazards, min_severity, statuses, bbox_list, include_footprints, limit) -> dict:
    beat = heartbeat.summary(conn, now)
    data_as_of = conn.execute("SELECT MAX(last_seen_at) FROM source_record").fetchone()[0]
    return {
        "data_as_of": data_as_of,
        "last_collector_run_at": beat["last_collector_run_at"],
        "missed_runs_7d": beat["missed_runs_7d"],
        "expected_runs_7d": beat["expected_runs_7d"],
        "generated_at": to_iso(now),
        "filters_applied": {
            "since": since_iso,
            "until": until_iso,
            "hazard": hazards,
            "min_severity": float(min_severity or 0.0),
            "status": statuses,
            "bbox": bbox_list,
            "include_footprints": bool(include_footprints),
            "limit": int(limit or 0),
        },
    }


def count_in_window(conn: sqlite3.Connection, since: str | datetime, until: str | datetime | None = None, now: datetime | None = None) -> int:
    """The SQL count of events observed in the window, using exactly the exporter's definition."""
    now = now or now_utc()
    since_iso = to_iso(parse_when(since, now))
    until_dt = parse_when(until, now)
    return conn.execute(
        f"SELECT COUNT(*) FROM event e WHERE {WINDOW_SQL}",
        {"since": since_iso, "until": to_iso(until_dt) if until_dt else None},
    ).fetchone()[0]


# ----------------------------------------------------------------------------- M3: the News tab and the About section
DOCUMENT_FIELDS = ("document_id", "source_id", "kind", "title", "url", "publisher", "published_at", "media_url", "media_kind", "language", "score", "method", "decided_by")


def event_documents(event_id: str, *, conn: sqlite3.Connection | None = None, limit: int = 100) -> list[dict]:
    """Attached documents of the (canonical) event, newest first, syndicated copies collapsed into one entry.

    Each entry carries the representative document's fields plus `copies` (documents in the group),
    `publishers` (their distinct publishers) and `urls`. Copies are documents whose vectors lie within
    identity.yaml's `syndication_cosine` of each other (single linkage over the event's attached rows).
    """
    own = conn is None
    conn = conn or db.connect()
    try:
        canonical = events.canonical_event_id(conn, event_id)
        merged = [row[0] for row in conn.execute("SELECT event_id FROM event WHERE merged_into_event_id = ?", (canonical,))]
        ids = [canonical, *merged]
        marks = ", ".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT d.document_id, d.source_id, d.kind, d.title, d.url, d.publisher, d.published_at, d.media_url, d.media_kind, d.language,
                   ed.score, ed.method, ed.decided_by
            FROM event_document ed JOIN document d ON d.document_id = ed.document_id
            WHERE ed.event_id IN ({marks}) AND ed.status = 'attached' AND d.removed_at IS NULL
            ORDER BY COALESCE(d.published_at, d.fetched_at) DESC, d.document_id
            """,
            ids,
        ).fetchall()
        by_id = {row["document_id"]: dict(row) for row in rows}
        vectors = embed.vectors_for(conn, list(by_id))
        groups = embed.similarity_groups(vectors) if vectors else []
        grouped: set[str] = {doc_id for group in groups for doc_id in group}
        groups += [[doc_id] for doc_id in by_id if doc_id not in grouped]
        out = []
        for group in groups:
            members = sorted((by_id[i] for i in group if i in by_id), key=lambda d: (d["published_at"] or "", d["document_id"]), reverse=True)
            if not members:
                continue
            head = dict(members[0])
            head["copies"] = len(members)
            head["publishers"] = sorted({m["publisher"] for m in members if m["publisher"]})
            head["urls"] = [m["url"] for m in members]
            if head["media_url"] is None:
                head["media_url"] = next((m["media_url"] for m in members if m["media_url"] and m["media_kind"] == "image"), None)
                head["media_kind"] = "image" if head["media_url"] else head["media_kind"]
            out.append(head)
        out.sort(key=lambda d: (d["published_at"] or "", d["document_id"]), reverse=True)
        return out[:limit] if limit else out
    finally:
        if own:
            conn.close()


def attributions(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Every data source and service the map shows, with its credit line and terms page, for the About section."""
    own = conn is None
    conn = conn or db.connect()
    try:
        rows = conn.execute("SELECT source_id, display_name, attribution, terms_url FROM source ORDER BY kind, source_id").fetchall()
    finally:
        if own:
            conn.close()
    out = [{"id": r["source_id"], "name": r["display_name"], "attribution": r["attribution"], "terms_url": r["terms_url"]} for r in rows]
    out.append({"id": "geonames", "name": "GeoNames gazetteer and web service", "attribution": config.GEOCODER_ATTRIBUTIONS["gazetteer"], "terms_url": "https://creativecommons.org/licenses/by/4.0/"})
    out.append({"id": "nominatim", "name": "Nominatim (OpenStreetMap)", "attribution": config.GEOCODER_ATTRIBUTIONS["nominatim"], "terms_url": "https://operations.osmfoundation.org/policies/nominatim/"})
    out.append({"id": "osm-tiles", "name": "Map tiles", "attribution": "© OpenStreetMap contributors, ODbL", "terms_url": "https://www.openstreetmap.org/copyright"})
    return out
