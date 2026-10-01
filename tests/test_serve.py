"""`eww serve` returns exactly what eww.api returns, over a temporary SQLite file."""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path, PurePosixPath

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from eww import api, config, db, weather
from eww.cli import app as cli_app
from eww.clock import parse_iso
from eww.ratelimit import RateLimitExceeded
from eww.serve import create_app
from tests.test_api import prepared

NOW = "2026-09-16T15:10:00Z"  # the fixtures' collection time, so relative windows select rows
DEV_ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(conn, data_dir, tmp_path, monkeypatch) -> TestClient:
    prepared(conn, data_dir)
    monkeypatch.setattr(api, "now_utc", lambda: parse_iso(NOW))
    return TestClient(create_app(tmp_path / "test.sqlite"), base_url="http://127.0.0.1:8000")


def without_generated_at(collection: dict) -> dict:
    return {**collection, "meta": {k: v for k, v in collection["meta"].items() if k != "generated_at"}}


def test_events_parity_with_the_api(client, conn):
    response = client.get("/events.geojson", params={"since": "7d", "hazard": "flood", "min_severity": 0.66})
    assert response.status_code == 200
    expected = api.events_geojson("7d", hazard="flood", min_severity=0.66, conn=conn)
    assert expected["features"]
    assert without_generated_at(response.json()) == without_generated_at(expected)


def test_events_repeated_params_bbox_and_footprints(client, conn):
    params = {"since": "36500d", "hazard": ["flood", "wildfire"], "status": ["active"], "bbox": "-180,-90,180,90", "include_footprints": "true"}
    body = client.get("/events.geojson", params=params).json()
    expected = api.events_geojson("36500d", hazard=["flood", "wildfire"], status=["active"], bbox=[-180, -90, 180, 90], include_footprints=True, conn=conn)
    assert without_generated_at(body) == without_generated_at(expected)
    assert body["meta"]["filters_applied"]["hazard"] == ["flood", "wildfire"]


def test_limit_zero_means_everything_in_the_window(client, conn):
    body = client.get("/events.geojson", params={"since": "36500d", "limit": 0}).json()
    assert len(body["features"]) == api.count_in_window(conn, "2000-01-01T00:00:00Z") == 15
    assert body["meta"]["filters_applied"]["limit"] == 0
    assert len(client.get("/events.geojson", params={"since": "36500d", "limit": 2}).json()["features"]) == 2


@pytest.mark.parametrize(
    "params",
    [
        {"since": "not-a-date"},
        {"bbox": "1,2,3"},
        {"bbox": "a,b,c,d"},
        {"bbox": "nan,0,1,1"},
        {"since": "99999999999d"},
        {"limit": "99999999999999999999"},
        {"limit": "-1"},
        {"min_severity": "nan"},
        {"min_severity": "1.5"},
    ],
)
def test_events_bad_input_is_400(client, params):
    response = client.get("/events.geojson", params=params)
    assert response.status_code == 400
    assert response.json()["detail"]


def test_event_documents(client, conn):
    event_id = conn.execute("SELECT event_id FROM event LIMIT 1").fetchone()[0]
    response = client.get(f"/events/{event_id}/documents")
    assert response.status_code == 200
    assert response.json() == api.event_documents(event_id, conn=conn)


def test_merged_event_id_reads_the_canonical_event(client, conn):
    merged, canonical = [row[0] for row in conn.execute("SELECT event_id FROM event ORDER BY event_id LIMIT 2")]
    conn.execute("UPDATE event SET merged_into_event_id = ?, status = 'merged' WHERE event_id = ?", (canonical, merged))
    conn.commit()
    response = client.get(f"/events/{merged}/documents")
    assert response.status_code == 200
    assert response.json() == api.event_documents(canonical, conn=conn)


def test_unknown_event_is_404(client):
    assert client.get("/events/NOSUCHEVENT/documents").status_code == 404


def test_attributions(client, conn):
    response = client.get("/attributions")
    assert response.status_code == 200
    assert response.json() == api.attributions(conn=conn)


def test_health_is_the_heartbeat_meta(client):
    body = client.get("/health").json()
    assert set(body) == set(api.META_KEYS) - {"filters_applied"}
    assert body["generated_at"] == NOW
    assert body["data_as_of"]


