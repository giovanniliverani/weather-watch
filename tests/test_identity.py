"""Cross-source identity end to end: deterministic keys, the storm-name auto-merge, the grey zone,
accept / reject / revert through eww.review, and the guards that keep wrong merges out."""

import json

from eww import api, config, matching, resolve, review
from eww import merge as merge_mod
from tests.conftest import copernicus_item, eonet_item, gdacs_item, gdacs_source, ingest_items

SINCE = "2026-01-01T00:00:00Z"
T0 = "2026-09-16T15:10:00Z"
T1 = "2026-09-16T18:10:00Z"


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def live_events(conn):
    return count(conn, "SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NULL")


def pins(conn, **kwargs):
    return api.events_geojson(SINCE, conn=conn, limit=0, **kwargs)["features"]


# ----------------------------------------------------------------------------- deterministic keys
def test_eonet_mirror_joins_its_gdacs_event(conn, data_dir):
    flood = gdacs_item(1104153, "FL", "Flood in Croatia", 43.51, 16.44, "2026-09-08T01:00:00", todate="2026-09-11T01:00:00", iscurrent="false", iso3="HRV")
    ring = [[43.54, 16.31], [43.54, 16.34], [43.59, 16.34], [43.59, 16.31], [43.54, 16.31]]  # EONET's [lat, lon] order
    mirror = eonet_item("EONET_24267", "Flood in Croatia 1104153", "floods", [ring], "2026-09-10T20:00:00Z", sources=(gdacs_source("FL", 1104153),), closed="2026-09-12T00:00:00Z", geometry_type="Polygon")
    ingest_items(conn, data_dir, "gdacs", [flood], T0)
    ingest_items(conn, data_dir, "eonet", [mirror], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_attached, stats.events_linked, stats.events_merged, stats.proposals_created) == (1, 1, 1, 0, 0)
    assert count(conn, "SELECT COUNT(*) FROM event") == 1 and count(conn, "SELECT COUNT(*) FROM event_lineage") == 0
    features = pins(conn, include_footprints=True)
    points = [f for f in features if f["geometry"]["type"] == "Point"]
    polygons = [f for f in features if f["geometry"]["type"] == "Polygon"]
    assert len(points) == 1 and points[0]["properties"]["source_ids"] == ["eonet", "gdacs"]
    assert points[0]["properties"]["title"] == "Flood in Croatia"  # GDACS is the primary source
    assert points[0]["geometry"]["coordinates"] == [16.44, 43.51]
    assert len(polygons) == 1 and polygons[0]["properties"]["event_id"] == points[0]["properties"]["event_id"]
    assert points[0]["properties"]["ems_activation"] is False
    again = resolve.resolve(conn)
    assert (again.records_resolved, again.events_changed) == (0, 0)


def test_copernicus_activation_joins_its_gdacs_event_and_flags_it(conn, data_dir):
    flood = gdacs_item(1104124, "FL", "Flood in Nepal", 28.2, 85.3, "2026-08-25T00:00:00", alertlevel="Green", episodealertscore=0.5, iso3="NPL")
    activation = copernicus_item("EMSR927", "Flood in Nepal", "Flood", 85.35, 28.21, "2026-08-25T22:00:00", "2026-08-26T09:53:00", gdacs_id="FL1104124", countries=("Nepal",))
    ingest_items(conn, data_dir, "copernicus", [activation], T0)  # arrives first: the order of the feeds must not matter
    ingest_items(conn, data_dir, "gdacs", [flood], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_linked) == (1, 1)
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE source_id = 'copernicus' AND event_id IS NULL") == 0
    feature = pins(conn)[0]["properties"]
    assert feature["ems_activation"] is True and feature["source_ids"] == ["copernicus", "gdacs"]
    assert feature["severity_label"] == "Green" and feature["severity_score"] == config.EMS_SEVERITY_FLOOR
    assert feature["detail_url"].startswith("https://www.gdacs.org/")
    assert pins(conn, min_severity=0.66) and pins(conn, min_severity=1.0) == []


