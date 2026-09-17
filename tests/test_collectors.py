"""normalise() on saved fixture JSON for both sources, plus the derived helpers."""

import xml.etree.ElementTree as ET

import pytest

from eww.collectors import eonet, gdacs
from tests.conftest import FIXTURES, eonet_events, gdacs_features

RECORD_KEYS = {
    "source_id",
    "external_id",
    "external_episode",
    "hazard_type",
    "title",
    "observed_at",
    "started_at",
    "ended_at",
    "lat",
    "lon",
    "severity_raw",
    "glide_number",
    "payload",
}


def by_type(features, event_type):
    return next(f for f in features if f["properties"]["eventtype"] == event_type)


# --------------------------------------------------------------------------- GDACS
def test_gdacs_normalise_earthquake():
    rec = gdacs.normalise(by_type(gdacs_features(), "EQ"))
    assert set(rec) == RECORD_KEYS
    assert rec["source_id"] == "gdacs"
    assert rec["external_id"] == "1564809"
    assert rec["external_episode"] == "1732664"
    assert rec["hazard_type"] == "earthquake"
    assert rec["title"] == "Earthquake in Indonesia"
    assert rec["started_at"] == "2026-09-11T21:23:55Z"
    assert rec["ended_at"] == "2026-09-11T21:23:55Z"  # iscurrent false -> the event has ended
    assert rec["observed_at"] == "2026-09-12T13:04:10Z"
    assert (rec["lon"], rec["lat"]) == (106.8742, -4.9832)
    assert rec["glide_number"] is None
    assert '"alertlevel": "Green"' in rec["severity_raw"]
    assert rec["payload"]["properties"]["eventid"] == 1564809


def test_gdacs_normalise_current_cyclone_has_no_end():
    feature = by_type(gdacs_features(), "TC")
    rec = gdacs.normalise(feature)
    assert rec["hazard_type"] == "tropical_cyclone"
    assert rec["title"] == "Tropical Cyclone NORBERT-26"
    assert rec["ended_at"] is None
    assert gdacs.country_iso3(feature) is None  # over the ocean: iso3 is ''


def test_gdacs_derived_helpers():
    feature = by_type(gdacs_features(), "FL")
    assert gdacs.severity(feature) == ("Orange", 0.675)  # Orange band 0.66 + episodealertscore 1.5 of 3.0 over a 0.03 span
    assert gdacs.country_iso3(feature) == "CHN"
    assert gdacs.detail_url(feature).startswith("https://www.gdacs.org/report.aspx?")
    assert gdacs.footprint(feature) is None
    assert gdacs.severity(by_type(gdacs_features(), "EQ")) == ("Green", 0.33)
    assert gdacs.iter_records(feature) == [gdacs.normalise(feature)]


def test_gdacs_rss_item_becomes_feature():
    root = ET.parse(FIXTURES / "gdacs_rss.xml").getroot()
    items = root.findall("./channel/item")
    assert len(items) == 2
    feature = gdacs.rss_item_to_feature(items[0])
    rec = gdacs.normalise(feature)
    assert set(rec) == RECORD_KEYS
    assert rec["hazard_type"] == "wildfire"
    assert rec["external_id"] == "1032032"
    assert rec["external_episode"] == "1"
    assert pytest.approx(rec["lat"], abs=1e-6) == -18.659463577186177
    assert pytest.approx(rec["lon"], abs=1e-6) == 145.78141708992061
    assert rec["started_at"] == "2026-09-16T00:00:00Z"
    assert rec["ended_at"] is None  # iscurrent true
    assert feature["properties"]["source_format"] == "rss"
    label, score = gdacs.severity(feature)
    assert label == "Green" and 0.33 <= score < 0.36
    assert gdacs.footprint(feature) is None  # a point bbox is not a footprint
    assert gdacs.linked_ids(feature) == []
    assert gdacs.country_iso3(feature) == "AUS"
    assert gdacs.detail_url(feature) == "https://www.gdacs.org/report.aspx?eventtype=WF&eventid=1032032"


# --------------------------------------------------------------------------- EONET
def by_id(events, event_id):
    return next(e for e in events if e["id"] == event_id)