def test_hazards_are_the_config_list_in_order(client):
    assert client.get("/hazards").json() == list(config.HAZARD_TYPES)
    assert client.get("/hazards", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://example.com"}).status_code == 403


def test_health_carries_pipeline_stale(client):
    assert isinstance(client.get("/health").json()["pipeline_stale"], bool)


# --------------------------------------------------------------------------- read-only database
def test_serve_never_writes_the_database(conn, data_dir, tmp_path, monkeypatch):
    prepared(conn, data_dir)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    path = tmp_path / "test.sqlite"
    versions, mtime = _versions(path), path.stat().st_mtime_ns
    monkeypatch.setattr(api, "now_utc", lambda: parse_iso(NOW))
    client = TestClient(create_app(path), base_url="http://127.0.0.1:8000")
    event_id = client.get("/events.geojson", params={"since": "36500d"}).json()["features"][0]["properties"]["event_id"]
    for route in ("/health", "/attributions", "/hazards", f"/events/{event_id}/documents"):
        assert client.get(route).status_code == 200
    assert _versions(path) == versions
    assert path.stat().st_mtime_ns == mtime
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.connect_readonly(path).execute("DELETE FROM event")


def _versions(path) -> list[tuple]:
    with closing(db.connect_readonly(path)) as reader:
        return [tuple(row) for row in reader.execute("SELECT version, applied_at FROM schema_version ORDER BY version")]


def test_outdated_schema_is_refused(conn, tmp_path):
    conn.execute("UPDATE schema_version SET version = 3")
    conn.commit()
    path = tmp_path / "test.sqlite"
    with pytest.raises(RuntimeError, match=rf"at schema 3, this code needs {db.SCHEMA_VERSION}: run `uv run eww init-db`"):
        create_app(path)
    result = CliRunner().invoke(cli_app, ["--db", str(path), "serve"])
    assert result.exit_code == 1
    assert "at schema 3" in result.output and "Traceback" not in result.output
    assert _versions(path)[-1][0] == 3  # nothing migrated it


def test_readonly_uri_handles_drives_shares_and_odd_names():
    assert db.readonly_uri(Path("C:/data/città #1 %.sqlite")) == "file:///C:/data/citt%C3%A0%20%231%20%25.sqlite?mode=ro"
    assert db.readonly_uri(Path("//server/share/eww.sqlite")) == "file:////server/share/eww.sqlite?mode=ro"
    assert db.readonly_uri(PurePosixPath("/home/me/eww.sqlite")) == "file:///home/me/eww.sqlite?mode=ro"


def test_odd_path_opens_read_only(conn, tmp_path):
    odd = tmp_path / "città #1 %" / "eww.sqlite"
    odd.parent.mkdir()
    with closing(db.connect(odd)) as writer:
        db.init_db(writer)
    with closing(db.connect_readonly(odd)) as reader:
        assert db.schema_version(reader) == db.SCHEMA_VERSION


def test_a_file_that_is_not_a_database_is_refused(tmp_path):
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"not a database" * 100)
    with pytest.raises(RuntimeError, match="cannot read .* as an eww database"):
        create_app(junk)
    result = CliRunner().invoke(cli_app, ["--db", str(junk), "serve"])
    assert result.exit_code == 1 and "Traceback" not in result.output


def test_newer_schema_starts_with_a_warning(conn, tmp_path, caplog):
    conn.execute("UPDATE schema_version SET version = ?", (db.SCHEMA_VERSION + 1,))
    conn.commit()
    with caplog.at_level("WARNING", logger="eww.db"):
        create_app(tmp_path / "test.sqlite")
    assert f"at schema {db.SCHEMA_VERSION + 1}, newer than this code's {db.SCHEMA_VERSION}" in caplog.text


def test_missing_database_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="no database at"):
        create_app(tmp_path / "absent.sqlite")
    assert not (tmp_path / "absent.sqlite").exists()


def test_reads_while_a_writer_commits_in_wal_mode(client, tmp_path):
    path = tmp_path / "test.sqlite"
    writer = db.connect(path)
    writer.execute("CREATE TABLE scratch (n INTEGER)")
    writer.commit()
    stop = threading.Event()

    def write() -> None:
        with closing(db.connect(path)) as own:  # a connection belongs to the thread that opened it
            for n in range(300):
                own.execute("INSERT INTO scratch VALUES (?)", (n,))
                own.commit()
        stop.set()

    thread = threading.Thread(target=write)
    thread.start()
    statuses = []
    while not stop.is_set():
        statuses.append(client.get("/health").status_code)
    thread.join()
    writer.execute("BEGIN")
    writer.execute("INSERT INTO scratch VALUES (-1)")  # an open write transaction must not block readers
    statuses.append(client.get("/health").status_code)
    writer.rollback()
    writer.close()
    assert statuses and set(statuses) == {200}