def test_copernicus_alone_is_an_event_with_the_ems_floor(conn, data_dir):
    activation = copernicus_item("EMSR932", "Wildfire in Huelva Province, Spain", "Wildfire", -7.21, 37.79, "2026-09-15T13:00:00", "2026-09-15T16:58:00", closed=False)
    ingest_items(conn, data_dir, "copernicus", [activation], T0)
    resolve.resolve(conn)
    feature = pins(conn)[0]["properties"]
    assert feature["severity_label"] == "EMS activation" and feature["severity_score"] == 0.66 and feature["ems_activation"] is True
    assert feature["status"] == "active" and feature["country_iso3"] == "ESP"
    assert feature["detail_url"] == "https://rapidmapping.emergency.copernicus.eu/EMSR932"


def test_gdacs_arriving_after_its_mirror_finds_the_mirror_event(conn, data_dir):
    mirror = eonet_item("EONET_24268", "Wildfire in Namibia 1031934", "wildfires", [16.53, -19.22], "2026-09-11T19:00:00Z", sources=(gdacs_source("WF", 1031934),), magnitude=5747.0, unit="hectare")
    ingest_items(conn, data_dir, "eonet", [mirror], T0)
    first = resolve.resolve(conn)
    assert first.events_created == 1
    fire = gdacs_item(1031934, "WF", "Forest fire in Namibia", -19.2, 16.5, "2026-09-11T00:00:00")
    ingest_items(conn, data_dir, "gdacs", [fire], T1)
    second = resolve.resolve(conn)
    assert (second.events_created, second.events_attached, second.events_linked) == (0, 1, 1)
    assert count(conn, "SELECT COUNT(*) FROM event") == 1
    assert pins(conn)[0]["properties"]["title"] == "Forest fire in Namibia"


# ----------------------------------------------------------------------------- scoring: auto-merge and its revert
def storm_pair():
    gdacs_storm = gdacs_item(1001320, "TC", "Tropical Cyclone NORBERT-26", 20.5, -143.1, "2026-09-09T21:00:00", eventname="NORBERT-26")
    eonet_storm = eonet_item("EONET_24184", "Cyclone Norbert", "severeStorms", [-142.0, 21.0], "2026-09-10T06:00:00Z", sources=(("NOAA_NHC", "https://www.nhc.noaa.gov/archive/2026/NORBERT.shtml"),), magnitude=75.0, unit="kts")
    return gdacs_storm, eonet_storm


def test_named_storm_auto_merges_with_a_lineage_row_and_reverts(conn, data_dir):
    gdacs_storm, eonet_storm = storm_pair()
    ingest_items(conn, data_dir, "gdacs", [gdacs_storm], T0)
    ingest_items(conn, data_dir, "eonet", [eonet_storm], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_merged, stats.proposals_created) == (2, 1, 0)
    assert count(conn, "SELECT COUNT(*) FROM event") == 2 and live_events(conn) == 1
    lineage = conn.execute("SELECT * FROM event_lineage").fetchall()
    assert len(lineage) == 1 and lineage[0]["action"] == "merge" and lineage[0]["performed_by"] == "pipeline"
    assert lineage[0]["score"] == config.SCORE_STORM_NAME_EQUAL and json.loads(lineage[0]["evidence"])["rule"] == "storm_name"
    merged = conn.execute("SELECT * FROM event WHERE merged_into_event_id IS NOT NULL").fetchone()
    assert merged["status"] == "merged" and merged["merged_into_event_id"] == lineage[0]["to_event_id"]
    feature = pins(conn)
    assert len(feature) == 1 and feature[0]["properties"]["source_ids"] == ["eonet", "gdacs"]
    assert feature[0]["properties"]["title"] == "Tropical Cyclone NORBERT-26" and feature[0]["properties"]["severity_label"] == "Green"

    with conn:
        revert_id = merge_mod.revert(conn, lineage[0]["lineage_id"], "human")
    rows = conn.execute("SELECT * FROM event_lineage ORDER BY performed_at, lineage_id").fetchall()
    assert len(rows) == 2 and rows[1]["action"] == "revert" and rows[1]["lineage_id"] == revert_id
    assert rows[0]["reverted_by_lineage_id"] == revert_id
    assert count(conn, "SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NOT NULL") == 0
    assert len(pins(conn)) == 2
    restored = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.source_id = 'eonet'").fetchone()
    assert restored["status"] == "active" and restored["title"] == "Cyclone Norbert" and restored["severity_label"] == "75 kts"
    again = resolve.resolve(conn)
    assert (again.records_resolved, again.events_changed) == (0, 0)


