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