def test_cors_allows_only_the_dev_server(client):
    assert DEV_ORIGIN in config.SERVE_CORS_ORIGINS
    allowed = client.get("/health", headers={"Origin": DEV_ORIGIN})
    assert allowed.headers["access-control-allow-origin"] == DEV_ORIGIN
    other = client.get("/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in other.headers


def test_only_get_is_served(client):
    assert client.post("/health").status_code == 405
    get = client.options("/health", headers={"Origin": DEV_ORIGIN, "Access-Control-Request-Method": "GET"})
    assert get.status_code == 200 and get.headers["access-control-allow-origin"] == DEV_ORIGIN
    post = client.options("/health", headers={"Origin": DEV_ORIGIN, "Access-Control-Request-Method": "POST"})
    assert post.status_code == 400


def test_foreign_host_header_is_refused(client):
    assert client.get("/health", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/health", headers={"Host": "evil.example.com"}).status_code == 400


# --------------------------------------------------------------------------- /forecast
FAKE_FORECAST = {"attribution": "test", "current": {"temperature_c": 20.0}, "daily": []}


@pytest.fixture
def forecast_calls(monkeypatch) -> list[tuple[float, float]]:
    calls: list[tuple[float, float]] = []

    def fake(latitude: float, longitude: float) -> dict:
        calls.append((latitude, longitude))
        return FAKE_FORECAST

    monkeypatch.setattr(weather, "forecast", fake)
    return calls


def test_forecast_is_cached_on_rounded_coordinates(client, forecast_calls):
    first = client.get("/forecast", params={"lat": 45.12341, "lon": 9.18})
    second = client.get("/forecast", params={"lat": 45.12344, "lon": 9.180001})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == FAKE_FORECAST
    assert forecast_calls == [(45.1234, 9.18)]


def test_forecast_cache_expires(client, forecast_calls, monkeypatch):
    monkeypatch.setattr(api, "FORECAST_CACHE_TTL_S", 0)
    client.get("/forecast", params={"lat": 1, "lon": 2})
    client.get("/forecast", params={"lat": 1, "lon": 2})
    assert len(forecast_calls) == 2


@pytest.mark.parametrize("params", [{"lat": 91, "lon": 0}, {"lat": 0, "lon": -180.5}, {"lat": "nan", "lon": 0}])
def test_forecast_out_of_range_is_400(client, forecast_calls, params):
    assert client.get("/forecast", params=params).status_code == 400
    assert forecast_calls == []


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("slow"), RuntimeError("open-meteo status 500"), RateLimitExceeded("budget")])
def test_forecast_upstream_failure_is_502(client, monkeypatch, error):
    def fail(latitude: float, longitude: float) -> dict:
        raise error

    monkeypatch.setattr(weather, "forecast", fail)
    response = client.get("/forecast", params={"lat": 1, "lon": 2})
    assert response.status_code == 502
    assert "Open-Meteo" in response.json()["detail"]


def test_concurrent_forecast_misses_make_one_upstream_call(client, monkeypatch):
    calls: list[tuple[float, float]] = []

    def slow(latitude: float, longitude: float) -> dict:
        calls.append((latitude, longitude))
        time.sleep(0.2)
        return FAKE_FORECAST

    monkeypatch.setattr(weather, "forecast", slow)
    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(lambda _: client.get("/forecast", params={"lat": 1, "lon": 2}).status_code, range(5)))
    assert statuses == [200] * 5
    assert calls == [(1.0, 2.0)]


def test_forecast_malformed_reply_is_502(client, monkeypatch):
    def odd(latitude: float, longitude: float) -> dict:
        raise TypeError("float() argument must be a string or a real number, not 'list'")

    monkeypatch.setattr(weather, "forecast", odd)
    assert client.get("/forecast", params={"lat": 1, "lon": 2}).status_code == 502


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Sec-Fetch-Site": "cross-site", "Origin": "https://example.com"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Sec-Fetch-Site": "cross-site", "Origin": DEV_ORIGIN}, 200),
        ({"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:5173"}, 200),
        ({"Sec-Fetch-Site": "same-origin"}, 200),
        ({"Sec-Fetch-Site": "none"}, 200),
        ({}, 200),
    ],
)
def test_cross_site_requests_are_refused(client, headers, status):
    assert client.get("/health", headers=headers).status_code == status
