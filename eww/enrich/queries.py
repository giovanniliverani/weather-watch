"""What an event is called in the news: the terms the enrichment collectors query with.

`event_terms()` reads an event and its records and returns the country names (English, plus the Italian
spelling from the lexicon), the admin1-level place names its titles carry ("Wildfire in Huelva Province,
Spain" -> Huelva; "Wildfire Snow, Custer, Montana" -> Custer, Montana), the storm name if it is a named
storm, and the hazard keywords of the lexicon in both languages. `gdelt_query()` turns them into the
GDELT DOC query language: `(Huelva OR Spain OR Spagna) (wildfire OR bushfire OR "incendio boschivo")`;
GDELT allows parentheses only around OR'd statements, so a group with one term goes bare (`Nepal (flood OR ...)`).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from eww import config, countries, events, lexicon, matching

_HAZARD_TITLE_WORDS = {
    "flood", "floods", "flooding", "wildfire", "wildfires", "forest", "fires", "fire", "drought", "storm", "tropical", "cyclone",
    "hurricane", "typhoon", "earthquake", "volcano", "volcanic", "eruption", "landslide", "tsunami", "heat", "heatwave", "cold",
    "wave", "snow", "severe", "in", "the", "of", "and", "near", "off", "coast",
}
_ADMIN_SUFFIXES = re.compile(
    r"\b(province|provincia|region|regione|prefecture|district|distretto|state|county|contea|department|departamento|governorate|"
    r"oblast|island|islands|isola|isole|municipality|comune|city|town|area|autonomous community|comunidad|canton|voivodeship)\b\.?",
    re.IGNORECASE,
)
_ISLAND_OF = re.compile(r"^(island|islands|isola|isole|province|region|state|city)\s+of\s+", re.IGNORECASE)
_ID_SUFFIX = re.compile(r"\s+\d+\s*$")


@dataclass
class EventTerms:
    hazard_type: str
    countries_iso3: list[str] = field(default_factory=list)
    countries_en: list[str] = field(default_factory=list)
    countries_it: list[str] = field(default_factory=list)
    admin1: list[str] = field(default_factory=list)
    storm_name: str | None = None

    @property
    def place_terms(self) -> list[str]:
        """Most specific first: storm name, admin1 names, then country names in both languages."""
        terms: list[str] = []
        if self.storm_name:
            terms.append(self.storm_name)
        terms += self.admin1
        for en, it in zip(self.countries_en, self.countries_it + [""] * len(self.countries_en)):
            terms.append(en)
            if it and it.casefold() != en.casefold():
                terms.append(it)
        return list(dict.fromkeys(t for t in terms if t))


def _clean_part(part: str) -> str:
    text = _ID_SUFFIX.sub("", part.strip().strip("."))
    text = _ISLAND_OF.sub("", text)
    text = _ADMIN_SUFFIXES.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ,-")
    return text


def places_from_title(title: str | None) -> list[str]:
    """Comma-separated place parts of a feed title that are neither the hazard words nor a number."""
    if not title:
        return []
    text = _ID_SUFFIX.sub("", title.strip())
    rest = text.split(" in ", 1)[1] if " in " in f" {text} " and " in " in text else None
    if rest is None:
        parts = text.split(",")
        parts = parts[1:] if len(parts) > 1 else []  # "Wildfire Snow, Custer, Montana": the first part names the fire
    else:
        parts = rest.split(",")
    out: list[str] = []
    for part in parts:
        cleaned = _clean_part(part)
        if not cleaned or any(ch.isdigit() for ch in cleaned) or len(cleaned) < 3:
            continue
        if cleaned.lower() in _HAZARD_TITLE_WORDS:
            continue
        out.append(cleaned)
    return list(dict.fromkeys(out))


def storm_display_name(raw: str | None) -> str | None:
    """'SAUDEL-26' -> 'Saudel', 'Hurricane Karina' -> 'Karina'; None for a numbered depression ('TWO-C-26')."""
    if not raw:
        return None
    text = str(raw).strip().split(" in ")[0]
    text = config.STORM_NAME_YEAR_SUFFIX_RE.sub("", text.strip())
    words = [w for w in re.split(r"[^A-Za-z\-]+", text) if w and w.lower() not in config.STORM_WORDS]
    if not words:
        return None
    name = words[-1].strip("-")
    if len(name) < 3 or "-" in name and name.split("-")[-1].isupper() and len(name.split("-")[-1]) == 1:
        return None
    numbers = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty"}
    lowered = name.lower()
    if any(lowered.startswith(n) for n in numbers):
        return None
    return name.title()


def event_terms(conn: sqlite3.Connection, event: sqlite3.Row) -> EventTerms:
    terms = EventTerms(hazard_type=event["hazard_type"])
    recs = events.event_records(conn, event["event_id"])
    iso3s: list[str] = []
    titles: list[str] = [event["title"] or ""]
    storm_raw: str | None = None
    for rec in recs:
        payload = events.payload_of(rec)
        collector = __import__("eww.collectors", fromlist=["get"]).get(rec["source_id"])
        iso3 = collector.country_iso3(payload)
        if iso3:
            iso3s.append(iso3)
        if rec["source_id"] == "gdacs":
            for country in (payload.get("properties") or {}).get("affectedcountries") or []:
                if country.get("iso3"):
                    iso3s.append(str(country["iso3"]).upper())
        titles.append(rec["title"] or "")
        if matching.hazard_class(event["hazard_type"]) == "storm" and storm_raw is None:
            storm_raw = getattr(collector, "storm_name", lambda p: None)(payload)
    if event["country_iso3"]:
        iso3s.insert(0, event["country_iso3"])
    # country names inside titles ("Drought in Kenya, Tanzania, Uganda")
    for title in titles:
        for part in places_from_title(title):
            iso3 = countries.iso3_for_name(part)
            if iso3:
                iso3s.append(iso3)
    terms.countries_iso3 = list(dict.fromkeys(i for i in iso3s if i))[:5]
    lex = lexicon.load()
    for iso3 in terms.countries_iso3:
        name = countries.name_for(iso3)
        if name:
            terms.countries_en.append(name)
            terms.countries_it.append((lex.country_names_it.get(iso3) or [""])[0])
    admin: list[str] = []
    for title in titles:
        for part in places_from_title(title):
            if countries.iso3_for_name(part) or part.lower() in _HAZARD_TITLE_WORDS:
                continue
            admin.append(part)
    terms.admin1 = list(dict.fromkeys(admin))[:4]
    terms.storm_name = storm_display_name(storm_raw)
    return terms


def _quote(term: str) -> str:
    text = term.replace('"', "").strip()
    return f'"{text}"' if (" " in text or "-" in text or "'" in text) else text


def gdelt_query(terms: EventTerms) -> str | None:
    """The GDELT DOC query for an event, or None when the event names no place and no storm."""
    places = terms.place_terms[: config.GDELT_MAX_PLACE_TERMS]
    hazards = lexicon.load().query_terms(terms.hazard_type)[: config.GDELT_MAX_HAZARD_TERMS]
    if not places or not hazards:
        return None
    return f"{_group(places)} {_group(hazards)}"


def _group(terms: list[str]) -> str:
    """GDELT allows parentheses only around OR'd statements: a single term goes bare."""
    quoted = [_quote(t) for t in terms]
    return quoted[0] if len(quoted) == 1 else "(" + " OR ".join(quoted) + ")"
