"""Spine collectors: one module per authoritative feed.

Every collector module exposes the same surface, so ingest, resolve and the API never branch
on the source name:

    fetch(since, until) -> FetchResult          raw items exactly as the feed returned them
    iter_records(item) -> list[dict]            one dict per source_record the item yields
    normalise(item, ...) -> dict                the source_record columns for one observation
    severity(payload) -> (label, score)         the source's own severity (eww.severity holds the rules)
    country_iso3(payload) -> str | None         derived from a stored payload, for event.country_iso3
    detail_url(payload) -> str | None           the source's own page for the event
    footprint(payload) -> dict | None           a GeoJSON Polygon when the source supplies one
    linked_ids(payload) -> [(source_id, external_id)]   deterministic cross-source keys the item carries
    storm_name(payload) -> str | None           the named storm, raw; eww.matching normalises it
    LINK_TARGETS: tuple[str, ...]               which sources linked_ids() can point at
    FOOTPRINT_PRECISION: str                    event_geometry.precision for the module's footprints
"""

from __future__ import annotations

from types import ModuleType

from eww.collectors import copernicus, eonet, gdacs
from eww.collectors.base import FetchResult

COLLECTORS: dict[str, ModuleType] = {"gdacs": gdacs, "eonet": eonet, "copernicus": copernicus}
LINKING_SOURCES: list[str] = [source_id for source_id, module in COLLECTORS.items() if module.LINK_TARGETS]

__all__ = ["COLLECTORS", "LINKING_SOURCES", "FetchResult", "get"]


def get(source_id: str) -> ModuleType:
    try:
        return COLLECTORS[source_id]
    except KeyError as exc:
        raise KeyError(f"unknown source {source_id!r}; known: {sorted(COLLECTORS)}") from exc
