"""The geocoder (M3): three tiers behind one interface, every answer cached, misses included.

    Geocoder(conn).lookup("Bologna", country_hint="ITA") -> Place | None

Tier 1 is the local gazetteer: GeoNames cities500 plus admin1 and country rows loaded once by
`eww geonames load` (`load_geonames()`), matched by folded name, then by alternate names through FTS5,
inside the hinted country, ties broken by population; a name that misses inside the hint may still match
a big place elsewhere (config.GAZETTEER_UNHINTED_MIN_POPULATION). Tier 2 is the GeoNames web service
(one credit a search, budgeted per hour and per day), tier 3 the public Nominatim (4 requests a minute,
one at a time). Remote tiers run only while the caller says so (`remote=True`): the extractor stops
paying for lookups once a document has one located place. Each tier's answer for (query, hint) is one
row of geocode_cache, NULL coordinates meaning "this provider found nothing", so no question is ever
asked twice. Precision is recorded on every result: city, admin1, country, street or exact.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from eww import config, countries, ratelimit
from eww import http as http_mod
from eww.clock import now_iso, now_utc
from eww.lexicon import fold

log = logging.getLogger(__name__)

PRECISIONS = ("exact", "street", "city", "admin1", "country", "unresolved")
_ADMIN1_CODE = "ADM1"
_COUNTRY_CODE = "PCLI"
_NOMINATIM_ADMIN1 = {"state", "region", "province", "county", "state_district", "district", "municipality"}
_NOMINATIM_CITY = {"city", "town", "village", "hamlet", "suburb", "borough", "quarter", "neighbourhood", "city_district", "locality", "island", "islet"}
_NOMINATIM_STREET = {"road", "street", "highway", "pedestrian", "footway", "residential", "path", "square", "place"}


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    precision: str
    provider: str
    country_iso3: str | None = None
    display_name: str | None = None
    query: str | None = None
    country_hint: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def normalise(name: str | None) -> str:
    """The cache key: folded (lower case, no diacritics), punctuation trimmed, whitespace collapsed."""
    text = fold(name).replace("–", "-").strip(" .,;:'\"()[]")
    return " ".join(text.split())


def precision_for_feature(feature_class: str | None, feature_code: str | None) -> str:
    code = (feature_code or "").upper()
    if code == _COUNTRY_CODE or code.startswith("PCL"):
        return "country"
    if code == _ADMIN1_CODE:
        return "admin1"
    if (feature_class or "").upper() == "P" or code.startswith("PPL"):
        return "city"
    if (feature_class or "").upper() == "A":
        return "admin1"
    return "exact"


def precision_for_nominatim(item: dict) -> str:
    kind = str(item.get("addresstype") or item.get("type") or "").lower()
    category = str(item.get("category") or item.get("class") or "").lower()
    if kind == "country":
        return "country"
    if kind in _NOMINATIM_ADMIN1:
        return "admin1"
    if kind in _NOMINATIM_CITY or category == "place":
        return "city"
    if kind in _NOMINATIM_STREET or category == "highway":
        return "street"
    return "exact"


# ----------------------------------------------------------------------------- the interface
class Geocoder:
    """One instance per run; holds the provider clients and their limiters."""

    def __init__(self, conn: sqlite3.Connection, *, tiers: list[str] | None = None, http=None, sleep=None, max_remote_calls: int | None = None):
        self.conn = conn
        self.tiers = list(tiers or config.GEOCODE_TIERS)
        self.max_remote_calls = config.GEOCODE_REMOTE_CALLS_PER_RUN if max_remote_calls is None else max_remote_calls
        self._http = http
        self._sleep = sleep
        self._geonames: http_mod.Provider | None = None
        self._nominatim: http_mod.Provider | None = None
        self.lookups = 0
        self.cache_hits = 0
        self.remote_calls = 0

    def close(self) -> None:
        for provider in (self._geonames, self._nominatim):
            if provider is not None:
                provider.close()

    # -------------------------------------------------------------- providers, created on first use
    def geonames_provider(self) -> http_mod.Provider | None:
        if not config.GEONAMES_USERNAME:
            return None
        if self._geonames is None:
            kwargs = {"sleep": self._sleep} if self._sleep else {}
            hourly = ratelimit.SlidingWindow(config.GEONAMES_HOURLY_LIMIT, 3600, budget=config.GEONAMES_HOURLY_BUDGET, name="geonames/hour", **kwargs)
            daily = ratelimit.SlidingWindow(config.GEONAMES_DAILY_LIMIT, 86400, budget=config.GEONAMES_DAILY_BUDGET, name="geonames/day", **kwargs)
            ages = ratelimit.recent_call_ages("geonames", 86400)
            hourly.preload(ages)
            daily.preload(ages)
            self._geonames = http_mod.Provider("geonames", limiters=[ratelimit.MinInterval(config.GEONAMES_MIN_INTERVAL_S, **kwargs), hourly, daily], http=self._http, retries=2, **kwargs)
        return self._geonames

    def nominatim_provider(self) -> http_mod.Provider:
        if self._nominatim is None:
            kwargs = {"sleep": self._sleep} if self._sleep else {}
            per_minute = ratelimit.SlidingWindow(config.NOMINATIM_PER_MINUTE, 60, name="nominatim", **kwargs)
            per_minute.preload(ratelimit.recent_call_ages("nominatim", 60))
            self._nominatim = http_mod.Provider("nominatim", limiters=[ratelimit.MinInterval(config.NOMINATIM_MIN_INTERVAL_S, **kwargs), per_minute], http=self._http, retries=2, **kwargs)
        return self._nominatim

    # -------------------------------------------------------------- lookup
    def lookup(self, name: str, country_hint: str | None = None, *, remote: bool = True) -> Place | None:
        query = normalise(name)
        self.lookups += 1
        if len(query) < config.GEOCODE_MIN_NAME_CHARS:
            return None
        hint = (country_hint or "").upper()
        for tier in self.tiers:
            if tier != "gazetteer" and (not remote or self.remote_calls >= self.max_remote_calls):
                break  # remote tiers are off for this document (or this run's budget is spent): nothing is cached
            if tier == "geonames" and self.geonames_provider() is None:
                continue
            cached = cache_get(self.conn, query, hint, tier)
            if cached is not None:
                found, place = cached
                ratelimit.log_geocode(tier, query, hint, hit=found, cached=True)
                self.cache_hits += 1
                if found:
                    return place
                continue
            try:
                place = self._ask(tier, query, name, hint)
            except ratelimit.RateLimitExceeded as exc:
                log.warning("geocode tier %s unavailable for this run: %s", tier, exc)
                self.tiers = [t for t in self.tiers if t != tier]
                continue
            except Exception as exc:  # a remote failure is not a miss: nothing is cached
                log.warning("geocode tier %s failed query=%r error=%s", tier, query, exc)
                continue
            cache_put(self.conn, query, hint, tier, place)
            ratelimit.log_geocode(tier, query, hint, hit=place is not None, cached=False)
            if place is not None:
                return place
        return None

    def _ask(self, tier: str, query: str, name: str, hint: str) -> Place | None:
        if tier == "gazetteer":
            return gazetteer_lookup(self.conn, query, hint or None)
        if tier == "geonames":
            self.remote_calls += 1
            return geonames_lookup(self.geonames_provider(), name, hint or None)
        if tier == "nominatim":
            self.remote_calls += 1
            return nominatim_lookup(self.nominatim_provider(), name, hint or None)
        raise ValueError(f"unknown geocode tier {tier!r}")


# ----------------------------------------------------------------------------- cache
def cache_get(conn: sqlite3.Connection, query: str, hint: str, provider: str) -> tuple[bool, Place | None] | None:
    row = conn.execute(
        "SELECT * FROM geocode_cache WHERE query_norm = ? AND country_hint = ? AND provider = ?", (query, hint, provider)
    ).fetchone()
    if row is None:
        return None
    if row["lat"] is None or row["lon"] is None:
        return False, None
    extra = json.loads(row["bbox"]) if row["bbox"] and row["bbox"].startswith("{") else {}
    return True, Place(
        name=extra.get("name") or row["display_name"] or query,
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        precision=row["precision"],
        provider=provider,
        country_iso3=extra.get("country_iso3"),
        display_name=row["display_name"],
        query=query,
        country_hint=hint or None,
    )


def cache_put(conn: sqlite3.Connection, query: str, hint: str, provider: str, place: Place | None) -> None:
    values = {
        "query_norm": query,
        "country_hint": hint,
        "provider": provider,
        "lat": place.lat if place else None,
        "lon": place.lon if place else None,
        "precision": place.precision if place else "unresolved",
        "display_name": place.display_name if place else None,
        "bbox": json.dumps({"name": place.name, "country_iso3": place.country_iso3}) if place else None,
        "resolved_at": now_iso(),
    }
    with conn:
        conn.execute(
            """
            INSERT INTO geocode_cache (query_norm, country_hint, provider, lat, lon, precision, display_name, bbox, resolved_at)
            VALUES (:query_norm, :country_hint, :provider, :lat, :lon, :precision, :display_name, :bbox, :resolved_at)
            ON CONFLICT(query_norm, country_hint, provider) DO UPDATE SET lat = excluded.lat, lon = excluded.lon,
                precision = excluded.precision, display_name = excluded.display_name, bbox = excluded.bbox, resolved_at = excluded.resolved_at
            """,
            values,
        )


def cache_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT provider, COUNT(*) AS n, SUM(lat IS NOT NULL) AS found FROM geocode_cache GROUP BY 1 ORDER BY 1").fetchall()
    return {row["provider"]: {"entries": row["n"], "found": row["found"] or 0} for row in rows}


# ----------------------------------------------------------------------------- tier 1: the gazetteer
def _place_from_row(row: sqlite3.Row, query: str, hint: str | None) -> Place:
    return Place(
        name=row["name"],
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        precision=precision_for_feature(row["feature_class"], row["feature_code"]),
        provider="gazetteer",
        country_iso3=countries.iso3_for_iso2(row["country_iso2"]),
        display_name=f"{row['name']}, {row['country_iso2']}",
        query=query,
        country_hint=hint,
    )


def gazetteer_lookup(conn: sqlite3.Connection, query: str, hint: str | None) -> Place | None:
    """Exact folded name first (name or asciiname), then alternate names through FTS5; inside the hinted
    country first, then anywhere for places above config.GAZETTEER_UNHINTED_MIN_POPULATION."""
    iso2 = countries.iso2_for(hint) if hint else None
    attempts = [(iso2, 0), (None, config.GAZETTEER_UNHINTED_MIN_POPULATION)] if iso2 else [(None, 0)]
    for country_filter, min_population in attempts:
        row = _exact(conn, query, country_filter, min_population) or _fts(conn, query, country_filter, min_population)
        if row is not None:
            return _place_from_row(row, query, hint)
    return None


def _exact(conn: sqlite3.Connection, query: str, iso2: str | None, min_population: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM gazetteer_place
        WHERE (asciiname = :q COLLATE NOCASE OR name = :q COLLATE NOCASE)
          AND (:iso2 IS NULL OR country_iso2 = :iso2) AND population >= :pop
        ORDER BY CASE WHEN feature_code = 'PCLI' THEN 0 WHEN feature_code = 'ADM1' THEN 1 ELSE 2 END, population DESC, geonameid
        LIMIT 1
        """,
        {"q": query, "iso2": iso2, "pop": min_population},
    ).fetchone()


