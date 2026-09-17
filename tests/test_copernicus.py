"""The Copernicus EMS collector: paging, the window filter, normalise() and the derived helpers."""

import json
from datetime import datetime, timezone

import httpx

from eww import config
from eww.collectors import copernicus
from tests.conftest import copernicus_activations
from tests.test_collectors import RECORD_KEYS


def by_code(code: str) -> dict:
    return next(i for i in copernicus_activations() if i["code"] == code)


def paged_transport(items: list[dict], page_size: int = 4) -> httpx.MockTransport:
    """Serve the fixture the way the API does: count, next, previous, results, driven by limit/offset."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/public-activations-info/")
        offset = int(request.url.params.get("offset", 0))
        page = items[offset : offset + page_size]
        next_url = None
        if offset + page_size < len(items):
            next_url = f"{config.COPERNICUS_ACTIVATIONS_URL}?limit={page_size}&offset={offset + page_size}"
        return httpx.Response(200, json={"count": len(items), "next": next_url, "previous": None, "results": page})

    return httpx.MockTransport(handler)


def test_fetch_pages_through_the_list_and_keeps_the_window():
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 20, tzinfo=timezone.utc)
    with httpx.Client(transport=paged_transport(copernicus_activations())) as http:
        result = copernicus.fetch(since, until, http=http)
    assert len(result.requests) == 3 and result.http_status == 200 and result.status == "ok"
    codes = [i["code"] for i in result.items]
    assert codes == sorted(codes)
    assert set(codes) == {"EMSR912", "EMSR916", "EMSR926", "EMSR927", "EMSR930", "EMSR932"}  # in the window or still open
    assert "EMSR872" not in codes and "EMSR751" not in codes  # April 2026 and 2024, closed


def test_in_window_keeps_open_activations():
    old_but_open = {**by_code("EMSR751"), "closed": False}
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert copernicus.in_window(old_but_open, since, until)
    assert not copernicus.in_window(by_code("EMSR751"), since, until)


def test_public_events_yield_no_record():
    assert copernicus.iter_records(by_code("EMSR877")) == []  # category 'Other': Public Event in Burgenland
    assert len(copernicus.iter_records(by_code("EMSR927"))) == 1


def test_normalise_flood_with_gdacs_id():
    item = by_code("EMSR927")
    rec = copernicus.normalise(item)
    assert set(rec) == RECORD_KEYS
    assert rec["source_id"] == "copernicus" and rec["external_id"] == "EMSR927" and rec["external_episode"] == ""
    assert rec["hazard_type"] == "flood" and rec["title"] == "Flood in Nepal"
    assert rec["started_at"] == "2026-08-25T22:00:00Z"  # eventTime
    assert rec["observed_at"] == "2026-08-26T09:53:00Z"  # activationTime
    assert rec["ended_at"] == "2026-09-14T07:16:09Z"  # closed: lastUpdate, without the microseconds
    assert abs(rec["lat"] - 28.2122) < 1e-3 and abs(rec["lon"] - 85.3538) < 1e-3
    assert rec["glide_number"] is None
    assert json.loads(rec["severity_raw"]) == {"category": "Flood", "closed": True, "n_aois": 6, "n_products": 6}
    assert rec["payload"] is item
    assert copernicus.linked_ids(item) == [("gdacs", "1104124")]
    assert copernicus.country_iso3(item) == "NPL"
    assert copernicus.detail_url(item) == "https://rapidmapping.emergency.copernicus.eu/EMSR927"
    assert copernicus.attribution(item) == "Copernicus Emergency Management Service (© 2026 European Union), EMSR927"
    assert copernicus.severity(item) == (None, None)
    assert copernicus.footprint(item) is None
    assert copernicus.storm_name(item) is None


def test_open_activation_has_no_end_and_no_link():
    rec = copernicus.normalise(by_code("EMSR932"))
    assert rec["ended_at"] is None and rec["hazard_type"] == "wildfire"
    assert copernicus.linked_ids(rec["payload"]) == []
    assert copernicus.country_iso3(rec["payload"]) == "ESP"


def test_category_mapping_and_storm_names():
    assert copernicus.normalise(by_code("EMSR872"))["hazard_type"] == "tropical_cyclone"
    assert copernicus.storm_name(by_code("EMSR872")) == "Tropical Cyclone Sinlaku"
    storm = copernicus.normalise(by_code("EMSR930"))
    assert storm["hazard_type"] == "severe_storm" and storm["title"] == "Storm in Basilicata, Italy"  # trailing dot removed
    assert copernicus.storm_name(by_code("EMSR930")) is None
    assert copernicus.normalise(by_code("EMSR751"))["hazard_type"] == "landslide"
    assert copernicus.normalise(by_code("EMSR912"))["hazard_type"] == "volcano"
    assert copernicus.normalise(by_code("EMSR916"))["hazard_type"] == "earthquake"
    assert copernicus.hazard_for({"category": "Something new", "code": "EMSR999", "name": "x"}) == "other"


def test_multi_country_activation_takes_the_first_country():
    item = by_code("EMSR926")
    assert item["countries"] == ["Latvia", "Lithuania"]
    assert copernicus.country_iso3(item) == "LVA"
    assert copernicus.attribution(item).endswith("EMSR926")
