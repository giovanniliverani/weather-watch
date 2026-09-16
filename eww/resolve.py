"""Resolve: source_record -> event. `create_event()` is the only INSERT INTO event in the codebase.

M0 form of the identity rules in docs/architecture.md §3: a record joins the live event that
already carries its GLIDE number, else the event of a sibling record with the same
(source_id, external_id), else a new event. No cross-source scoring yet (that is M2).

After attaching, `refresh_event()` recomputes every derived column of the event from all of
its records (window, status, severity, centroid, country, GLIDE) and keeps the primary Point
and any footprint polygons in event_geometry in step. It only writes when something changed,
so running `eww resolve` twice is a no-op the second time.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from eww import collectors, config
from eww.clock import now_iso, now_utc, to_iso
from eww.ids import new_id

log = logging.getLogger(__name__)


@dataclass
class ResolveStats:
    records_resolved: int = 0
    events_created: int = 0
    events_attached: int = 0
    events_refreshed: int = 0
    events_changed: int = 0


def resolve(conn: sqlite3.Connection, *, refresh_days: int | None = 30) -> ResolveStats:
    stats = ResolveStats()
    touched: set[str] = set()
    unresolved = conn.execute(
        "SELECT * FROM source_record WHERE event_id IS NULL ORDER BY source_id, external_id, observed_at, source_record_id"
    ).fetchall()
    with conn:
        for rec in unresolved:
            event_id, created = resolve_record(conn, rec)
            touched.add(event_id)
            stats.records_resolved += 1
            if created:
                stats.events_created += 1
            else:
                stats.events_attached += 1
    if refresh_days is not None:
        since = to_iso(now_utc() - timedelta(days=refresh_days))
        for row in conn.execute(
            "SELECT DISTINCT event_id FROM source_record WHERE event_id IS NOT NULL AND last_seen_at >= ?", (since,)
        ):
            touched.add(row[0])
    with conn:
        for event_id in sorted(touched):
            stats.events_refreshed += 1
            if refresh_event(conn, event_id):
                stats.events_changed += 1
    log.info(
        "resolve records=%d created=%d attached=%d refreshed=%d changed=%d",
        stats.records_resolved, stats.events_created, stats.events_attached, stats.events_refreshed, stats.events_changed,
    )
    return stats


def canonical_event_id(conn: sqlite3.Connection, event_id: str) -> str:
    """Follow merged_into_event_id pointers to the live event."""
    seen = {event_id}
    while True:
        row = conn.execute("SELECT merged_into_event_id FROM event WHERE event_id = ?", (event_id,)).fetchone()
        if row is None or row[0] is None or row[0] in seen:
            return event_id
        event_id = row[0]
        seen.add(event_id)


def resolve_record(conn: sqlite3.Connection, rec: sqlite3.Row) -> tuple[str, bool]:
    """Attach one unresolved record to an event, creating the event if needed. Returns (event_id, created)."""
    if rec["glide_number"]:
        row = conn.execute(
            "SELECT event_id FROM event WHERE glide_number = ? AND merged_into_event_id IS NULL", (rec["glide_number"],)
        ).fetchone()
        if row:
            _attach(conn, rec["source_record_id"], row["event_id"])
            return row["event_id"], False
    row = conn.execute(
        """
        SELECT event_id FROM source_record
        WHERE source_id = ? AND external_id = ? AND event_id IS NOT NULL
        ORDER BY observed_at DESC, source_record_id DESC LIMIT 1
        """,
        (rec["source_id"], rec["external_id"]),
    ).fetchone()
    if row:
        event_id = canonical_event_id(conn, row["event_id"])
        _attach(conn, rec["source_record_id"], event_id)
        return event_id, False
    event_id = create_event(conn, rec)
    _attach(conn, rec["source_record_id"], event_id)
    return event_id, True


def _attach(conn: sqlite3.Connection, source_record_id: str, event_id: str) -> None:
    conn.execute("UPDATE source_record SET event_id = ? WHERE source_record_id = ?", (event_id, source_record_id))


def create_event(conn: sqlite3.Connection, rec: sqlite3.Row) -> str:
    """THE ONLY INSERT INTO event. Derived columns are filled by refresh_event() right after."""
    event_id = new_id()
    now = now_iso()
    conn.execute(
        """
        INSERT INTO event (event_id, hazard_type, title, status, started_at, ended_at, last_observed_at,
                           centroid_lat, centroid_lon, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            rec["hazard_type"],
            rec["title"] or f"{rec['hazard_type']} {rec['external_id']}",
            "active" if rec["ended_at"] is None else "ended",
            rec["started_at"] or rec["observed_at"],
            rec["ended_at"],
            rec["observed_at"],
            rec["lat"],
            rec["lon"],
            now,
            now,
        ),
    )
    return event_id


