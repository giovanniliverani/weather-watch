"""The exporter's output carries exactly the contract's keys (golden example: tests/fixtures/events.geojson)."""

import json

from typer.testing import CliRunner

from eww import api, resolve
from eww.cli import app
from tests.conftest import load_json
from tests.test_ingest_resolve import ingest_fixtures

SINCE = "2026-01-01T00:00:00Z"


def golden():
    collection = load_json("events.geojson")
    features = collection["features"]
    event_keys = set(next(f for f in features if f["geometry"]["type"] == "Point")["properties"])
    footprint_keys = set(next(f for f in features if f["geometry"]["type"] == "Polygon")["properties"])
    return set(collection["meta"]), set(collection["meta"]["filters_applied"]), event_keys, footprint_keys


def prepared(conn, data_dir):
    ingest_fixtures(conn, data_dir)
    resolve.resolve(conn)
    return conn


def test_export_matches_the_contract_keys(conn, data_dir):
    prepared(conn, data_dir)
    meta_keys, filter_keys, event_keys, footprint_keys = golden()
    collection = api.events_geojson(SINCE, include_footprints=True, conn=conn)
    assert collection["type"] == "FeatureCollection"
    assert set(collection["meta"]) == meta_keys == set(api.META_KEYS)
    assert set(collection["meta"]["filters_applied"]) == filter_keys
    points = [f for f in collection["features"] if f["geometry"]["type"] == "Point"]
    polygons = [f for f in collection["features"] if f["geometry"]["type"] != "Point"]
    assert points and polygons
    for feature in points:
        assert set(feature["properties"]) == event_keys == set(api.EVENT_PROPERTIES)
        lon, lat = feature["geometry"]["coordinates"]
        assert -180 <= lon <= 180 and -90 <= lat <= 90
        assert feature["properties"]["source_ids"] in (["eonet"], ["gdacs"])
        assert feature["properties"]["ems_activation"] is False
        assert feature["properties"]["precision"] in ("exact", "admin1")
        assert feature["properties"]["detail_url"].startswith("https://")
    for feature in polygons:
        assert set(feature["properties"]) == footprint_keys == set(api.FOOTPRINT_PROPERTIES)
        assert feature["properties"]["role"] == "footprint"
        assert feature["properties"]["event_id"] in {f["properties"]["event_id"] for f in points}
    json.dumps(collection)  # serialisable


def test_pin_count_equals_the_sql_count(conn, data_dir):
    prepared(conn, data_dir)
    collection = api.events_geojson(SINCE, conn=conn)
    assert len(collection["features"]) == api.count_in_window(conn, SINCE) == 15


def test_filters_are_applied_inside_the_function(conn, data_dir):
    prepared(conn, data_dir)
    floods = api.events_geojson(SINCE, hazard=["flood"], conn=conn)["features"]
    assert floods and {f["properties"]["hazard_type"] for f in floods} == {"flood"}
    assert api.events_geojson(SINCE, hazard="tsunami", conn=conn)["features"] == []
    orange = api.events_geojson(SINCE, min_severity=0.6, conn=conn)["features"]
    assert [f["properties"]["severity_label"] for f in orange] == ["Orange"]
    active = api.events_geojson(SINCE, status="active", conn=conn)["features"]
    assert active and all(f["properties"]["ended_at"] is None for f in active)
    europe = api.events_geojson(SINCE, bbox=[-10, 35, 30, 60], conn=conn)["features"]
    assert europe and all(-10 <= f["geometry"]["coordinates"][0] <= 30 for f in europe)
    assert api.events_geojson("2027-01-01T00:00:00Z", conn=conn)["features"] == []
    assert len(api.events_geojson(SINCE, limit=2, conn=conn)["features"]) == 2
    assert len(api.events_geojson(SINCE, limit=0, conn=conn)["features"]) == 15
    relative = api.events_geojson("36500d", conn=conn)
    assert relative["meta"]["filters_applied"]["since"].endswith("Z")


def test_cli_export_prints_json(tmp_path, data_dir):
    from eww import db

    db_path = tmp_path / "cli.sqlite"
    connection = db.connect(db_path)
    db.init_db(connection)
    prepared(connection, data_dir)
    connection.close()
    runner = CliRunner()
    result = runner.invoke(app, ["--db", str(db_path), "export", "--since", SINCE])
    assert result.exit_code == 0, result.output
    collection = json.loads(result.stdout.strip().splitlines()[-1])
    assert collection["type"] == "FeatureCollection" and len(collection["features"]) == 15
    doctor = runner.invoke(app, ["--db", str(db_path), "doctor"])
    assert doctor.exit_code == 0, doctor.output
    assert "duplicate source_record keys (source_id, external_id, external_episode): 0" in doctor.stdout
    assert "unresolved source_record rows (event_id IS NULL): 0" in doctor.stdout