def _fts(conn: sqlite3.Connection, query: str, iso2: str | None, min_population: int) -> sqlite3.Row | None:
    phrase = '"' + query.replace('"', " ") + '"'
    try:
        return conn.execute(
            """
            SELECT p.* FROM gazetteer_fts f JOIN gazetteer_place p ON p.geonameid = f.rowid
            WHERE gazetteer_fts MATCH :phrase AND (:iso2 IS NULL OR p.country_iso2 = :iso2) AND p.population >= :pop
            ORDER BY p.population DESC, p.geonameid LIMIT 1
            """,
            {"phrase": phrase, "iso2": iso2, "pop": min_population},
        ).fetchone()
    except sqlite3.OperationalError as exc:  # an FTS syntax edge case is a miss, not a crash
        log.debug("gazetteer fts skipped query=%r error=%s", query, exc)
        return None


# ----------------------------------------------------------------------------- tier 2: GeoNames web service
def geonames_lookup(provider: http_mod.Provider | None, name: str, hint: str | None) -> Place | None:
    if provider is None:
        return None
    params = {"q": name, "maxRows": config.GEONAMES_MAX_ROWS, "username": config.GEONAMES_USERNAME, "style": "MEDIUM"}
    iso2 = countries.iso2_for(hint) if hint else None
    if iso2:
        params["country"] = iso2
    status, body = provider.get_json(config.GEONAMES_SEARCH_URL, params)
    if status >= 400:
        raise ValueError(f"geonames answered {status}")
    if isinstance(body, dict) and body.get("status"):
        message = str(body["status"].get("message") or "")
        if "limit" in message.lower():
            raise ratelimit.RateLimitExceeded(f"geonames: {message}")
        raise ValueError(f"geonames: {message}")
    items = list((body or {}).get("geonames") or [])
    if not items:
        return None
    items.sort(key=lambda i: (0 if str(i.get("fcl")) in ("P", "A") else 1, -int(i.get("population") or 0)))
    item = items[0]
    return Place(
        name=str(item.get("name") or item.get("toponymName") or name),
        lat=float(item["lat"]),
        lon=float(item["lng"]),
        precision=precision_for_feature(item.get("fcl"), item.get("fcode")),
        provider="geonames",
        country_iso3=countries.iso3_for_iso2(item.get("countryCode")),
        display_name=", ".join(str(v) for v in (item.get("name"), item.get("adminName1"), item.get("countryName")) if v),
        query=normalise(name),
        country_hint=hint,
    )