def test_named_storm_merges_even_when_its_first_track_point_is_far_away(conn, data_dir):
    gdacs_storm = gdacs_item(1001314, "TC", "Tropical Cyclone KARINA-26", 23.1, -146.2, "2026-08-27T21:00:00", eventname="KARINA-26", datemodified="2026-09-05T00:00:00")
    first = eonet_item("EONET_23611", "Hurricane Karina", "severeStorms", [-111.5, 10.8], "2026-08-27T18:00:00Z", magnitude=40.0, unit="kts")
    first["geometry"].append({"magnitudeValue": 75.0, "magnitudeUnit": "kts", "date": "2026-09-04T18:00:00Z", "type": "Point", "coordinates": [-145.7, 23.0]})
    ingest_items(conn, data_dir, "gdacs", [gdacs_storm], T0)
    ingest_items(conn, data_dir, "eonet", [first], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_merged, stats.events_attached, live_events(conn)) == (1, 1, 1)  # first point 3,900 km away, second attaches as a sibling
    evidence = json.loads(conn.execute("SELECT evidence FROM event_lineage").fetchone()[0])
    assert evidence["rule"] == "storm_name" and evidence["distance_km"] > 3000
    unnamed = gdacs_item(1001399, "TC", "Tropical Cyclone TWENTYNINE-26", 10.0, -110.0, "2026-08-28T00:00:00", eventname="TWENTYNINE-26")
    ingest_items(conn, data_dir, "gdacs", [unnamed], T1)
    again = resolve.resolve(conn)
    assert again.events_merged == 0 and again.proposals_created == 0 and live_events(conn) == 2  # a different name never merges


def test_sibling_records_rescore_the_event(conn, data_dir):
    """A GDACS depression is numbered until named; EONET's track starts far away: the pair is found later."""
    two_c = gdacs_item(1001306, "TC", "Tropical Cyclone TWO-C-26", 16.3, -164.1, "2026-08-20T15:00:00", eventname="TWO-C-26")
    moke = eonet_item("EONET_23206", "Tropical Storm Moke", "severeStorms", [-150.0, 12.0], "2026-08-21T18:00:00Z", magnitude=35.0, unit="kts")
    ingest_items(conn, data_dir, "gdacs", [two_c], T0)
    ingest_items(conn, data_dir, "eonet", [moke], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_merged, stats.proposals_created) == (2, 0, 0)  # 1,500 km apart, names differ
    moke["geometry"].append({"magnitudeValue": 45.0, "magnitudeUnit": "kts", "date": "2026-08-22T18:00:00Z", "type": "Point", "coordinates": [-163.5, 16.2]})
    ingest_items(conn, data_dir, "eonet", [moke], T1)
    stats = resolve.resolve(conn)
    assert (stats.events_attached, stats.events_merged, stats.proposals_created) == (1, 0, 1)  # close now: proposed, never merged (different names)
    proposal = review.open_proposals(conn)[0]
    assert proposal["evidence"]["names_differ"] is True and proposal["event_a"]["title"] == "Tropical Storm Moke"
    renamed = gdacs_item(1001306, "TC", "Tropical Cyclone MOKE-26", 16.5, -164.5, "2026-08-20T15:00:00", eventname="MOKE-26", episodeid=2, datemodified="2026-08-23T00:00:00")
    ingest_items(conn, data_dir, "gdacs", [renamed], "2026-09-16T21:10:00Z")
    stats = resolve.resolve(conn)
    assert (stats.events_attached, stats.events_merged) == (1, 1)  # the new episode carries the name: merged by name
    assert live_events(conn) == 1 and review.open_proposals(conn) == []
    assert count(conn, "SELECT COUNT(*) FROM merge_proposal WHERE status = 'accepted'") == 1


