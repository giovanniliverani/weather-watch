"""`eww serve`: the read-only HTTP API the React frontend in web/ talks to (M7).

Every route wraps one eww.api function and adds no logic: filters, severity and merge pointers stay
in eww.api. There is no authentication, so it binds to 127.0.0.1 unless told otherwise. It opens
SQLite read-only and refuses a database whose schema is behind the code. Forecasts are cached in
memory only, never in SQLite.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

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


def create_app(db_path: Path | None = None, host: str = config.SERVE_HOST) -> FastAPI:
    """Build the API over the SQLite file at `db_path` (default: config.DB_PATH), answering `host` and localhost."""
    db.require_current_schema(db_path)  # serve never migrates: a database behind the code is refused here
    app = FastAPI(title="Extreme Weather Watch API", version=config.VERSION)
    app.add_middleware(CORSMiddleware, allow_origins=config.SERVE_CORS_ORIGINS, allow_methods=["GET"])

    @app.middleware("http")
    async def refuse_other_sites(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # CORS hides answers from other sites but still lets them send requests (e.g. spend the forecast budget).
        if request.headers.get("sec-fetch-site") == "cross-site" and request.headers.get("origin") not in config.SERVE_CORS_ORIGINS:
            return JSONResponse({"detail": "cross-site requests are refused"}, status_code=403)
        return await call_next(request)
    # CORS alone does not stop a page whose DNS name is rebound to 127.0.0.1; checking Host does.
    hosts = [*config.SERVE_ALLOWED_HOSTS, *([host] if host not in ("0.0.0.0", "::", "") else [])]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    def connect() -> closing[sqlite3.Connection]:
        # One read-only connection per request: FastAPI runs these handlers on worker threads.
        return closing(db.connect_readonly(db_path))

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
        except (ValueError, OverflowError) as exc:  # OverflowError: a span or limit too large for timedelta or SQLite
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

    @app.get("/hazards")
    def hazards() -> list[str]:
        """The hazard_type ids in config order, for the frontend's hazard filter."""
        return list(api.HAZARD_TYPES)

    forecasts: dict[tuple[float, float], tuple[float, dict]] = {}  # (lat, lon) -> (expires at, forecast); memory only
    forecasts_lock = threading.Lock()

    @app.get("/forecast")
    def forecast(lat: float, lon: float) -> dict:
        """Current conditions and the daily forecast at one point, cached for api.FORECAST_CACHE_TTL_S."""
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):  # also refuses NaN
            raise HTTPException(status_code=400, detail="lat must be within -90..90 and lon within -180..180")
        key = (round(lat, 4), round(lon, 4))
        # Held across the upstream call: one Open-Meteo request at a time, so concurrent misses on a point
        # share one answer and the per-call rate limiters (rebuilt from the provider log) see every call.
        with forecasts_lock:
            now = time.monotonic()
            for stale in [k for k, (expires, _) in forecasts.items() if expires <= now]:
                del forecasts[stale]
            if key in forecasts:
                return forecasts[key][1]
            try:
                result = api.forecast(*key)
            except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:  # transport, status or rate limit, not JSON, odd shape
                raise HTTPException(status_code=502, detail=f"Open-Meteo unavailable: {exc}") from exc
            forecasts[key] = (time.monotonic() + api.FORECAST_CACHE_TTL_S, result)
            return result

    @app.get("/health")
    def health() -> dict:
        """Data freshness and the collector heartbeat (the contract's meta without filters)."""
        with connect() as conn:
            return api.heartbeat_meta(conn=conn)

    return app