# ----------------------------------------------------------------------------- tier 3: Nominatim
def nominatim_lookup(provider: http_mod.Provider, name: str, hint: str | None) -> Place | None:
    params = {"q": name, "format": "jsonv2", "limit": 1}
    iso2 = countries.iso2_for(hint) if hint else None
    if iso2:
        params["countrycodes"] = iso2.lower()
    status, body = provider.get_json(config.NOMINATIM_SEARCH_URL, params)
    if status >= 400:
        raise ValueError(f"nominatim answered {status}")
    items = body if isinstance(body, list) else []
    if not items:
        return None
    item = items[0]
    country = None
    display = str(item.get("display_name") or "")
    if hint:
        country = hint
    return Place(
        name=str(item.get("name") or display.split(",")[0] or name),
        lat=float(item["lat"]),
        lon=float(item["lon"]),
        precision=precision_for_nominatim(item),
        provider="nominatim",
        country_iso3=country,
        display_name=display or None,
        query=normalise(name),
        country_hint=hint,
    )


# ----------------------------------------------------------------------------- loading the gazetteer
@dataclass
class LoadStats:
    cities: int = 0
    admin1: int = 0
    countries: int = 0
    skipped: int = 0
    source: str = ""
    loaded_at: str = ""


def download_dump(dest: Path, http=None) -> Path:
    """Fetch the three GeoNames files into `dest` (a temporary folder; nothing is kept under data/)."""
    dest.mkdir(parents=True, exist_ok=True)
    provider = http_mod.Provider("geonames-dump", http=http, timeout=300.0, retries=2)
    try:
        for filename in config.GEONAMES_DUMP_FILES:
            response = provider.get(config.GEONAMES_DUMP_URL + filename)
            if response.status_code >= 400:
                raise ValueError(f"{filename}: status {response.status_code}")
            (dest / filename).write_bytes(response.content)
            log.info("geonames downloaded file=%s bytes=%d", filename, len(response.content))
    finally:
        provider.close()
    return dest