def test_differently_named_storms_never_auto_merge(conn, data_dir):
    alpha = gdacs_item(1001401, "TC", "Tropical Cyclone ALPHA-26", 15.0, -50.0, "2026-09-01T00:00:00", eventname="ALPHA-26")
    beta = eonet_item("EONET_90010", "Tropical Cyclone Beta", "severeStorms", [-50.0, 15.0], "2026-09-01T00:00:00Z", magnitude=50.0, unit="kts")
    ingest_items(conn, data_dir, "gdacs", [alpha], T0)
    ingest_items(conn, data_dir, "eonet", [beta], T0)
    stats = resolve.resolve(conn)
    proposal = review.open_proposals(conn)[0]
    assert proposal["score"] >= config.AUTO_MERGE_THRESHOLD  # same place, same day, similar title...
    assert (stats.events_merged, stats.proposals_created, live_events(conn)) == (0, 1, 2)  # ...but a person decides


def test_a_human_revert_is_never_undone_by_the_pipeline(conn, data_dir):
    gdacs_storm, eonet_storm = storm_pair()
    ingest_items(conn, data_dir, "gdacs", [gdacs_storm], T0)
    ingest_items(conn, data_dir, "eonet", [eonet_storm], T0)
    resolve.resolve(conn)
    lineage_id = conn.execute("SELECT lineage_id FROM event_lineage").fetchone()[0]
    review.revert(lineage_id, conn)
    eonet_storm["geometry"].append({"magnitudeValue": 80.0, "magnitudeUnit": "kts", "date": "2026-09-11T06:00:00Z", "type": "Point", "coordinates": [-143.0, 20.6]})
    ingest_items(conn, data_dir, "eonet", [eonet_storm], T1)
    stats = resolve.resolve(conn)
    assert (stats.events_attached, stats.events_merged, stats.proposals_created) == (1, 0, 0)
    assert live_events(conn) == 2 and count(conn, "SELECT COUNT(*) FROM event_lineage") == 2


def test_conflicting_keys_are_never_scored(conn, data_dir):
    fire_a = gdacs_item(1031202, "WF", "Forest fires in Russian Federation", 60.0, 100.0, "2026-09-01T00:00:00", iso3="RUS")
    mirror_of_b = eonet_item("EONET_23559", "Wildfire in Russian Federation 1031315", "wildfires", [100.01, 60.01], "2026-09-01T05:00:00Z", sources=(gdacs_source("WF", 1031315),), magnitude=5100.0, unit="hectare")
    ingest_items(conn, data_dir, "gdacs", [fire_a], T0)
    ingest_items(conn, data_dir, "eonet", [mirror_of_b], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_merged, stats.proposals_created) == (2, 0, 0)  # 1 km apart, same hour, but EONET names another GDACS fire
    fire_b = gdacs_item(1031315, "WF", "Forest fires in Russian Federation", 60.02, 100.02, "2026-09-01T00:00:00", iso3="RUS")
    ingest_items(conn, data_dir, "gdacs", [fire_b], T1)
    again = resolve.resolve(conn)
    assert (again.events_created, again.events_linked) == (0, 1) and live_events(conn) == 2
    mirrored = conn.execute("SELECT e.* FROM event e JOIN source_record r ON r.event_id = e.event_id WHERE r.external_id = '1031315'").fetchone()
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE event_id = ?", mirrored["event_id"]) == 2


