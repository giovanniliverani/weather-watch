"""`eww serve`: the read-only HTTP API the React frontend in web/ talks to (M7).

Every route wraps one eww.api function and adds no logic: filters, severity and merge pointers stay
in eww.api. There is no authentication, so it binds to 127.0.0.1 unless told otherwise.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from eww import api, config, db


def parse_bbox(value: str | None) -> list[float] | None:
    """Turn 'min_lon,min_lat,max_lon,max_lat' into four floats; raise ValueError otherwise."""
    if value is None:
        return None
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must be min_lon,min_lat,max_lon,max_lat")
    try:
        return [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("bbox must be four comma-separated numbers") from exc


def create_app(db_path: Path | None = None) -> FastAPI:
    """Build the API over the SQLite file at `db_path` (default: config.DB_PATH)."""
    app = FastAPI(title="Extreme Weather Watch API", version=config.VERSION)
    app.add_middleware(CORSMiddleware, allow_origins=config.SERVE_CORS_ORIGINS, allow_methods=["GET"])

    def connect() -> closing[sqlite3.Connection]:
        # One connection per request: FastAPI runs these handlers on worker threads.
        return closing(db.connect(db_path))

    @app.get("/events.geojson")
    def events_geojson(
        since: str = f"{config.DEFAULT_VIEWER_DAYS}d",
        until: str | None = None,
        hazard: Annotated[list[str] | None, Query()] = None,
        min_severity: float = 0.0,
        status: Annotated[list[str] | None, Query()] = None,
        bbox: str | None = None,
        include_footprints: bool = False,
        limit: int = api.DEFAULT_LIMIT,
    ) -> dict:
        """The contract's FeatureCollection; `limit=0` returns everything in the window."""
        try:
            with connect() as conn:
                return api.events_geojson(since, until, hazard, min_severity, status, parse_bbox(bbox), include_footprints, limit, conn=conn)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/events/{event_id}/documents")
    def event_documents(event_id: str) -> list[dict]:
        """The event's attached documents, posts and videos, syndicated copies collapsed."""
        with connect() as conn:
            if not api.event_exists(event_id, conn=conn):
                raise HTTPException(status_code=404, detail=f"no event {event_id}")
            return api.event_documents(event_id, conn=conn)

    @app.get("/attributions")
    def attributions() -> list[dict]:
        """Every source and service the map shows, with its credit line and terms page."""
        with connect() as conn:
            return api.attributions(conn=conn)

    @app.get("/health")
    def health() -> dict:
        """Data freshness and the collector heartbeat (the contract's meta without filters)."""
        with connect() as conn:
            return api.heartbeat_meta(conn=conn)

    return app
