"""Severity normalisation: GDACS bands with the episode-score tie-break, EONET per-unit scaling, the EMS floor."""

import json

import pytest

from eww import config, severity


def gdacs_props(level, episode=None, alert=None):
    props = {"alertlevel": level}
    if episode is not None:
        props["episodealertscore"] = episode
    if alert is not None:
        props["alertscore"] = alert
    return props


@pytest.mark.parametrize(
    "level, episode, alert, expected",
    [
        ("Green", 0.0, 1, ("Green", 0.33)),
        ("Green", 0.74, 1, ("Green", 0.337)),
        ("green", 0.5, 1, ("Green", 0.335)),
        ("Orange", 1.5, 2, ("Orange", 0.675)),
        ("Orange", None, 2, ("Orange", 0.68)),  # no episode score: alertscore breaks the tie
        ("Red", 2.5, 3, ("Red", 1.0)),
        ("Red", 3.0, 3, ("Red", 1.0)),
        ("Purple", 1.0, 1, ("Purple", None)),
        ("", None, None, (None, None)),
    ],
)
def test_gdacs_bands_and_tiebreak(level, episode, alert, expected):
    assert severity.gdacs_severity(gdacs_props(level, episode, alert)) == expected


def test_gdacs_tiebreak_never_crosses_a_band():
    for level, base in config.GDACS_SEVERITY.items():
        for episode in (0.0, 1.0, 2.0, 3.0, 99.0):
            _, score = severity.gdacs_severity(gdacs_props(level, episode))
            assert base <= score <= min(1.0, base + config.GDACS_TIEBREAK_SPAN)


@pytest.mark.parametrize(
    "value, unit, expected",
    [
        (None, None, (None, 0.4)),
        (45.0, "kts", ("45 kts", 0.451)),
        (34.0, "kts", ("34 kts", 0.33)),
        (64.0, "kts", ("64 kts", 0.66)),
        (140.0, "kts", ("140 kts", 1.0)),
        (5747.0, "hectare", ("5747 hectare", 0.34)),
        (1000.0, "acres", ("1000 acres", 0.119)),  # 404.7 ha, well below the Green point
        (107562.0, "acres", ("107562 acres", 0.726)),  # 43,529 ha: between the Orange and Red points
        (12.0, "NM", ("12 NM", 0.4)),  # unknown unit: the default
    ],
)
def test_eonet_magnitude_scaled_per_unit(value, unit, expected):
    assert severity.eonet_severity({"magnitudeValue": value, "magnitudeUnit": unit}) == expected


def record(source_id, payload, observed_at="2026-09-10T00:00:00Z", record_id="01R"):
    return {"source_id": source_id, "observed_at": observed_at, "source_record_id": record_id, "payload": json.dumps(payload)}


def gdacs_payload(level, episode=0.5):
    return {"properties": gdacs_props(level, episode, {"Green": 1, "Orange": 2, "Red": 3}[level])}


def eonet_payload(value=None, unit=None):
    return {"geometry": [{"magnitudeValue": value, "magnitudeUnit": unit}]}


def test_normalise_prefers_gdacs_then_eonet_and_applies_the_ems_floor():
    orange, eonet_rec = record("gdacs", gdacs_payload("Orange", 1.5), record_id="01A"), record("eonet", eonet_payload(45, "kts"), record_id="01B")
    assert severity.normalise([eonet_rec, orange]) == severity.Severity("Orange", 0.675, False)
    assert severity.normalise([eonet_rec]) == severity.Severity("45 kts", 0.451, False)
    ems = record("copernicus", {"code": "EMSR1", "category": "Flood"}, record_id="01C")
    assert severity.normalise([ems]) == severity.Severity("EMS activation", 0.66, True)
    assert severity.normalise([record("gdacs", gdacs_payload("Green", 0.0), record_id="01D"), ems]) == severity.Severity("Green", 0.66, True)
    assert severity.normalise([ems, record("gdacs", gdacs_payload("Red", 2.5), record_id="01E")]) == severity.Severity("Red", 1.0, True)
    assert severity.normalise([]) == severity.Severity(None, None, False)


def test_normalise_takes_the_latest_record_of_a_source():
    older = record("gdacs", gdacs_payload("Green", 0.0), observed_at="2026-09-01T00:00:00Z", record_id="01A")
    newer = record("gdacs", gdacs_payload("Orange", 1.0), observed_at="2026-09-02T00:00:00Z", record_id="01B")
    assert severity.normalise([newer, older]).label == "Orange"
    assert severity.normalise([older, newer]).label == "Orange"
