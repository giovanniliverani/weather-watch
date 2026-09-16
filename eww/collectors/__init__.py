"""Spine collectors: one module per authoritative feed.

Every collector module exposes the same surface, so ingest, resolve and the API never branch
on the source name:

    fetch(since, until) -> FetchResult          raw items exactly as the feed returned them
    iter_records(item) -> list[dict]            one dict per source_record the item yields
    normalise(item, ...) -> dict                the source_record columns for one observation
    country_iso3(payload) -> str | None         derived from a stored payload, for event.country_iso3
    detail_url(payload) -> str | None           the source's own page for the event
    footprint(payload) -> dict | None           a GeoJSON Polygon when the source supplies one
"""

from __future__ import annotations

from types import ModuleType

from eww.collectors import eonet, gdacs
from eww.collectors.base import FetchResult

COLLECTORS: dict[str, ModuleType] = {"gdacs": gdacs, "eonet": eonet}

__all__ = ["COLLECTORS", "FetchResult", "get"]



def get(source_id: str) -> ModuleType:
    try:
        return COLLECTORS[source_id]
    except KeyError as exc:
        raise KeyError(f"unknown source {source_id!r}; known: {sorted(COLLECTORS)}") from exc