def test_revert_guards(conn, data_dir):
    gdacs_storm, eonet_storm = storm_pair()
    ingest_items(conn, data_dir, "gdacs", [gdacs_storm], T0)
    ingest_items(conn, data_dir, "eonet", [eonet_storm], T0)
    resolve.resolve(conn)
    lineage_id = conn.execute("SELECT lineage_id FROM event_lineage").fetchone()[0]
    with conn:
        merge_mod.revert(conn, lineage_id, "human")
    for bad in (lineage_id, "01NOPE"):
        try:
            with conn:
                merge_mod.revert(conn, bad, "human")
        except merge_mod.MergeError:
            pass
        else:
            raise AssertionError("a reverted or unknown lineage row must not revert again")
    event_ids = [r[0] for r in conn.execute("SELECT event_id FROM event ORDER BY event_id")]
    try:
        with conn:
            merge_mod.merge(conn, event_ids[0], event_ids[0], "human")
    except merge_mod.MergeError:
        pass
    else:
        raise AssertionError("an event cannot be merged into itself")


# ----------------------------------------------------------------------------- the grey zone and the Review tab actions
def austrian_floods():
    gdacs_flood = gdacs_item(1104115, "FL", "Flood in Austria", 48.2, 14.3, "2026-09-01T00:00:00", iso3="AUT")
    ring = [[48.8, 15.2], [48.8, 15.4], [49.0, 15.4], [49.0, 15.2], [48.8, 15.2]]  # [lat, lon]: about 100 km away
    eonet_flood = eonet_item("EONET_90001", "Flood in Austria 999", "floods", [ring], "2026-09-03T00:00:00Z", geometry_type="Polygon")
    return gdacs_flood, eonet_flood


def test_grey_zone_writes_a_proposal_and_review_can_accept_and_revert(conn, data_dir):
    gdacs_flood, eonet_flood = austrian_floods()
    ingest_items(conn, data_dir, "gdacs", [gdacs_flood], T0)
    ingest_items(conn, data_dir, "eonet", [eonet_flood], T0)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_merged, stats.proposals_created) == (2, 0, 1)
    assert len(pins(conn)) == 2 and count(conn, "SELECT COUNT(*) FROM event_lineage") == 0

    proposals = review.open_proposals(conn)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert config.PROPOSAL_THRESHOLD <= proposal["score"] < config.AUTO_MERGE_THRESHOLD
    assert 90 < proposal["distance_km"] < 110 and proposal["days_apart"] == 2.0 and proposal["text_sim"] == 1.0
    assert proposal["event_a"]["title"] == "Flood in Austria 999" and proposal["event_a"]["source_ids"] == ["eonet"]
    assert proposal["event_b"]["title"] == "Flood in Austria" and proposal["event_b"]["source_ids"] == ["gdacs"]
    assert review.counts(conn)["open_proposals"] == 1

    lineage_id = review.accept(proposal["proposal_id"], conn)
    assert count(conn, "SELECT COUNT(*) FROM merge_proposal WHERE status = 'accepted'") == 1
    assert review.open_proposals(conn) == []
    features = pins(conn, include_footprints=True)
    points = [f for f in features if f["geometry"]["type"] == "Point"]
    polygons = [f for f in features if f["geometry"]["type"] == "Polygon"]
    assert len(points) == 1 and points[0]["properties"]["event_id"] == proposal["event_b"]["event_id"]
    assert len(polygons) == 1 and polygons[0]["properties"]["event_id"] == proposal["event_b"]["event_id"]  # the polygon moved with its record
    merges = review.recent_merges(conn)
    assert len(merges) == 1 and merges[0]["lineage_id"] == lineage_id and merges[0]["performed_by"] == "human" and merges[0]["revertable"]
    assert json.loads(conn.execute("SELECT evidence FROM event_lineage WHERE lineage_id = ?", (lineage_id,)).fetchone()[0])["proposal_id"] == proposal["proposal_id"]

    review.revert(lineage_id, conn)
    assert count(conn, "SELECT COUNT(*) FROM event_lineage") == 2
    assert count(conn, "SELECT COUNT(*) FROM event WHERE merged_into_event_id IS NOT NULL") == 0
    features = pins(conn, include_footprints=True)
    points = [f for f in features if f["geometry"]["type"] == "Point"]
    polygons = [f for f in features if f["geometry"]["type"] == "Polygon"]
    assert len(points) == 2
    assert len(polygons) == 1 and polygons[0]["properties"]["event_id"] == proposal["event_a"]["event_id"]  # and back
    assert count(conn, "SELECT COUNT(*) FROM event_geometry WHERE role = 'footprint'") == 1
    assert review.recent_merges(conn)[0]["reverted"] is True and review.recent_merges(conn)[0]["revertable"] is False
    assert count(conn, "SELECT COUNT(*) FROM merge_proposal WHERE status = 'open'") == 1  # undo puts the decision back in the queue

    review.reject(proposal["proposal_id"], conn)
    assert review.open_proposals(conn) == [] and review.counts(conn)["rejected_proposals"] == 1
    again = resolve.resolve(conn)
    assert again.proposals_created == 0 and count(conn, "SELECT COUNT(*) FROM merge_proposal") == 1