def test_eonet_normalise_wildfire_point():
    event = by_id(eonet_events(), "EONET_24268")
    rec = eonet.normalise(event)
    assert set(rec) == RECORD_KEYS
    assert rec["source_id"] == "eonet"
    assert rec["external_id"] == "EONET_24268"
    assert rec["external_episode"] == "2026-09-11T19:00:00Z"
    assert rec["hazard_type"] == "wildfire"
    assert rec["ended_at"] == "2026-09-12T00:00:00Z"
    assert rec["started_at"] == rec["observed_at"] == "2026-09-11T19:00:00Z"
    assert pytest.approx(rec["lon"], abs=1e-6) == 16.525371955075
    assert pytest.approx(rec["lat"], abs=1e-6) == -19.219845995937
    assert rec["payload"]["geometry"] == event["geometry"]
    assert eonet.severity(rec["payload"]) == ("5747 hectare", 0.34)  # just above the 5,000 ha Green point
    assert eonet.linked_ids(rec["payload"]) == [("gdacs", "1031934")]
    assert eonet.storm_name(rec["payload"]) is None
    assert eonet.country_iso3(rec["payload"]) == "NAM"
    assert eonet.detail_url(rec["payload"]) == event["link"]


def test_eonet_polygon_axes_are_swapped_to_lon_lat():
    event = by_id(eonet_events(), "EONET_24267")  # Flood in Croatia; GDACS puts it at lon 16.44, lat 43.51
    rec = eonet.normalise(event)
    assert rec["hazard_type"] == "flood"
    assert abs(rec["lat"] - 43.51) < 0.2 and abs(rec["lon"] - 16.44) < 0.2
    footprint = eonet.footprint(rec["payload"])
    assert footprint["type"] == "Polygon"
    lon, lat = footprint["coordinates"][0][0]
    assert 16 < lon < 17 and 43 < lat < 44
    assert eonet.country_iso3(rec["payload"]) == "HRV"


def test_eonet_one_record_per_geometry_entry():
    storm = by_id(eonet_events(), "EONET_23611")
    records = eonet.iter_records(storm)
    assert len(records) == 2
    assert len({r["external_episode"] for r in records}) == 2
    assert {r["hazard_type"] for r in records} == {"tropical_cyclone"}
    assert all(r["started_at"] == min(g["date"] for g in storm["geometry"]) for r in records)
    assert all(len(r["payload"]["geometry"]) == 1 for r in records)
    assert records[0]["ended_at"] is None or storm["closed"] is not None
    label, score = eonet.severity(records[0]["payload"])
    assert label == "40 kts" and 0.33 <= score < 0.66  # a 40 kt tropical storm sits between Green and Orange
    assert eonet.storm_name(records[0]["payload"]) == "Hurricane Karina"
    assert eonet.country_iso3(records[0]["payload"]) is None


@pytest.mark.parametrize(
    "event_id, hazard",
    [
        ("EONET_TEST_1", "heatwave"),
        ("EONET_TEST_2", "coldwave"),
        ("EONET_TEST_3", "coldwave"),
        ("EONET_TEST_4", "other"),
        ("EONET_TEST_5", "landslide"),
        ("EONET_TEST_6", "severe_storm"),
        ("EONET_TEST_7", "earthquake"),
        ("EONET_TEST_8", "volcano"),
        ("EONET_TEST_9", "drought"),
    ],
)
def test_eonet_hazard_mapping(event_id, hazard):
    assert eonet.normalise(by_id(eonet_events(), event_id))["hazard_type"] == hazard


def test_eonet_country_from_title_with_aliases():
    assert eonet.country_iso3(by_id(eonet_events(), "EONET_TEST_9")) == "ZMB"  # "Zambia, The Democratic Republic of Congo"
    assert eonet.country_iso3(by_id(eonet_events(), "EONET_TEST_5")) == "NPL"
    assert eonet.country_iso3({"title": "Flood in Islamic Republic of Iran 1104147", "sources": []}) == "IRN"
    assert eonet.country_iso3({"title": "Bear Creek Fire", "sources": [{"id": "IRWIN", "url": "x"}]}) == "USA"
    assert eonet.country_iso3({"title": "Green forest fire notification in [unknown] 5", "sources": []}) is None
