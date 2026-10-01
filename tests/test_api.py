"""The exporter's output carries exactly the contract's keys (golden example: tests/fixtures/events.geojson)."""

import json
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from eww import api, resolve
from eww.cli import app
from eww.clock import parse_iso, to_iso
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
        iso3, name = feature["properties"]["country_iso3"], feature["properties"]["country_name"]
        assert (name is None) if iso3 is None else (isinstance(name, str) and name)
    assert any(f["properties"]["country_name"] for f in points)
    for feature in polygons:
        assert set(feature["properties"]) == footprint_keys == set(api.FOOTPRINT_PROPERTIES)
        assert feature["properties"]["role"] == "footprint"
        assert feature["properties"]["event_id"] in {f["properties"]["event_id"] for f in points}
    json.dumps(collection)  # serialisable


def test_severity_steps_follow_the_config_bands():
    steps = api.severity_steps()
    assert [s["label"] for s in steps] == ["Any", "Green", "Orange", "Red"]
    assert [s["value"] for s in steps] == [0.0, 0.33, 0.66, 1.0]
    orange = steps[2]["hint"]
    assert "0.66" in orange and "Orange" in orange and "Red" in orange and "EMS activation" in orange
    assert "EMS activation" not in steps[3]["hint"]  # the EMS floor (0.66) is below Red


def test_severity_steps_change_with_config(monkeypatch):
    monkeypatch.setattr(api.config, "GDACS_SEVERITY", {"Green": 0.2, "Orange": 0.5, "Red": 0.9})
    monkeypatch.setattr(api.config, "EMS_SEVERITY_FLOOR", 0.9)
    steps = api.severity_steps()
    assert [s["value"] for s in steps] == [0.0, 0.2, 0.5, 0.9]
    assert all("EMS activation" in s["hint"] for s in steps[1:])


def test_country_name_comes_from_the_geonames_table():
    assert api.countries.name_for("HRV") == "Croatia"
    assert api.countries.name_for(None) is None and api.countries.name_for("XXX") is None


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


def test_out_of_range_filters_raise(conn, data_dir):
    prepared(conn, data_dir)
    for kwargs in ({"limit": -1}, {"min_severity": float("nan")}, {"min_severity": 1.5}, {"min_severity": -0.1}, {"bbox": [0, 0, float("inf"), 1]}, {"bbox": [float("nan"), 0, 1, 1]}):
        with pytest.raises(ValueError):
            api.events_geojson(SINCE, conn=conn, **kwargs)
    assert api.events_geojson(SINCE, min_severity=1.0, limit=None, conn=conn)["type"] == "FeatureCollection"


@pytest.mark.parametrize(
    ("hours_ago", "missed", "stale"),
    [
        (api.STATUS_RED_STALE_HOURS, api.STATUS_RED_MISSED_RUNS, False),
        (api.STATUS_RED_STALE_HOURS + 0.01, 0, True),
        (0, api.STATUS_RED_MISSED_RUNS + 1, True),
        (None, 0, True),
    ],
)
def test_pipeline_stale_follows_the_status_strip_rule(conn, monkeypatch, hours_ago, missed, stale):
    now = parse_iso("2026-09-16T12:00:00Z")
    last_run = None if hours_ago is None else to_iso(now - timedelta(hours=hours_ago))
    monkeypatch.setattr(api.heartbeat, "summary", lambda conn, now: {"last_collector_run_at": last_run, "missed_runs_7d": missed, "expected_runs_7d": 56})
    assert api.heartbeat_meta(conn=conn, now=now)["pipeline_stale"] is stale
    assert api.events_geojson(SINCE, conn=conn, now=now)["meta"]["pipeline_stale"] is stale


def test_cli_export_reports_bad_filters_without_a_traceback(tmp_path):
    result = CliRunner().invoke(app, ["--db", str(tmp_path / "x.sqlite"), "export", "--limit", "-1"])
    assert result.exit_code == 2
    assert "limit must be 0" in result.output
    assert "Traceback" not in result.output
