"""Derived state of an event: the columns of `event` and the rows of `event_geometry` that follow
from its source_record rows. Nothing here decides identity; resolve and merge do that and then
call `refresh_event()`, which recomputes deterministically, so calling it twice is a no-op.

Which record speaks for the event follows config.PRIMARY_SOURCE_ORDER (GDACS, then Copernicus,
then EONET): its latest record supplies the title, the hazard type and the primary Point; the
severity comes from eww.severity.normalise() over every record; the window is the union of all
records; the event has ended only when every source's latest record says so.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from eww import collectors, config, geo, severity
from eww.clock import now_iso
from eww.ids import new_id

log = logging.getLogger(__name__)


def canonical_event_id(conn: sqlite3.Connection, event_id: str) -> str:
    """Follow merged_into_event_id pointers to the live event."""
    seen = {event_id}
    while True:
        row = conn.execute("SELECT merged_into_event_id FROM event WHERE event_id = ?", (event_id,)).fetchone()
        if row is None or row[0] is None or row[0] in seen:
            return event_id
        event_id = row[0]
        seen.add(event_id)


def event_records(conn: sqlite3.Connection, event_id: str) -> list[sqlite3.Row]:
    """The event's records, oldest observation first."""
    return conn.execute(
        "SELECT * FROM source_record WHERE event_id = ? ORDER BY observed_at, source_record_id", (event_id,)
    ).fetchall()


def payload_of(rec) -> dict:
    payload = rec["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


def latest_by_source(recs) -> dict[str, sqlite3.Row]:
    """source_id -> its latest record (recs are in ascending observation order, so the last wins)."""
    out: dict[str, sqlite3.Row] = {}
    for rec in recs:
        out[rec["source_id"]] = rec
    return out


def source_order(present) -> list[str]:
    """Sources present, primary first; unknown sources after the configured ones, alphabetically."""
    present = set(present)
    return [s for s in config.PRIMARY_SOURCE_ORDER if s in present] + sorted(present - set(config.PRIMARY_SOURCE_ORDER))


def primary_record(recs, *, located: bool = False) -> sqlite3.Row | None:
    """The latest record of the highest-priority source, optionally only among records with coordinates."""
    pool = [r for r in recs if not located or (r["lat"] is not None and r["lon"] is not None)]
    if not pool:
        return None
    by_source = latest_by_source(pool)
    return by_source[source_order(by_source)[0]]


def derive(recs) -> dict:
    """Event columns as a function of its records."""
    primary = primary_record(recs)
    by_source = latest_by_source(recs)
    ended = None
    if all(r["ended_at"] is not None for r in by_source.values()):
        ended = max(r["ended_at"] for r in by_source.values())
    country = None
    for source_id in source_order(by_source):
        for rec in reversed([r for r in recs if r["source_id"] == source_id]):
            country = collectors.get(source_id).country_iso3(payload_of(rec))
            if country:
                break
        if country:
            break
    glide = None
    for source_id in source_order(by_source):
        glide = next((r["glide_number"] for r in reversed(recs) if r["source_id"] == source_id and r["glide_number"]), None)
        if glide:
            break
    sev = severity.normalise(recs)
    return {
        "hazard_type": primary["hazard_type"],
        "title": primary["title"] or f"{primary['hazard_type']} {primary['external_id']}",
        "status": "active" if ended is None else "ended",
        "started_at": min(r["started_at"] or r["observed_at"] for r in recs),
        "ended_at": ended,
        "last_observed_at": max(r["observed_at"] for r in recs),
        "severity_score": sev.score,
        "severity_label": sev.label,
        "country_iso3": country,
        "glide_number": glide,
    }


def refresh_event(conn: sqlite3.Connection, event_id: str) -> bool:
    """Recompute the event's derived columns and geometries from its records. True when anything changed.

    Merged events are left alone: their records live on the canonical event now.
    """
    current = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
    if current is None or current["merged_into_event_id"] is not None:
        return False
    recs = event_records(conn, event_id)
    if not recs:
        return False
    values = derive(recs)
    located = primary_record(recs, located=True)
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
    return sync_geometries(conn, event_id, recs, located) or changed


def sync_geometries(conn: sqlite3.Connection, event_id: str, recs, located) -> bool:
    """Keep the primary Point and the footprint polygons of `event_id` in step with its records.

    Footprints whose record has moved to another event (merge, revert) or stopped supplying a
    polygon are deleted: event_geometry rows are derived, only `event` rows are never deleted.
    """
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
    kept: list[str] = []
    for rec in recs:
        collector = collectors.get(rec["source_id"])
        footprint = collector.footprint(payload_of(rec))
        if footprint is None:
            continue
        kept.append(rec["source_record_id"])
        geojson = json.dumps(footprint, sort_keys=True)
        min_lat, min_lon, max_lat, max_lon = geo.bbox_of(footprint)
        precision = getattr(collector, "FOOTPRINT_PRECISION", "exact")
        row = conn.execute(
            "SELECT geometry_id, geojson, precision FROM event_geometry WHERE event_id = ? AND role = 'footprint' AND source_record_id = ?",
            (event_id, rec["source_record_id"]),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO event_geometry (geometry_id, event_id, role, geojson, min_lat, min_lon, max_lat, max_lon,
                                            precision, observed_at, source_id, source_record_id, is_primary)
                VALUES (?, ?, 'footprint', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (new_id(), event_id, geojson, min_lat, min_lon, max_lat, max_lon, precision, rec["observed_at"], rec["source_id"], rec["source_record_id"]),
            )
            changed = True
        elif row["geojson"] != geojson or row["precision"] != precision:
            conn.execute(
                "UPDATE event_geometry SET geojson = ?, min_lat = ?, min_lon = ?, max_lat = ?, max_lon = ?, precision = ?, observed_at = ? WHERE geometry_id = ?",
                (geojson, min_lat, min_lon, max_lat, max_lon, precision, rec["observed_at"], row["geometry_id"]),
            )
            changed = True
    marks = ", ".join("?" * len(kept))
    stale = conn.execute(
        "DELETE FROM event_geometry WHERE event_id = ? AND role = 'footprint' AND source_record_id IS NOT NULL"
        + (f" AND source_record_id NOT IN ({marks})" if kept else ""),
        (event_id, *kept),
    )
    return changed or stale.rowcount > 0
