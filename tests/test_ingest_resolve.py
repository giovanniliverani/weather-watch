"""Ingest is idempotent on the natural key plus payload hash; resolve creates each event once."""

import copy
import json

from eww import ingest, resolve, snapshots
from tests.conftest import envelope, eonet_events, gdacs_features

EONET_RECORDS = 1 + 1 + 2 + 9  # wildfire, flood polygon, storm with two geometry entries, nine synthetic events
EONET_EVENTS = 12
GDACS_RECORDS = 3


def ingest_fixtures(conn, data_dir, stamp="2026-09-16T15:10:00Z", run_id="01TESTRUN000000000000000AA"):
    stats = []
    for source_id, items in (("gdacs", gdacs_features()), ("eonet", eonet_events())):
        path = snapshots.write(envelope(source_id, items, stamp, run_id), data_dir)
        stats.append(ingest.ingest_snapshot(conn, path, data_dir=data_dir))
    return stats


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_snapshot_path_format(data_dir):
    path = snapshots.write(envelope("gdacs", [], "2026-09-16T15:10:42Z"), data_dir)
    assert path == data_dir / "snapshots" / "gdacs" / "2026-09-16T15-10Z.json"
    assert snapshots.relative_path(path, data_dir) == "snapshots/gdacs/2026-09-16T15-10Z.json"
    assert snapshots.read(path)["format"] == "eww.snapshot/1"


def test_ingest_twice_adds_nothing(conn, data_dir):
    first = ingest_fixtures(conn, data_dir)
    assert [s.new for s in first] == [GDACS_RECORDS, EONET_RECORDS]
    assert all(not s.errors for s in first)
    assert count(conn, "SELECT COUNT(*) FROM source_record") == GDACS_RECORDS + EONET_RECORDS
    assert count(conn, "SELECT COUNT(*) FROM collector_run") == 2

    # the same files again: skipped because snapshot_ingest lists them
    again = ingest_fixtures(conn, data_dir)
    assert all(s.skipped for s in again)

    # a later snapshot with identical items: nothing new, nothing changed, last_seen_at moves
    later = ingest_fixtures(conn, data_dir, stamp="2026-09-16T18:10:00Z", run_id="01TESTRUN000000000000000AB")
    assert [s.new for s in later] == [0, 0]
    assert [s.changed for s in later] == [0, 0]
    assert sum(s.unchanged for s in later) == GDACS_RECORDS + EONET_RECORDS
    assert count(conn, "SELECT COUNT(*) FROM source_record") == GDACS_RECORDS + EONET_RECORDS
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE last_seen_at = '2026-09-16T18:10:00Z'") == GDACS_RECORDS + EONET_RECORDS
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE first_seen_at = '2026-09-16T15:10:00Z'") == GDACS_RECORDS + EONET_RECORDS
    assert ingest.duplicates(conn) == []


def test_changed_payload_updates_in_place(conn, data_dir):
    ingest_fixtures(conn, data_dir)
    features = copy.deepcopy(gdacs_features())
    flood = next(f for f in features if f["properties"]["eventtype"] == "FL")
    flood["properties"]["alertlevel"] = "Red"
    path = snapshots.write(envelope("gdacs", features, "2026-09-16T21:10:00Z", "01TESTRUN000000000000000AC"), data_dir)
    stats = ingest.ingest_snapshot(conn, path, data_dir=data_dir)
    assert (stats.new, stats.changed, stats.unchanged) == (0, 1, 2)
    row = conn.execute("SELECT payload, severity_raw FROM source_record WHERE source_id = 'gdacs' AND external_id = ?", (str(flood["properties"]["eventid"]),)).fetchone()
    assert json.loads(row["payload"])["properties"]["alertlevel"] == "Red"
    assert json.loads(row["severity_raw"])["alertlevel"] == "Red"
    assert ingest.duplicates(conn) == []


def test_resolve_creates_each_event_once(conn, data_dir):
    ingest_fixtures(conn, data_dir)
    stats = resolve.resolve(conn)
    assert stats.records_resolved == GDACS_RECORDS + EONET_RECORDS
    assert stats.events_created == GDACS_RECORDS + EONET_EVENTS
    assert stats.events_attached == 1  # the storm's second geometry entry
    assert resolve.unresolved_count(conn) == 0
    assert count(conn, "SELECT COUNT(*) FROM event") == GDACS_RECORDS + EONET_EVENTS
    assert count(conn, "SELECT COUNT(*) FROM event_geometry WHERE is_primary = 1") == GDACS_RECORDS + EONET_EVENTS
    assert count(conn, "SELECT COUNT(*) FROM event_geometry WHERE role = 'footprint'") == 1

    storm = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.external_id = 'EONET_23611' LIMIT 1").fetchone()
    assert storm["hazard_type"] == "tropical_cyclone"
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE event_id = ?", storm["event_id"]) == 2
    assert storm["severity_score"] == 0.4

    flood = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.external_id = 'EONET_24267'").fetchone()
    assert flood["country_iso3"] == "HRV"
    assert abs(flood["centroid_lat"] - 43.51) < 0.2

    gdacs_flood = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.source_id = 'gdacs' AND r.hazard_type = 'flood'").fetchone()
    assert (gdacs_flood["severity_label"], gdacs_flood["severity_score"], gdacs_flood["country_iso3"]) == ("Orange", 0.66, "CHN")
    cyclone = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.source_id = 'gdacs' AND r.hazard_type = 'tropical_cyclone'").fetchone()
    assert cyclone["status"] == "active" and cyclone["ended_at"] is None

    # running resolve again is a no-op
    again = resolve.resolve(conn)
    assert (again.records_resolved, again.events_created, again.events_changed) == (0, 0, 0)
    assert count(conn, "SELECT COUNT(*) FROM event") == GDACS_RECORDS + EONET_EVENTS


def test_new_episode_reuses_the_event(conn, data_dir):
    ingest_fixtures(conn, data_dir)
    resolve.resolve(conn)
    events_before = count(conn, "SELECT COUNT(*) FROM event")
    features = copy.deepcopy(gdacs_features())
    cyclone = next(f for f in features if f["properties"]["eventtype"] == "TC")
    cyclone["properties"]["episodeid"] = cyclone["properties"]["episodeid"] + 1
    cyclone["properties"]["alertlevel"] = "Orange"
    cyclone["properties"]["datemodified"] = "2026-09-17T06:00:00"
    cyclone["geometry"]["coordinates"] = [-118.0, 17.0]
    path = snapshots.write(envelope("gdacs", [cyclone], "2026-09-17T06:10:00Z", "01TESTRUN000000000000000AD"), data_dir)
    assert ingest.ingest_snapshot(conn, path, data_dir=data_dir).new == 1
    stats = resolve.resolve(conn)
    assert (stats.records_resolved, stats.events_created, stats.events_attached) == (1, 0, 1)
    assert count(conn, "SELECT COUNT(*) FROM event") == events_before
    event = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.external_id = ? GROUP BY e.event_id", (str(cyclone["properties"]["eventid"]),)).fetchone()
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE event_id = ?", event["event_id"]) == 2
    assert event["severity_label"] == "Orange"
    assert event["last_observed_at"] == "2026-09-17T06:00:00Z"
    assert (event["centroid_lon"], event["centroid_lat"]) == (-118.0, 17.0)
    primary = conn.execute("SELECT geojson FROM event_geometry WHERE event_id = ? AND is_primary = 1", (event["event_id"],)).fetchone()
    assert json.loads(primary["geojson"])["coordinates"] == [-118.0, 17.0]