def _read_cities(folder: Path):
    zip_path = folder / "cities500.zip"
    if zip_path.exists():
        with zipfile.ZipFile(zip_path) as archive:
            member = next(n for n in archive.namelist() if n.endswith(".txt"))
            with archive.open(member) as handle:
                for line in io.TextIOWrapper(handle, encoding="utf-8"):
                    yield line
    else:
        with (folder / "cities500.txt").open(encoding="utf-8") as handle:
            yield from handle


def load_geonames(conn: sqlite3.Connection, folder: Path) -> LoadStats:
    """Replace gazetteer_place with the dump in `folder` and rebuild gazetteer_fts. Idempotent."""
    stats = LoadStats(source=str(folder), loaded_at=now_iso())
    admin_names: dict[str, tuple[str, str, int]] = {}
    for line in (folder / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 4 and parts[3].strip().isdigit():
            admin_names[parts[0]] = (parts[1], parts[2], int(parts[3]))
    country_rows: dict[str, dict] = {}
    for line in (folder / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 17 or not parts[0] or not parts[16].strip().isdigit():
            continue
        country_rows[parts[0]] = {"iso2": parts[0], "iso3": parts[1], "name": parts[4], "capital": parts[5], "population": int(parts[7] or 0), "geonameid": int(parts[16])}
    cities: list[tuple] = []
    admin_acc: dict[tuple[str, str], list[float]] = {}  # (iso2, admin1) -> [sum_w_lat, sum_w_lon, sum_w, pop_sum]
    country_acc: dict[str, list[float]] = {}
    capitals: dict[str, tuple[float, float]] = {}
    for line in _read_cities(folder):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 15:
            stats.skipped += 1
            continue
        try:
            geonameid = int(parts[0])
            lat, lon = float(parts[4]), float(parts[5])
            population = int(parts[14] or 0)
        except ValueError:
            stats.skipped += 1
            continue
        iso2, admin1 = parts[8], parts[10]
        cities.append((geonameid, parts[1], parts[2], parts[3], lat, lon, parts[6], parts[7], iso2, admin1 or None, population))
        weight = float(max(population, 1))
        for key, acc in ((("admin", (iso2, admin1)), admin_acc), (("country", iso2), country_acc)):
            bucket = acc.setdefault(key[1], [0.0, 0.0, 0.0, 0.0])
            bucket[0] += lat * weight
            bucket[1] += lon * weight
            bucket[2] += weight
            bucket[3] += population
        if parts[7] == "PPLC":
            capitals[iso2] = (lat, lon)
    admin_rows: list[tuple] = []
    for code, (name, ascii_name, geonameid) in admin_names.items():
        iso2, _, admin1 = code.partition(".")
        acc = admin_acc.get((iso2, admin1))
        if not acc or acc[2] <= 0:
            continue
        admin_rows.append((geonameid, name, ascii_name, "", acc[0] / acc[2], acc[1] / acc[2], "A", _ADMIN1_CODE, iso2, admin1, int(acc[3])))
    country_out: list[tuple] = []
    for iso2, info in country_rows.items():
        point = capitals.get(iso2)
        acc = country_acc.get(iso2)
        if point is None and acc and acc[2] > 0:
            point = (acc[0] / acc[2], acc[1] / acc[2])
        if point is None:
            continue
        alternates = ",".join(n for n in (info["iso3"], *(_italian_names(info["iso3"]))) if n)
        country_out.append((info["geonameid"], info["name"], fold(info["name"]).title(), alternates, point[0], point[1], "A", _COUNTRY_CODE, iso2, None, info["population"]))
    seen_ids = {row[0] for row in cities}
    admin_rows = [r for r in admin_rows if r[0] not in seen_ids]
    seen_ids |= {row[0] for row in admin_rows}
    country_out = [r for r in country_out if r[0] not in seen_ids]
    with conn:
        conn.execute("DELETE FROM gazetteer_place")
        sql = "INSERT INTO gazetteer_place (geonameid, name, asciiname, alternatenames, lat, lon, feature_class, feature_code, country_iso2, admin1_code, population) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        conn.executemany(sql, cities)
        conn.executemany(sql, admin_rows)
        conn.executemany(sql, country_out)
        conn.execute("INSERT INTO gazetteer_fts(gazetteer_fts) VALUES ('rebuild')")
    stats.cities, stats.admin1, stats.countries = len(cities), len(admin_rows), len(country_out)
    log.info("gazetteer loaded cities=%d admin1=%d countries=%d skipped=%d", stats.cities, stats.admin1, stats.countries, stats.skipped)
    return stats


def _italian_names(iso3: str) -> list[str]:
    try:
        from eww import lexicon

        return list(lexicon.load().country_names_it.get(iso3, []))
    except Exception:  # pragma: no cover - the lexicon is optional here
        return []


def gazetteer_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM gazetteer_place").fetchone()[0]
