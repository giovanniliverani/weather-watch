"""Write tests/golden/*.jsonl. Not part of the pipeline; re-run if a label changes.

The hazard word in each excerpt is a strong lexicon term, checked here so a label cannot drift from it.
"""

from __future__ import annotations

import json
from pathlib import Path

from eww import lexicon

ROOT = Path(__file__).resolve().parent

# hazard, term that the lexicon must accept, city, iso3, iso2, lat, lon, dead, injured, missing, displaced
HAZARDS = [
    ("flood", "flood", "Bologna", "ITA", "IT", 44.4949, 11.3426, 3, 12, None, 400),
    ("flood", "flood", "Kathmandu", "NPL", "NP", 27.7172, 85.3240, 14, None, 2, None),
    ("flood", "flood", "Lisbon", "PRT", "PT", 38.7223, -9.1393, 1, 6, None, None),
    ("tropical_cyclone", "hurricane", "Miami", "USA", "US", 25.7617, -80.1918, 5, 20, None, 1000),
    ("tropical_cyclone", "typhoon", "Osaka", "JPN", "JP", 34.6937, 135.5023, 8, None, None, 300),
    ("tropical_cyclone", "cyclone", "Toamasina", "MDG", "MG", -18.1492, 49.4023, 2, 4, 1, None),
    ("severe_storm", "thunderstorm", "Dallas", "USA", "US", 32.7767, -96.7970, None, 7, None, None),
    ("severe_storm", "tornado", "Tulsa", "USA", "US", 36.1540, -95.9928, 4, 15, None, None),
    ("severe_storm", "hailstorm", "Stuttgart", "DEU", "DE", 48.7758, 9.1829, None, 3, None, None),
    ("wildfire", "wildfire", "Athens", "GRC", "GR", 37.9838, 23.7275, 2, 9, None, 500),
    ("wildfire", "wildfire", "Valparaiso", "CHL", "CL", -33.0472, -71.6127, 6, None, 1, None),
    ("heatwave", "heatwave", "Seville", "ESP", "ES", 37.3891, -5.9845, 11, None, None, None),
    ("heatwave", "heatwave", "Phoenix", "USA", "US", 33.4484, -112.0740, 9, 30, None, None),
    ("coldwave", "blizzard", "Helsinki", "FIN", "FI", 60.1699, 24.9384, 1, 4, None, None),
    ("coldwave", "blizzard", "Sapporo", "JPN", "JP", 43.0618, 141.3545, None, 2, None, 50),
    ("drought", "drought", "Nairobi", "KEN", "KE", -1.2921, 36.8219, None, None, None, 800),
    ("drought", "drought", "Windhoek", "NAM", "NA", -22.5609, 17.0658, None, None, None, 200),
    ("landslide", "landslide", "Quito", "ECU", "EC", -0.1807, -78.4678, 7, 3, 2, None),
    ("landslide", "mudslide", "Freetown", "SLE", "SL", 8.4657, -13.2317, 12, None, 4, None),
    ("volcano", "eruption", "Catania", "ITA", "IT", 37.5079, 15.0830, None, 5, None, 150),
    ("volcano", "eruption", "Legazpi", "PHL", "PH", 13.1391, 123.7438, 1, None, None, 60),
    ("earthquake", "earthquake", "Antakya", "TUR", "TR", 36.2023, 36.1606, 20, 80, 6, None),
    ("earthquake", "earthquake", "Sendai", "JPN", "JP", 38.2682, 140.8694, 10, 40, None, 700),
    ("earthquake", "earthquake", "Christchurch", "NZL", "NZ", -43.5321, 172.6362, 2, 11, None, None),
    ("tsunami", "tsunami", "Sendai", "JPN", "JP", 38.2682, 140.8694, 15, None, 3, None),
    ("tsunami", "tsunami", "Banda", "IDN", "ID", -4.5236, 129.8981, 4, 1, None, None),
    ("tsunami", "tsunami", "Hilo", "USA", "US", 19.7297, -155.0900, None, 2, None, None),
]

QUIET = [
    ("Bergen", "NOR", "NO", 60.3913, 5.3221, "Museum hours in Bergen", "The museum in Bergen opens at ten."),
    ("Geneva", "CHE", "CH", 46.2044, 6.1432, "Chamber concert in Geneva", "A chamber concert opens in Geneva next month."),
    ("Brno", "CZE", "CZ", 49.1951, 16.6068, "Tram timetable in Brno", "The tram timetable in Brno changes in spring."),
]


