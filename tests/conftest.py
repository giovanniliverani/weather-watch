"""Shared fixtures: a temporary SQLite database with the schema applied, and a temporary data dir."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eww import config, db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.sqlite")
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def data_dir(tmp_path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def gdacs_features() -> list[dict]:
    return load_json("gdacs_features.json")["features"]


def eonet_events() -> list[dict]:
    return load_json("eonet_events.json")["events"]


def envelope(source_id: str, items: list[dict], started_at: str, run_id: str = "01TESTRUN000000000000000AA") -> dict:
    """A snapshot envelope in exactly the shape eww.collect writes."""
    return {
        "format": config.SNAPSHOT_FORMAT,
        "source_id": source_id,
        "run_id": run_id,
        "scheduled_for": started_at[:11] + "15:00:00Z",
        "started_at": started_at,
        "finished_at": started_at,
        "since": "2026-08-17T00:00:00Z",
        "until": "2026-09-16T00:00:00Z",
        "status": "ok",
        "http_status": 200,
        "error": None,
        "requests": [],
        "items_seen": len(items),
        "items": items,
    }


def copernicus_activations() -> list[dict]:
    return load_json("copernicus_activations.json")["results"]


# --------------------------------------------------------------------------- synthetic feed items for identity tests
ALERT_SCORE = {"Green": 1, "Orange": 2, "Red": 3}


def gdacs_item(
    eventid: int,
    eventtype: str,
    name: str,
    lat: float,
    lon: float,
    fromdate: str,
    *,
    todate: str | None = None,
    alertlevel: str = "Green",
    episodealertscore: float = 0.5,
    eventname: str = "",
    glide: str = "",
    iscurrent: str = "true",
    iso3: str = "",
    episodeid: int = 1,
    datemodified: str | None = None,
    bbox: list | None = None,
) -> dict:
    """A GDACS SEARCH Feature with the fields normalise() reads."""
    return {
        "type": "Feature",
        "bbox": bbox or [lon, lat, lon, lat],
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "eventtype": eventtype,
            "eventid": eventid,
            "episodeid": episodeid,
            "eventname": eventname,
            "glide": glide,
            "name": name,
            "description": name,
            "alertlevel": alertlevel,
            "alertscore": ALERT_SCORE[alertlevel],
            "episodealertlevel": alertlevel,
            "episodealertscore": episodealertscore,
            "iscurrent": iscurrent,
            "country": "",
            "iso3": iso3,
            "fromdate": fromdate,
            "todate": todate or fromdate,
            "datemodified": datemodified or todate or fromdate,
            "severitydata": {"severity": 0, "severitytext": "", "severityunit": ""},
            "url": {"report": f"https://www.gdacs.org/report.aspx?eventtype={eventtype}&eventid={eventid}", "details": None, "geometry": None},
        },
    }


def gdacs_source(eventtype: str, eventid: int) -> tuple[str, str]:
    """The (id, url) pair EONET puts in sources[] for an event mirrored from GDACS."""
    return ("GDACS", f"https://www.gdacs.org/report.aspx?eventtype={eventtype}&eventid={eventid}")


def eonet_item(
    event_id: str,
    title: str,
    category: str,
    coordinates,
    date: str,
    *,
    sources: tuple = (),
    closed: str | None = None,
    magnitude: float | None = None,
    unit: str | None = None,
    geometry_type: str = "Point",
) -> dict:
    """An EONET v3 event with one geometry entry. Polygon coordinates are given in EONET's [lat, lon] order."""
    return {
        "id": event_id,
        "title": title,
        "description": None,
        "link": f"https://eonet.gsfc.nasa.gov/api/v3/events/{event_id}",
        "closed": closed,
        "categories": [{"id": category, "title": category}],
        "sources": [{"id": source_id, "url": url} for source_id, url in sources],
        "geometry": [{"magnitudeValue": magnitude, "magnitudeUnit": unit, "date": date, "type": geometry_type, "coordinates": coordinates}],
    }


def copernicus_item(
    code: str,
    name: str,
    category: str,
    lon: float,
    lat: float,
    event_time: str,
    activation_time: str,
    *,
    gdacs_id: str | None = None,
    closed: bool = True,
    countries: tuple = ("Spain",),
    last_update: str | None = None,
) -> dict:
    return {
        "code": code,
        "countries": list(countries),
        "eventTime": event_time,
        "name": name,
        "centroid": f"POINT ({lon} {lat})",
        "activationTime": activation_time,
        "category": category,
        "lastUpdate": last_update or activation_time,
        "closed": closed,
        "gdacsId": gdacs_id,
        "n_aois": 1,
        "n_products": 1,
    }


def ingest_items(conn, data_dir: Path, source_id: str, items: list[dict], stamp: str, run_id: str | None = None):
    """Write one snapshot for `source_id` and ingest it; returns the IngestStats."""
    from eww import ingest, snapshots

    run_id = run_id or f"01TESTRUN{stamp.replace('-', '').replace(':', '').replace('T', '').replace('Z', '')[:14]}{source_id[:3].upper()}".ljust(26, "0")[:26]
    path = snapshots.write(envelope(source_id, items, stamp, run_id), data_dir)
    return ingest.ingest_snapshot(conn, path, data_dir=data_dir)