def derive(conn: sqlite3.Connection, recs: list[sqlite3.Row]) -> dict:
    """Event columns as a function of its records; the latest observation wins ties."""
    latest = recs[-1]
    collector = collectors.get(latest["source_id"])
    label, score = collector.severity(json.loads(latest["payload"]))
    ended = None
    if latest["ended_at"] is not None:
        ended = max(r["ended_at"] for r in recs if r["ended_at"])
    country = None
    for r in reversed(recs):
        country = collectors.get(r["source_id"]).country_iso3(json.loads(r["payload"]))
        if country:
            break
    return {
        "hazard_type": latest["hazard_type"],
        "title": latest["title"],
        "status": "active" if ended is None else "ended",
        "started_at": min(r["started_at"] or r["observed_at"] for r in recs),
        "ended_at": ended,
        "last_observed_at": max(r["observed_at"] for r in recs),
        "severity_score": score,
        "severity_label": label,
        "country_iso3": country,
        "glide_number": next((r["glide_number"] for r in reversed(recs) if r["glide_number"]), None),
    }


def refresh_event(conn: sqlite3.Connection, event_id: str) -> bool:
    """Recompute the event's derived columns and geometries from its records. True when anything changed."""
    current = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
    if current is None or current["merged_into_event_id"] is not None:
        return False
    recs = conn.execute(
        "SELECT * FROM source_record WHERE event_id = ? ORDER BY observed_at, source_record_id", (event_id,)
    ).fetchall()
    if not recs:
        return False
    values = derive(conn, recs)
    located = next((r for r in reversed(recs) if r["lat"] is not None and r["lon"] is not None), None)
    values["centroid_lat"] = located["lat"] if located else None
    values["centroid_lon"] = located["lon"] if located else None
    if values["glide_number"]:
        clash = conn.execute(
            "SELECT event_id FROM event WHERE glide_number = ? AND merged_into_event_id IS NULL AND event_id <> ?",
            (values["glide_number"], event_id),
        ).fetchone()
        if clash:
            log.warning("glide %s already on event %s; leaving event %s without it", values["glide_number"], clash[0], event_id)
            values["glide_number"] = None
    changed = any(current[key] != value for key, value in values.items())
    if changed:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        conn.execute(
            f"UPDATE event SET {assignments}, updated_at = :updated_at WHERE event_id = :event_id",
            {**values, "updated_at": now_iso(), "event_id": event_id},
        )
    return _sync_geometries(conn, event_id, recs, located) or changed


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
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


def _sync_geometries(conn: sqlite3.Connection, event_id: str, recs: list[sqlite3.Row], located: sqlite3.Row | None) -> bool:
    changed = False
    if located is not None:
        lat, lon = float(located["lat"]), float(located["lon"])
        values = {
            "role": "centroid",
            "geojson": json.dumps({"type": "Point", "coordinates": [lon, lat]}),
            "min_lat": lat,
            "min_lon": lon,
            "max_lat": lat,
            "max_lon": lon,
            "precision": config.GEOMETRY_PRECISION_BY_HAZARD.get(located["hazard_type"], config.GEOMETRY_PRECISION_DEFAULT),
            "observed_at": located["observed_at"],
            "source_id": located["source_id"],
            "source_record_id": located["source_record_id"],
        }
        row = conn.execute("SELECT * FROM event_geometry WHERE event_id = ? AND is_primary = 1", (event_id,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO event_geometry (geometry_id, event_id, role, geojson, min_lat, min_lon, max_lat, max_lon,
                                            precision, observed_at, source_id, source_record_id, is_primary)
                VALUES (:geometry_id, :event_id, :role, :geojson, :min_lat, :min_lon, :max_lat, :max_lon,
                        :precision, :observed_at, :source_id, :source_record_id, 1)
                """,
                {"geometry_id": new_id(), "event_id": event_id, **values},
            )
            changed = True
        elif any(row[key] != value for key, value in values.items()):
            assignments = ", ".join(f"{key} = :{key}" for key in values)
            conn.execute(f"UPDATE event_geometry SET {assignments} WHERE geometry_id = :geometry_id", {**values, "geometry_id": row["geometry_id"]})
            changed = True
    for rec in recs:
        footprint = collectors.get(rec["source_id"]).footprint(json.loads(rec["payload"]))
        if footprint is None:
            continue
        geojson = json.dumps(footprint, sort_keys=True)
        min_lat, min_lon, max_lat, max_lon = _bbox(footprint)
        row = conn.execute(
            "SELECT geometry_id, geojson FROM event_geometry WHERE event_id = ? AND role = 'footprint' AND source_record_id = ?",
            (event_id, rec["source_record_id"]),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO event_geometry (geometry_id, event_id, role, geojson, min_lat, min_lon, max_lat, max_lon,
                                            precision, observed_at, source_id, source_record_id, is_primary)
                VALUES (?, ?, 'footprint', ?, ?, ?, ?, ?, 'exact', ?, ?, ?, 0)
                """,
                (new_id(), event_id, geojson, min_lat, min_lon, max_lat, max_lon, rec["observed_at"], rec["source_id"], rec["source_record_id"]),
            )
            changed = True
        elif row["geojson"] != geojson:
            conn.execute(
                "UPDATE event_geometry SET geojson = ?, min_lat = ?, min_lon = ?, max_lat = ?, max_lon = ?, observed_at = ? WHERE geometry_id = ?",
                (geojson, min_lat, min_lon, max_lat, max_lon, rec["observed_at"], row["geometry_id"]),
            )
            changed = True
    return changed


def unresolved_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM source_record WHERE event_id IS NULL").fetchone()[0]
