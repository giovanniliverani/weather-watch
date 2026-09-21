"""Enrichment collectors: query terms, the GDELT and ReliefWeb calls (through a mock transport), spacing, 429s and provenance."""

import json
from datetime import timedelta

import httpx
import pytest

from eww import config, documents, enrich, ratelimit, resolve
from eww import http as http_mod
from eww.clock import now_utc, parse_iso, to_iso
from eww.enrich import gdelt, queries, reliefweb
from tests.conftest import copernicus_item, eonet_item, gdacs_item, gdacs_source, ingest_items

T0 = "2026-09-16T15:10:00Z"


@pytest.fixture(autouse=True)
def provider_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_LOG", tmp_path / "logs" / "providers.jsonl")


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": config.USER_AGENT})


def prepared(conn, data_dir):
    flood = gdacs_item(1104124, "FL", "Flood in Nepal", 28.2, 85.3, "2026-09-10T00:00:00", alertlevel="Red", iso3="NPL", glide="FL-2026-000124-NPL")
    storm = gdacs_item(1000456, "TC", "Tropical Cyclone SAUDEL-26", 20.0, 125.0, "2026-09-12T00:00:00", alertlevel="Orange", eventname="SAUDEL-26", iso3="PHL")
    fire = copernicus_item("EMSR940", "Wildfire in Huelva Province, Spain", "Wildfire", -6.9, 37.3, "2026-09-15T13:00:00", "2026-09-15T16:58:00", countries=("Spain",), closed=False)
    ingest_items(conn, data_dir, "gdacs", [flood, storm], T0)
    ingest_items(conn, data_dir, "copernicus", [fire], T0)
    resolve.resolve(conn)
    return {row["title"]: row for row in conn.execute("SELECT * FROM event")}


# ----------------------------------------------------------------------------- query terms
@pytest.mark.parametrize(
    "title, expected",
    [
        ("Wildfire in Huelva Province, Spain", ["Huelva", "Spain"]),
        ("Storm in Basilicata, Italy", ["Basilicata", "Italy"]),
        ("Wildfire Snow, Custer, Montana", ["Custer", "Montana"]),
        ("Flood in Nepal", ["Nepal"]),
        ("Flood in Croatia 1104153", ["Croatia"]),
        ("Wildfire in Island of Brac, Croatia", ["Brac", "Croatia"]),
        ("Drought in Kenya, Tanzania, Uganda", ["Kenya", "Tanzania", "Uganda"]),
        ("Tropical Cyclone SAUDEL-26", []),
    ],
)
def test_places_from_title(title, expected):
    assert queries.places_from_title(title) == expected


def test_storm_display_name():
    assert queries.storm_display_name("SAUDEL-26") == "Saudel"
    assert queries.storm_display_name("Hurricane Karina") == "Karina"
    assert queries.storm_display_name("Tropical Cyclone GEZANI-26 in Madagascar") == "Gezani"
    assert queries.storm_display_name("TWENTYFOUR-26") is None and queries.storm_display_name("TWO-C-26") is None
    assert queries.storm_display_name(None) is None


def test_event_terms_and_gdelt_query(conn, data_dir):
    events = prepared(conn, data_dir)
    terms = queries.event_terms(conn, events["Wildfire in Huelva Province, Spain"])
    assert terms.countries_iso3 == ["ESP"] and terms.countries_en == ["Spain"] and terms.countries_it == ["Spagna"]
    assert terms.admin1 == ["Huelva"] and terms.storm_name is None
    query = queries.gdelt_query(terms)
    assert query.startswith('(Huelva OR Spain OR Spagna) (wildfire OR wildfires OR bushfire OR "forest fire"')
    assert query.count("(") == 2 and query.count(")") == 2
    storm = queries.event_terms(conn, events["Tropical Cyclone SAUDEL-26"])
    assert storm.storm_name == "Saudel" and storm.place_terms[0] == "Saudel" and "Philippines" in storm.place_terms
    assert queries.gdelt_query(storm).startswith("(Saudel OR Philippines OR Filippine) (hurricane OR typhoon")
    nepal = queries.event_terms(conn, events["Flood in Nepal"])
    assert queries.gdelt_query(nepal) == "Nepal (flood OR floods OR flooding OR \"flash flood\" OR alluvione OR alluvioni OR inondazione OR esondazione OR allagamenti)"  # one term: no parentheses (GDELT rejects them)
    empty = queries.EventTerms(hazard_type="flood")
    assert queries.gdelt_query(empty) is None


# ----------------------------------------------------------------------------- GDELT
def articles(n=3, prefix="https://news.example.com/story"):
    return {
        "articles": [
            {
                "url": f"{prefix}{i}?utm_source=gdelt",
                "url_mobile": f"{prefix}{i}/amp",
                "title": f"Nepal floods: rivers burst their banks, {i} dead",
                "seendate": f"20260915T1{i}0000Z",
                "socialimage": f"https://img.example.com/{i}.jpg" if i % 2 else "",
                "domain": "news.example.com",
                "language": "English",
                "sourcecountry": "Nepal",
            }
            for i in range(n)
        ]
    }


