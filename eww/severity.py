"""Severity normalisation: one function for every source (docs/architecture.md §3).

    normalise(records) -> Severity(label, score, ems_activation)

* GDACS: Green / Orange / Red -> 0.33 / 0.66 / 1.0 (config.GDACS_SEVERITY). Inside a band the
  continuous `episodealertscore` breaks ties by adding at most config.GDACS_TIEBREAK_SPAN, so an
  event never crosses into the next band and Red stays exactly 1.0.
* EONET: `magnitudeValue` scaled per unit through the piecewise-linear points in
  config.EONET_MAGNITUDE_SCALE (knots, hectares; acres are converted), else config.EONET_SEVERITY.
* Copernicus EMS: an activation carries no scale of its own; it sets `ems_activation` and raises
  the event's score to at least config.EMS_SEVERITY_FLOOR.

The label keeps the primary source's own wording ("Orange", "45 kts", "EMS activation"). Which
source is primary follows config.PRIMARY_SOURCE_ORDER: the first source present whose latest
record yields a score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from eww import config


@dataclass(frozen=True)
class Severity:
    label: str | None
    score: float | None
    ems_activation: bool = False


# ----------------------------------------------------------------------------- GDACS
def gdacs_severity(props: Mapping) -> tuple[str | None, float | None]:
    """('Orange', 0.675): the alert level as GDACS says it, plus the in-band tie-break."""
    level = str(props.get("alertlevel") or "").strip().title() or None
    if level is None or level not in config.GDACS_SEVERITY:
        return level, None
    base = config.GDACS_SEVERITY[level]
    tiebreak = _number(props.get("episodealertscore"))
    if tiebreak is None:
        tiebreak = _number(props.get("alertscore"))
    fraction = 0.0 if tiebreak is None else min(max(tiebreak / config.GDACS_ALERTSCORE_MAX, 0.0), 1.0)
    return level, round(min(1.0, base + config.GDACS_TIEBREAK_SPAN * fraction), 3)


# ----------------------------------------------------------------------------- EONET
def eonet_severity(geometry: Mapping | None) -> tuple[str | None, float]:
    """The magnitude as a label ('5747 hectare') and its per-unit score; 0.4 when there is none."""
    geometry = geometry or {}
    value, unit = _number(geometry.get("magnitudeValue")), geometry.get("magnitudeUnit")
    if value is None:
        return None, config.EONET_SEVERITY
    label = f"{value:g} {unit}".strip() if unit else f"{value:g}"
    score = scale_magnitude(value, unit)
    return label, config.EONET_SEVERITY if score is None else score


def scale_magnitude(value: float, unit: str | None) -> float | None:
    """Piecewise-linear interpolation of config.EONET_MAGNITUDE_SCALE; None for an unknown unit."""
    unit_key = str(unit or "").strip().lower()
    if unit_key in config.EONET_UNIT_TO_HECTARE:
        value = value * config.EONET_UNIT_TO_HECTARE[unit_key]
        unit_key = "hectare"
    points = config.EONET_MAGNITUDE_SCALE.get(unit_key)
    if not points:
        return None
    floor, ceiling = config.EONET_SEVERITY_FLOOR, 1.0
    if value <= points[0][0]:
        return round(max(floor, points[0][1]), 3)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            score = y0 + (y1 - y0) * (value - x0) / (x1 - x0)
            return round(min(ceiling, max(floor, score)), 3)
    return round(min(ceiling, max(floor, points[-1][1])), 3)


# ----------------------------------------------------------------------------- Copernicus
def copernicus_severity(payload: Mapping) -> tuple[str | None, float | None]:
    """An activation has no scale of its own; normalise() applies the EMS floor at event level."""
    return None, None


# ----------------------------------------------------------------------------- per source, from a stored payload
def _from_payload(source_id: str, payload: Mapping) -> tuple[str | None, float | None]:
    if source_id == "gdacs":
        return gdacs_severity(payload.get("properties") or {})
    if source_id == "eonet":
        geometries = payload.get("geometry") or []
        return eonet_severity(geometries[0] if geometries else None)
    if source_id == "copernicus":
        return copernicus_severity(payload)
    return None, None


def _payload(record) -> Mapping:
    payload = record["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


def _number(value) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- the one function
def normalise(records: Iterable) -> Severity:
    """Severity of an event from its source_record rows (any order; the latest observation per source wins)."""
    latest: dict[str, object] = {}
    for record in records:
        current = latest.get(record["source_id"])
        if current is None or (record["observed_at"], record["source_record_id"]) >= (current["observed_at"], current["source_record_id"]):
            latest[record["source_id"]] = record
    ems = "copernicus" in latest
    label = score = None
    for source_id in [*config.PRIMARY_SOURCE_ORDER, *sorted(set(latest) - set(config.PRIMARY_SOURCE_ORDER))]:
        record = latest.get(source_id)
        if record is None:
            continue
        candidate_label, candidate_score = _from_payload(source_id, _payload(record))
        if candidate_score is not None:
            label, score = candidate_label, candidate_score
            break
    if ems:
        score = max(score or 0.0, config.EMS_SEVERITY_FLOOR)
        label = label or "EMS activation"
    return Severity(label, score, ems)
