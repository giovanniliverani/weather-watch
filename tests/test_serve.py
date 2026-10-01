"""`eww serve` returns exactly what eww.api returns, over a temporary SQLite file."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eww import api, config
from eww.clock import parse_iso
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
    [{"since": "not-a-date"}, {"bbox": "1,2,3"}, {"bbox": "a,b,c,d"}, {"since": "99999999999d"}, {"limit": "99999999999999999999"}],
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