def test_later_records_follow_the_canonical_event(conn, data_dir):
    gdacs_flood, eonet_flood = austrian_floods()
    ingest_items(conn, data_dir, "gdacs", [gdacs_flood], T0)
    ingest_items(conn, data_dir, "eonet", [eonet_flood], T0)
    resolve.resolve(conn)
    proposal = review.open_proposals(conn)[0]
    review.accept(proposal["proposal_id"], conn)
    later = {**eonet_flood, "geometry": [{**eonet_flood["geometry"][0], "date": "2026-09-04T00:00:00Z"}]}
    ingest_items(conn, data_dir, "eonet", [later], T1)
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_attached) == (0, 1)
    assert len(pins(conn)) == 1
    assert count(conn, "SELECT COUNT(*) FROM source_record WHERE event_id = ?", proposal["event_b"]["event_id"]) == 3


# ----------------------------------------------------------------------------- guards
def test_same_source_glide_does_not_merge_two_cyclones(conn, data_dir):
    lala = gdacs_item(1001303, "TC", "Tropical Cyclone LALA-26", 22.0, 121.0, "2026-08-12T00:00:00", eventname="LALA-26", glide="TC-2026-000161-CHN")
    saudel = gdacs_item(1001305, "TC", "Tropical Cyclone SAUDEL-26", 23.0, 122.0, "2026-08-19T00:00:00", eventname="SAUDEL-26", glide="TC-2026-000161-CHN")
    ingest_items(conn, data_dir, "gdacs", [lala, saudel], T0)
    stats = resolve.resolve(conn)
    assert stats.events_created == 2 and stats.events_merged == 0 and stats.proposals_created == 0
    assert count(conn, "SELECT COUNT(*) FROM event WHERE glide_number = 'TC-2026-000161-CHN'") == 1  # one keeps the number, the other stays apart
    later = gdacs_item(1001305, "TC", "Tropical Cyclone SAUDEL-26", 24.0, 123.0, "2026-08-19T00:00:00", eventname="SAUDEL-26", glide="TC-2026-000161-CHN", episodeid=2, datemodified="2026-08-20T00:00:00")
    ingest_items(conn, data_dir, "gdacs", [later], T1)
    again = resolve.resolve(conn)
    assert again.events_attached == 1 and count(conn, "SELECT COUNT(*) FROM event") == 2


