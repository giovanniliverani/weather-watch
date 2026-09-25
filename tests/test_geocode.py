"""The geocoder: gazetteer loading from a tiny GeoNames dump, the three tiers, the cache and the Nominatim limiter."""

import httpx
import pytest

from eww import config, geocode, ratelimit

CITY_COLUMNS = 19


def city(geonameid, name, lat, lon, cc, admin1, population, fcode="PPL", alternates=""):
    fields = [str(geonameid), name, name, alternates, str(lat), str(lon), "P", fcode, cc, "", admin1, "", "", "", str(population), "", "10", "Europe/Rome", "2026-01-01"]
    assert len(fields) == CITY_COLUMNS
    return "\t".join(fields)


@pytest.fixture
def dump(tmp_path):
    folder = tmp_path / "geonames"
    folder.mkdir()
    (folder / "cities500.txt").write_text(
        "\n".join([
            city(3181928, "Bologna", 44.49381, 11.33875, "IT", "05", 366133, "PPLA", "Bolonia,Bologne,Bononia"),
            city(3176959, "Forlì", 44.22177, 12.04144, "IT", "05", 118167, "PPLA2", "Forli"),
            city(1283240, "Kathmandu", 27.70169, 85.3206, "NP", "ST", 1442271, "PPLC", "Katmandu,Kathmandou"),
            city(2988507, "Paris", 48.85341, 2.3488, "FR", "11", 2138551, "PPLC", "Parigi,Paree"),
            city(4717560, "Paris", 33.66094, -95.55551, "US", "TX", 24171, "PPL"),
            city(2516548, "Huelva", 37.26638, -6.94004, "ES", "51", 149310, "PPLA2"),
            city(4409896, "Springfield", 37.21533, -93.29824, "US", "MO", 167882, "PPL"),
            city(4250542, "Springfield", 39.80172, -89.64371, "US", "IL", 116250, "PPLA"),
            "bad line without enough columns",
        ]),
        encoding="utf-8",
    )
    (folder / "admin1CodesASCII.txt").write_text(
        "IT.05\tEmilia-Romagna\tEmilia-Romagna\t3177401\nES.51\tAndalusia\tAndalusia\t2593109\nNP.ST\tCentral Region\tCentral Region\t1283236\nXX.99\tNowhere\tNowhere\t1\n",
        encoding="utf-8",
    )
    header = "#ISO\tISO3\tISO-Numeric\tfips\tCountry\tCapital\tArea(in sq km)\tPopulation\tContinent\ttld\tCurrencyCode\tCurrencyName\tPhone\tPostal Code Format\tPostal Code Regex\tLanguages\tgeonameid\tneighbours\tEquivalentFipsCode\n"
    rows = [
        ["IT", "ITA", "380", "IT", "Italy", "Rome", "301230", "60340328", "EU", ".it", "EUR", "Euro", "39", "", "", "it-IT", "3175395", "CH,VA,SI,SM,FR,AT", ""],
        ["NP", "NPL", "524", "NP", "Nepal", "Kathmandu", "140800", "28095714", "AS", ".np", "NPR", "Rupee", "977", "", "", "ne", "1282988", "CN,IN", ""],
        ["FR", "FRA", "250", "FR", "France", "Paris", "547030", "67059887", "EU", ".fr", "EUR", "Euro", "33", "", "", "fr-FR", "3017382", "CH,DE", ""],
        ["ES", "ESP", "724", "SP", "Spain", "Madrid", "504782", "47076781", "EU", ".es", "EUR", "Euro", "34", "", "", "es-ES", "2510769", "AD,PT", ""],
        ["US", "USA", "840", "US", "United States", "Washington", "9629091", "327167434", "NA", ".us", "USD", "Dollar", "1", "", "", "en-US", "6252001", "CA,MX", ""],
    ]
    (folder / "countryInfo.txt").write_text(header + "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    return folder


@pytest.fixture(autouse=True)
def provider_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_LOG", tmp_path / "logs" / "providers.jsonl")


def test_load_gazetteer_and_tier_1_lookups(conn, dump):
    stats = geocode.load_geonames(conn, dump)
    assert (stats.cities, stats.admin1, stats.skipped) == (8, 3, 1)
    assert stats.countries == 5  # Italy, Nepal, France, Spain (capital or city centroid) and the US
    assert geocode.gazetteer_count(conn) == 16
    geocoder = geocode.Geocoder(conn, tiers=["gazetteer"])
    bologna = geocoder.lookup("Bologna", "ITA")
    assert bologna.precision == "city" and bologna.country_iso3 == "ITA" and bologna.provider == "gazetteer"
    assert (round(bologna.lat, 2), round(bologna.lon, 2)) == (44.49, 11.34)
    region = geocoder.lookup("Emilia-Romagna", "ITA")
    assert region.precision == "admin1" and 44.2 < region.lat < 44.5  # population-weighted centre of its cities
    italy = geocoder.lookup("Italy", None)
    assert italy.precision == "country" and italy.country_iso3 == "ITA" and geocoder.lookup("Italia", None).country_iso3 == "ITA"
    assert geocoder.lookup("Bolonia", "ITA").name == "Bologna"  # alternate names through FTS
    assert geocoder.lookup("Forli", "ITA").name == "Forlì"  # asciiname
    assert geocoder.lookup("Springfield", "USA").lat == pytest.approx(37.21533)  # ties broken by population
    assert geocoder.lookup("Paris", "USA").country_iso3 == "USA"  # the hint wins over population
    assert geocoder.lookup("Paris", "NPL").country_iso3 == "FRA"  # nothing in Nepal: a big place elsewhere
    assert geocoder.lookup("Zzyzx", "ITA") is None
    assert geocoder.lookup("ab", "ITA") is None  # too short to try
    assert geocoder.lookups == 11 and geocoder.cache_hits == 0
    geocoder.lookup("Bologna", "ITA")
    geocoder.lookup("Zzyzx", "ITA")  # the miss is cached too
    assert geocoder.cache_hits == 2
    cached = conn.execute("SELECT * FROM geocode_cache WHERE query_norm = 'zzyzx'").fetchone()
    assert cached["lat"] is None and cached["precision"] == "unresolved" and cached["provider"] == "gazetteer"
    assert geocode.cache_stats(conn)["gazetteer"] == {"entries": 10, "found": 9}
    geo = ratelimit.geocode_report()
    assert geo["lookups"] == 12 and geo["cache_hits"] == 2
    again = geocode.load_geonames(conn, dump)
    assert again.cities == 8 and geocode.gazetteer_count(conn) == 16  # idempotent


@pytest.mark.parametrize(
    "fclass, fcode, expected",
    [("P", "PPLC", "city"), ("P", "PPL", "city"), ("A", "ADM1", "admin1"), ("A", "PCLI", "country"), ("A", "ADM2", "admin1"), ("T", "MT", "exact"), ("S", "HTL", "exact")],
)
def test_precision_from_geonames_features(fclass, fcode, expected):
    assert geocode.precision_for_feature(fclass, fcode) == expected


@pytest.mark.parametrize(
    "item, expected",
    [
        ({"addresstype": "road", "category": "highway"}, "street"),
        ({"addresstype": "city", "category": "place"}, "city"),
        ({"addresstype": "state", "category": "boundary"}, "admin1"),
        ({"addresstype": "country"}, "country"),
        ({"addresstype": "building", "category": "building"}, "exact"),
    ],
)
def test_precision_from_nominatim(item, expected):
    assert geocode.precision_for_nominatim(item) == expected


def remote_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": config.USER_AGENT})


def test_remote_tiers_run_only_when_allowed_and_are_cached(conn, dump, monkeypatch):
    geocode.load_geonames(conn, dump)
    monkeypatch.setattr(config, "GEONAMES_USERNAME", "eww_test")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        assert config.CONTACT in request.headers["User-Agent"]
        if "geonames" in request.url.host:
            params = dict(request.url.params)
            assert params["username"] == "eww_test" and params["maxRows"] == str(config.GEONAMES_MAX_ROWS)
            if params["q"] == "Casalecchio di Reno":
                return httpx.Response(200, json={"totalResultsCount": 1, "geonames": [{"name": "Casalecchio di Reno", "lat": "44.47646", "lng": "11.27488", "countryCode": "IT", "fcl": "P", "fcode": "PPL", "population": 35800, "adminName1": "Emilia-Romagna", "countryName": "Italy"}]})
            return httpx.Response(200, json={"totalResultsCount": 0, "geonames": []})
        params = dict(request.url.params)
        assert params["format"] == "jsonv2" and params["limit"] == "1" and params.get("countrycodes") == "it"
        if params["q"] == "Via Zamboni":
            return httpx.Response(200, json=[{"lat": "44.4953656", "lon": "11.3483321", "category": "highway", "type": "pedestrian", "addresstype": "road", "name": "Via Zamboni", "display_name": "Via Zamboni, Bologna, Italia"}])
        return httpx.Response(200, json=[])

    slept = []
    geocoder = geocode.Geocoder(conn, http=remote_client(handler), sleep=slept.append)
    assert geocoder.lookup("Casalecchio di Reno", "ITA", remote=False) is None  # gazetteer only: nothing asked remotely
    assert calls == []
    place = geocoder.lookup("Casalecchio di Reno", "ITA")
    assert place.provider == "geonames" and place.precision == "city" and place.country_iso3 == "ITA"
    street = geocoder.lookup("Via Zamboni", "ITA")
    assert street.provider == "nominatim" and street.precision == "street" and street.country_iso3 == "ITA"
    assert calls == ["api.geonames.org", "api.geonames.org", "nominatim.openstreetmap.org"]
    assert geocoder.lookup("Via Zamboni", "ITA").provider == "nominatim" and len(calls) == 3  # every tier answered from the cache
    assert geocoder.lookup("Nowhere Street", "ITA") is None and len(calls) == 5
    assert conn.execute("SELECT COUNT(*) FROM geocode_cache WHERE provider = 'nominatim' AND lat IS NULL").fetchone()[0] == 1
    report = ratelimit.limits_report()
    assert report["geonames"]["calls"] == 3 and report["nominatim"]["calls"] == 2 and report["nominatim"]["user_agent_ok"]
    geocoder.close()


def test_nominatim_never_exceeds_four_per_minute(conn, dump, monkeypatch):
    geocode.load_geonames(conn, dump)
    monkeypatch.setattr(config, "GEONAMES_USERNAME", None)  # tier 2 skipped without a username
    slept = []
    geocoder = geocode.Geocoder(conn, http=remote_client(lambda r: httpx.Response(200, json=[])), sleep=slept.append)
    for name in ("Alpha Street", "Beta Street", "Gamma Street", "Delta Street", "Epsilon Street"):
        assert geocoder.lookup(name, "ITA") is None
    assert geocoder.remote_calls == 5
    assert max(slept) > 55  # the fifth Nominatim call waited for the first to leave the minute
    assert sum(1 for s in slept if 0 < s <= 1.1) >= 3  # and every call kept at least a second from the previous one
    geocoder.close()


def test_geocoder_drops_a_tier_that_hits_its_budget(conn, dump, monkeypatch):
    geocode.load_geonames(conn, dump)
    monkeypatch.setattr(config, "GEONAMES_USERNAME", "eww_test")
    monkeypatch.setattr(config, "GEONAMES_HOURLY_BUDGET", 1)
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if "geonames" in request.url.host:
            return httpx.Response(200, json={"geonames": []})
        return httpx.Response(200, json=[])

    geocoder = geocode.Geocoder(conn, http=remote_client(handler), sleep=lambda s: None)
    assert geocoder.lookup("Unknown Alpha", "ITA") is None
    assert geocoder.lookup("Unknown Beta", "ITA") is None
    assert calls == ["api.geonames.org", "nominatim.openstreetmap.org", "nominatim.openstreetmap.org"]
    assert "geonames" not in geocoder.tiers
    geocoder.close()
