"""Deterministic extraction (M3): lexicon -> NER -> geocoder -> date, no model.

    extract_document(conn, doc, geocoder) -> Extraction   and writes document_extraction

(a) The hazard lexicon (eww.lexicon) classifies title + excerpt in English and Italian, negative
    patterns included; a document the lexicon cannot classify gets an extraction row with hazard_type
    NULL and no further work, since attach_document() ignores it anyway.
(b) spaCy NER: xx_ent_wiki_sm (LOC) on every text, plus en_core_web_sm (GPE, LOC, FAC) on English text.
    Country names and demonyms in the text supply the geocoder's country hint; the country of the event
    whose query fetched the document is the fallback hint.
(c) The geocoder resolves each place name through its tiers; remote tiers stop once one place is located.
(d) The publication date comes from the API (document.published_at), never from the text.

document_extraction.method is 'lexicon+ner'; places is a JSON list of
{name, country_hint, country_iso3, lat, lon, precision, provider}, unresolved names included with
precision 'unresolved' so a reviewer can see what NER found.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

from eww import config, events, geocode, lexicon
from eww.clock import now_iso

log = logging.getLogger(__name__)

_ENGLISH = {"english", "en", "eng"}


@dataclass
class Extraction:
    document_id: str
    hazard_type: str | None
    hazard_confidence: float
    places: list[dict] = field(default_factory=list)
    country_hints: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # every place name NER produced, in order
    event_date: str | None = None
    hits: dict = field(default_factory=dict)

    @property
    def located(self) -> list[dict]:
        return [p for p in self.places if p.get("lat") is not None]


@dataclass
class ExtractStats:
    documents: int = 0
    classified: int = 0
    located: int = 0
    names: int = 0
    geocode_lookups: int = 0
    cache_hits: int = 0
    remote_calls: int = 0


# ----------------------------------------------------------------------------- NER, lazily loaded
_pipelines: dict[str, object] = {}
_ner_override = None


def set_ner(function) -> None:
    """Tests replace spaCy with a function (text, language) -> list[str]."""
    global _ner_override
    _ner_override = function


def _pipeline(model: str):
    if model not in _pipelines:
        try:
            import spacy
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("spaCy is not installed: run `uv sync --all-groups` (the 'enrich' dependency group)") from exc
        try:
            _pipelines[model] = spacy.load(model, disable=["parser", "lemmatizer", "attribute_ruler", "tagger", "morphologizer"])
        except OSError as exc:
            raise RuntimeError(f"spaCy model {model!r} is not installed: run `uv sync --all-groups`") from exc
    return _pipelines[model]


def is_english(language: str | None) -> bool:
    return (language or "").strip().lower() in _ENGLISH


def ner_places(text: str, language: str | None) -> list[str]:
    """Place names in order of first appearance: the multilingual model for every text, the English one for English."""
    if _ner_override is not None:
        return _ner_override(text, language)
    if not text or not text.strip():
        return []
    models = [config.SPACY_MODELS["xx"]] + ([config.SPACY_MODELS["en"]] if is_english(language) else [])
    found: list[tuple[int, str]] = []
    for model in models:
        labels = set(config.NER_LABELS.get(model, ("LOC",)))
        doc = _pipeline(model)(text[:5000])
        for ent in doc.ents:
            if ent.label_ in labels:
                found.append((ent.start_char, ent.text))
    names: list[str] = []
    seen: set[str] = set()
    for _, name in sorted(found):
        cleaned = clean_name(name)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            names.append(cleaned)
    return names


def clean_name(name: str) -> str | None:
    text = " ".join(str(name).split()).strip(" \t\n'\"“”‘’.,;:!?()[]|")
    for prefix in ("the ", "The ", "l'", "L'", "la ", "il ", "lo ", "le ", "gli "):
        if text.startswith(prefix) and len(text) > len(prefix) + 2:
            text = text[len(prefix):]
    if len(text) < config.GEOCODE_MIN_NAME_CHARS or text.isdigit():
        return None
    if lexicon.fold(text) in _HAZARD_NAMES():
        return None
    return text


@lru_cache(maxsize=1)
def _HAZARD_NAMES() -> set[str]:
    words: set[str] = set()
    lex = lexicon.load()
    for hazard in lex.positives:
        for _, term, _ in lex.positives[hazard]:
            words.add(lexicon.fold(term))
    return words


# ----------------------------------------------------------------------------- one document
def document_text(doc: sqlite3.Row) -> str:
    title = doc["title"] or ""
    excerpt = doc["text_excerpt"] or ""
    return f"{title}. {excerpt}".strip(". ") if excerpt else title


def retrieval_context(conn: sqlite3.Connection, document_id: str) -> tuple[str | None, str | None]:
    """(hazard_type, country_iso3) of the highest-severity event whose query fetched the document."""
    row = conn.execute(
        """
        SELECT e.hazard_type, e.country_iso3 FROM document_retrieval r
        JOIN event e ON e.event_id = r.event_id
        WHERE r.document_id = ?
        ORDER BY COALESCE(e.severity_score, 0) DESC, r.retrieved_at, e.event_id LIMIT 1
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        return None, None
    return row["hazard_type"], row["country_iso3"]