def _clause(value, word: str) -> str | None:
    if value is None:
        return None
    return f"{value} {word}"


def hazard_row(index: int, spec: tuple) -> dict:
    hazard, term, city, iso3, iso2, lat, lon, dead, injured, missing, displaced = spec
    title = f"{term[0].upper()}{term[1:]} in {city}"
    bits = [f"The {term} in {city}"]
    counts = [c for c in (_clause(dead, "dead"), _clause(injured, "injured"), _clause(missing, "missing"), _clause(displaced, "displaced")) if c]
    excerpt = bits[0] + (" left " + ", ".join(counts) + "." if counts else " continues.")
    text = f"{title}. {excerpt}"
    found = lexicon.classify(text).hazard_type
    if found != hazard:
        raise SystemExit(f"{title!r} classified as {found}, expected {hazard}")
    return {
        "document_id": f"doc-{index:02d}",
        "title": title,
        "text_excerpt": excerpt,
        "language": "en",
        "places": [{"name": city, "country_hint": iso3, "lat": lat, "lon": lon, "iso2": iso2}],
        "candidates": [{
            "event_id": f"cand-{index:02d}",
            "hazard_type": hazard,
            "title": title,
            "lat": lat,
            "lon": lon,
            "country_iso3": iso3,
            "started_at": "2026-09-01T00:00:00Z",
        }],
        "expected": {
            "hazard_type": hazard,
            "figures": {"dead": dead, "injured": injured, "missing": missing, "displaced": displaced},
        },
    }


def quiet_row(index: int, spec: tuple) -> dict:
    city, iso3, iso2, lat, lon, title, excerpt = spec
    found = lexicon.classify(f"{title}. {excerpt}").hazard_type
    if found is not None:
        raise SystemExit(f"{title!r} classified as {found}, expected null")
    return {
        "document_id": f"doc-{index:02d}",
        "title": title,
        "text_excerpt": excerpt,
        "language": "en",
        "places": [{"name": city, "country_hint": iso3, "lat": lat, "lon": lon, "iso2": iso2}],
        "candidates": [{
            "event_id": f"cand-{index:02d}",
            "hazard_type": "flood",
            "title": f"Flood near {city}",
            "lat": lat,
            "lon": lon,
            "country_iso3": iso3,
            "started_at": "2026-09-01T00:00:00Z",
        }],
        "expected": {"hazard_type": None, "figures": {"dead": None, "injured": None, "missing": None, "displaced": None}},
    }


def event_row(index: int, spec: tuple) -> dict:
    hazard, term, city, iso3, iso2, lat, lon, dead, injured, missing, displaced = spec
    counts = [c for c in (_clause(dead, "dead"), _clause(injured, "injured"), _clause(missing, "missing"), _clause(displaced, "displaced")) if c]
    authority = f"GDACS reports the {term} in {city}" + (f" left {', '.join(counts)}." if counts else ".")
    docs = []
    for n in range(3):
        docs.append({
            "document_id": f"ev{index:02d}-doc-{n}",
            "title": f"{term[0].upper()}{term[1:]} in {city}",
            "text_excerpt": authority,
            "score": 0.9 - n * 0.05,
        })
    return {
        "event_id": f"ev-{index:02d}",
        "title": f"{term[0].upper()}{term[1:]} in {city}",
        "hazard_type": hazard,
        "started_at": "2026-09-01T00:00:00Z",
        "authority_text": authority,
        "documents": docs,
        "expected": {"figures": {"dead": dead, "injured": injured, "missing": missing, "displaced": displaced}},
    }


def main() -> None:
    documents = [hazard_row(i, spec) for i, spec in enumerate(HAZARDS, start=1)]
    documents += [quiet_row(i, spec) for i, spec in enumerate(QUIET, start=len(documents) + 1)]
    events = [event_row(i, spec) for i, spec in enumerate(HAZARDS[:10], start=1)]
    if len(documents) != 30 or len(events) != 10:
        raise SystemExit(f"expected 30 documents and 10 events, got {len(documents)} and {len(events)}")
    (ROOT / "documents.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in documents), encoding="utf-8")
    (ROOT / "events.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in events), encoding="utf-8")
    print(f"wrote {len(documents)} documents and {len(events)} events")


if __name__ == "__main__":
    main()
