"""Country lookups backed by eww/data/countries.csv (GeoNames countryInfo.txt, CC BY 4.0)."""

from __future__ import annotations

import csv
from functools import lru_cache
from typing import NamedTuple

from eww import config


class Country(NamedTuple):
    iso3: str
    continent: str
    name: str


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, Country], dict[str, str]]:
    by_iso3: dict[str, Country] = {}
    by_name: dict[str, str] = {}
    with config.COUNTRIES_CSV.open(encoding="utf-8", newline="") as handle:
        rows = (line for line in handle if not line.startswith("#"))
        for row in csv.DictReader(rows):
            country = Country(row["iso3"].strip().upper(), row["continent"].strip(), row["country"].strip())
            by_iso3[country.iso3] = country
            by_name[country.name.casefold()] = country.iso3
    for alias, iso3 in config.COUNTRY_NAME_ALIASES.items():
        by_name.setdefault(alias.casefold(), iso3)
    return by_iso3, by_name


def continent_for(iso3: str | None) -> str | None:
    if not iso3:
        return None
    country = _tables()[0].get(iso3.strip().upper())
    return country.continent if country else None


def name_for(iso3: str | None) -> str | None:
    if not iso3:
        return None
    country = _tables()[0].get(iso3.strip().upper())
    return country.name if country else None


def iso3_for_name(name: str | None) -> str | None:
    """Match a country name as a feed spells it; 'A, B' resolves to A. None when unknown."""
    if not name:
        return None
    first = name.split(",")[0].strip()
    if not first or first.startswith("["):
        return None
    return _tables()[1].get(first.casefold())


def all_continents() -> list[str]:
    return sorted({c.continent for c in _tables()[0].values()})