def extract_document(conn: sqlite3.Connection, doc: sqlite3.Row, geocoder: geocode.Geocoder | None, *, write: bool = True) -> Extraction:
    text = document_text(doc)
    prefer_hazard, fallback_country = retrieval_context(conn, doc["document_id"])
    classification = lexicon.classify(text, prefer=prefer_hazard)
    extraction = Extraction(doc["document_id"], classification.hazard_type, classification.confidence, event_date=doc["published_at"], hits=classification.hits)
    if classification.hazard_type is not None:
        extraction.country_hints = lexicon.country_hints(text)
        names = ner_places(text, doc["language"])
        if not names and extraction.country_hints:
            from eww import countries

            names = [n for n in (countries.name_for(iso3) for iso3 in extraction.country_hints[:2]) if n]
        extraction.names = names[: config.EXTRACT_MAX_PLACES]
        hint = extraction.country_hints[0] if extraction.country_hints else fallback_country
        located_any = False
        if geocoder is not None:
            for name in extraction.names:
                place = geocoder.lookup(name, hint, remote=not located_any)
                if place is None:
                    extraction.places.append({"name": name, "country_hint": hint, "country_iso3": None, "lat": None, "lon": None, "precision": "unresolved", "provider": None})
                    continue
                located_any = True
                extraction.places.append({
                    "name": name,
                    "country_hint": hint,
                    "country_iso3": place.country_iso3,
                    "lat": place.lat,
                    "lon": place.lon,
                    "precision": place.precision,
                    "provider": place.provider,
                    "display_name": place.display_name,
                })
    if write:
        write_extraction(conn, extraction)
    return extraction


def write_extraction(conn: sqlite3.Connection, extraction: Extraction) -> None:
    conn.execute(
        """
        INSERT INTO document_extraction (document_id, method, model_version, hazard_type, hazard_confidence, places, event_date, figures, extracted_at, cost_usd)
        VALUES (:document_id, :method, :model_version, :hazard_type, :hazard_confidence, :places, :event_date, NULL, :extracted_at, 0)
        ON CONFLICT(document_id) DO UPDATE SET method = excluded.method, model_version = excluded.model_version,
            hazard_type = excluded.hazard_type, hazard_confidence = excluded.hazard_confidence, places = excluded.places,
            event_date = excluded.event_date, extracted_at = excluded.extracted_at
        """,
        {
            "document_id": extraction.document_id,
            "method": config.EXTRACT_METHOD,
            "model_version": f"{config.SPACY_MODELS['xx']}+{config.SPACY_MODELS['en']}",
            "hazard_type": extraction.hazard_type,
            "hazard_confidence": extraction.hazard_confidence,
            "places": json.dumps(extraction.places, ensure_ascii=False),
            "event_date": extraction.event_date,
            "extracted_at": now_iso(),
        },
    )


def read_extraction(conn: sqlite3.Connection, document_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM document_extraction WHERE document_id = ?", (document_id,)).fetchone()


# ----------------------------------------------------------------------------- the run
def pending_documents(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT d.* FROM document d
        WHERE d.removed_at IS NULL AND NOT EXISTS (SELECT 1 FROM document_extraction x WHERE x.document_id = d.document_id)
        ORDER BY d.document_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def run(conn: sqlite3.Connection, *, geocoder: geocode.Geocoder | None = None, limit: int | None = None, remote: bool = True) -> ExtractStats:
    """Extract every document without an extraction row, oldest first. Idempotent."""
    stats = ExtractStats()
    own = geocoder is None
    geocoder = geocoder or geocode.Geocoder(conn, tiers=config.GEOCODE_TIERS if remote else ["gazetteer"])
    try:
        for doc in pending_documents(conn, limit):
            with conn:
                extraction = extract_document(conn, doc, geocoder)
            stats.documents += 1
            stats.classified += 1 if extraction.hazard_type else 0
            stats.located += 1 if extraction.located else 0
            stats.names += len(extraction.names)
    finally:
        stats.geocode_lookups, stats.cache_hits, stats.remote_calls = geocoder.lookups, geocoder.cache_hits, geocoder.remote_calls
        if own:
            geocoder.close()
    log.info("extract documents=%d classified=%d located=%d names=%d lookups=%d cache_hits=%d remote=%d", stats.documents, stats.classified, stats.located, stats.names, stats.geocode_lookups, stats.cache_hits, stats.remote_calls)
    return stats


def event_country(conn: sqlite3.Connection, event_id: str) -> str | None:
    row = conn.execute("SELECT country_iso3 FROM event WHERE event_id = ?", (events.canonical_event_id(conn, event_id),)).fetchone()
    return row["country_iso3"] if row else None