def test_gdelt_collector_writes_documents_and_provenance(conn, data_dir):
    events = prepared(conn, data_dir)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        params = dict(request.url.params)
        assert params["mode"] == "ArtList" and params["format"] == "json" and params["sort"] == "DateDesc"
        assert params["maxrecords"] == str(config.GDELT_MAX_RECORDS)
        assert len(params["startdatetime"]) == 14 and len(params["enddatetime"]) == 14
        assert request.headers["User-Agent"] == config.USER_AGENT and config.CONTACT in request.headers["User-Agent"]
        return httpx.Response(200, json=articles())

    slept = []
    collector = gdelt.GdeltCollector(http=mock_client(handler), sleep=slept.append)
    now = now_utc()
    event = events["Flood in Nepal"]
    since, until = enrich.window_for(conn, event, "gdelt", now, lookback_days=config.GDELT_LOOKBACK_DAYS)
    assert since == parse_iso(event["started_at"]) - timedelta(days=config.ENRICH_BACKFILL_DAYS)
    result = collector.enrich_event(conn, event, since, until, now)
    assert result.items_seen == 3 and result.documents_new == 3 and result.documents_seen == 3
    assert "Nepal" in result.query
    rows = conn.execute("SELECT * FROM document ORDER BY url").fetchall()
    assert len(rows) == 3
    assert rows[0]["url_canonical"] == "https://news.example.com/story0" and rows[0]["publisher"] == "news.example.com"
    assert rows[0]["published_at"] == "2026-09-15T10:00:00Z" and rows[0]["language"] == "English" and rows[0]["kind"] == "article"
    assert rows[0]["media_kind"] == "none" and rows[1]["media_url"] == "https://img.example.com/1.jpg" and rows[1]["media_kind"] == "image"
    assert rows[0]["text_excerpt"] is None  # GDELT gives headlines only: never a body
    assert conn.execute("SELECT COUNT(*) FROM document_retrieval WHERE event_id = ? AND source_id = 'gdelt'", (event["event_id"],)).fetchone()[0] == 3
    again = collector.enrich_event(conn, event, since, until, now)
    assert again.documents_new == 0 and again.documents_seen == 3 and conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 3
    assert len(calls) == 2 and slept and slept[0] >= 4.5  # the second call waited for the 5-second spacing
    logged = ratelimit.read(kind="call")
    assert len(logged) == 2 and all(r["provider"] == "gdelt" and config.CONTACT in r["user_agent"] for r in logged)
    collector.close()


def test_gdelt_429_and_text_notice_stop_the_run(conn, data_dir):
    events = prepared(conn, data_dir)
    responses = iter([httpx.Response(429, text="slow down"), httpx.Response(200, text="Please limit requests to one every 5 seconds")])

    def handler(request):
        return next(responses)

    collector = gdelt.GdeltCollector(http=mock_client(handler), sleep=lambda s: None)
    now = now_utc()
    with pytest.raises(ratelimit.RateLimitExceeded):
        collector.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=2), now, now)
    with pytest.raises(ratelimit.RateLimitExceeded):
        collector.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=2), now, now)
    collector.close()


def test_enrich_run_records_windows_and_stops_on_a_rate_limit(conn, data_dir, monkeypatch):
    prepared(conn, data_dir)
    count = {"n": 0}

    def handler(request):
        count["n"] += 1
        if count["n"] == 2:
            return httpx.Response(429, text="")
        return httpx.Response(200, json=articles(2, prefix=f"https://site{count['n']}.example.com/a"))

    monkeypatch.setattr(gdelt, "collector", lambda http=None, sleep=None: gdelt.GdeltCollector(http=mock_client(handler), sleep=lambda s: None))
    now = now_utc()
    stats = enrich.run(conn, ["gdelt", "reliefweb"], now=now)
    g = stats["gdelt"]
    assert g.events_considered == 3 and g.events_queried == 1 and g.stopped and "429" in g.stopped
    assert g.documents_new == 2
    runs = conn.execute("SELECT * FROM enrichment_run").fetchall()
    assert len(runs) == 1 and runs[0]["last_success_at"] == to_iso(now) and runs[0]["source_id"] == "gdelt"
    assert stats["reliefweb"].stopped and "RELIEFWEB_APPNAME" in stats["reliefweb"].stopped
    # the next run for that event starts where this one ended
    event = conn.execute("SELECT * FROM event WHERE event_id = ?", (runs[0]["event_id"],)).fetchone()
    since, _ = enrich.window_for(conn, event, "gdelt", now + timedelta(hours=3))
    assert since == now
    line = enrich.summary_line(stats)
    assert "gdelt: events=1/3" in line and "reliefweb=skipped" in line