def test_glide_joins_across_sources(conn, data_dir):
    gdacs_flood = gdacs_item(1104081, "FL", "Flood in China", 47.2, 127.0, "2026-07-31T01:00:00", glide="FL-2026-000148-CHN", alertlevel="Orange", episodealertscore=1.5)
    ingest_items(conn, data_dir, "gdacs", [gdacs_flood], T0)
    resolve.resolve(conn)
    # a second source carrying the same GLIDE joins by the number before anything else (synthetic: no live feed does yet)
    row = conn.execute("SELECT * FROM source_record").fetchone()
    with conn:
        conn.execute(
            "INSERT INTO source_record (source_record_id, source_id, external_id, external_episode, event_id, hazard_type, title, observed_at, started_at, lat, lon, glide_number, payload, payload_hash, first_seen_at, last_seen_at) "
            "VALUES ('01SYNTHETICGLIDE0000000000', 'copernicus', 'EMSR000', '', NULL, 'flood', 'Flood in China', ?, ?, 47.3, 127.1, 'FL-2026-000148-CHN', ?, 'x', ?, ?)",
            (row["observed_at"], row["started_at"], json.dumps({"code": "EMSR000", "category": "Flood", "countries": ["China"]}), T0, T0),
        )
    stats = resolve.resolve(conn)
    assert (stats.events_created, stats.events_attached) == (0, 1) and count(conn, "SELECT COUNT(*) FROM event") == 1


def test_different_hazard_classes_never_merge(conn, data_dir):
    flood = gdacs_item(1104200, "FL", "Flood in Austria", 48.2, 14.3, "2026-09-01T00:00:00", iso3="AUT")
    fire = eonet_item("EONET_90002", "Flood in Austria", "wildfires", [14.3, 48.2], "2026-09-01T00:00:00Z", magnitude=6000.0, unit="hectare")
    ingest_items(conn, data_dir, "gdacs", [flood], T0)
    ingest_items(conn, data_dir, "eonet", [fire], T0)
    stats = resolve.resolve(conn)
    assert stats.events_created == 2 and stats.events_merged == 0 and stats.proposals_created == 0
    eonet_record = conn.execute("SELECT * FROM source_record WHERE source_id = 'eonet'").fetchone()
    assert matching.candidate_events(conn, matching.entity_from_record(eonet_record)) == []
    with conn:  # the block itself, seen from the flood: same class only
        conn.execute("UPDATE source_record SET hazard_type = 'flood' WHERE source_id = 'eonet'")
    flood_entity = matching.entity_from_record(conn.execute("SELECT * FROM source_record WHERE source_id = 'eonet'").fetchone())
    candidates = matching.candidate_events(conn, flood_entity)
    assert len(candidates) == 1 and candidates[0][1].source_ids == frozenset({"gdacs"}) and candidates[0][1].positions == ((48.2, 14.3),)


def test_far_apart_floods_stay_apart(conn, data_dir):
    a = gdacs_item(1104300, "FL", "Flood in Spain", 40.4, -3.7, "2026-09-01T00:00:00", iso3="ESP")
    b = eonet_item("EONET_90003", "Flood in Bulgaria 1104120", "floods", [25.5, 42.7], "2026-09-01T00:00:00Z")
    ingest_items(conn, data_dir, "gdacs", [a], T0)
    ingest_items(conn, data_dir, "eonet", [b], T0)
    stats = resolve.resolve(conn)
    assert stats.events_created == 2 and stats.proposals_created == 0 and len(pins(conn)) == 2


def test_blocking_excludes_events_of_the_same_source(conn, data_dir):
    first = gdacs_item(1104400, "FL", "Flood in Poland", 52.0, 21.0, "2026-09-01T00:00:00", iso3="POL")
    second = gdacs_item(1104401, "FL", "Flood in Poland", 52.1, 21.1, "2026-09-02T00:00:00", iso3="POL")
    ingest_items(conn, data_dir, "gdacs", [first, second], T0)
    stats = resolve.resolve(conn)
    assert stats.events_created == 2 and stats.events_merged == 0 and stats.proposals_created == 0