def test_active_events_order_and_cap(conn, data_dir):
    prepared(conn, data_dir)
    rows = enrich.active_events(conn, days=14, now=parse_iso("2026-09-17T00:00:00Z"), limit=2)
    assert [r["title"] for r in rows] == ["Flood in Nepal", "Tropical Cyclone SAUDEL-26"]  # Red, then Orange
    assert len(enrich.active_events(conn, days=14, now=parse_iso("2026-09-17T00:00:00Z"), limit=0)) == 3
    assert enrich.active_events(conn, days=1, now=parse_iso("2026-12-01T00:00:00Z")) == []


# ----------------------------------------------------------------------------- ReliefWeb
def test_reliefweb_skipped_without_appname_and_filter_shapes(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "RELIEFWEB_APPNAME", None)
    ok, reason = reliefweb.available(conn)
    assert not ok and "RELIEFWEB_APPNAME" in reason
    since = parse_iso("2026-09-01T00:00:00Z")
    glide_filter = reliefweb.build_filter(events["Flood in Nepal"], since)
    assert glide_filter["conditions"][0] == {"field": "disaster.glide", "value": "FL-2026-000124-NPL"}
    assert glide_filter["conditions"][1]["value"]["from"] == "2026-09-01T00:00:00+00:00"
    country_filter = reliefweb.build_filter(events["Wildfire in Huelva Province, Spain"], since)
    assert country_filter["conditions"][0] == {"field": "country.iso3", "value": "esp"}
    assert country_filter["conditions"][1] == {"field": "disaster_type.name", "value": ["Wild Fire"], "operator": "OR"}
    body = reliefweb.build_body(events["Flood in Nepal"], since)
    assert body["fields"]["include"] == config.RELIEFWEB_FIELDS and body["limit"] == config.RELIEFWEB_PAGE_LIMIT and body["sort"] == ["date.original:desc"]


def test_reliefweb_collector_truncates_bodies_and_handles_403(conn, data_dir, monkeypatch):
    events = prepared(conn, data_dir)
    monkeypatch.setattr(config, "RELIEFWEB_APPNAME", "eww-test-appname")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST" and dict(request.url.params)["appname"] == "eww-test-appname"
        payload = json.loads(request.content)
        assert payload["filter"]["conditions"][0]["field"] == "disaster.glide"
        return httpx.Response(200, json={"data": [{
            "id": 4001,
            "fields": {
                "title": "Nepal: Floods and Landslides Flash Update No. 1",
                "url": "https://reliefweb.int/report/nepal/nepal-floods-flash-update-1",
                "date": {"original": "2026-09-14T00:00:00+00:00"},
                "source": [{"shortname": "OCHA"}],
                "disaster": [{"glide": "FL-2026-000124-NPL"}],
                "country": [{"iso3": "npl"}],
                "language": [{"code": "en"}],
                "body": "Heavy rain " * 1000,
            },
        }]})

    collector = reliefweb.ReliefWebCollector(http=mock_client(handler), sleep=lambda s: None)
    now = now_utc()
    result = collector.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=7), now, now)
    assert result.items_seen == 1 and result.documents_new == 1 and result.query.startswith("glide=")
    row = conn.execute("SELECT * FROM document").fetchone()
    assert row["kind"] == "report" and row["publisher"] == "ocha" and row["external_id"] == "4001" and row["language"] == "en"
    assert row["published_at"] == "2026-09-14T00:00:00Z" and len(row["text_excerpt"]) == 2000
    assert len(json.loads(row["payload"])["fields"]["body"]) <= 2000  # the stored payload never carries a full body either
    assert reliefweb.country_of(json.loads(row["payload"])) == "NPL"
    collector.close()

    forbidden = reliefweb.ReliefWebCollector(http=mock_client(lambda r: httpx.Response(403, json={"error": {"message": "not approved"}})), sleep=lambda s: None)
    with pytest.raises(ratelimit.RateLimitExceeded):
        forbidden.enrich_event(conn, events["Flood in Nepal"], now - timedelta(days=7), now, now)
    assert forbidden.forbidden
    forbidden.close()


def test_provider_retries_5xx_but_not_429(tmp_path):
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(503 if attempts["n"] == 1 else 200, json={"ok": True})

    provider = http_mod.Provider("test", http=mock_client(handler), retries=3, sleep=lambda s: None)
    status, body = provider.get_json("https://example.com/x", {"a": 1})
    assert status == 200 and body == {"ok": True} and attempts["n"] == 2
    with pytest.raises(ratelimit.RateLimitExceeded):
        http_mod.Provider("test", http=mock_client(lambda r: httpx.Response(429)), sleep=lambda s: None).get("https://example.com/y")
    with pytest.raises(ValueError):
        http_mod.Provider("test", http=mock_client(lambda r: httpx.Response(200, text="<html>not json"))).get_json("https://example.com/z")
    records = ratelimit.read(kind="call")
    assert [r["status"] for r in records] == [503, 200, 429, 200]
