"""Model extraction and summaries (M4). Local by default, cloud only under the cap.

    extract(document, candidate_events) -> Extraction
    summarise(event, authority_text, documents) -> Summary

`EWW_LLM_BACKEND` selects the implementation (`local`, the default, `ollama`, or `anthropic`).
Each returns pydantic models. A second check runs before anything is written: the hazard must agree
with the lexicon when the lexicon found one, a place is kept only when it geocodes within twice the
candidate's blocking radius, and a figure is kept only with an evidence span copied verbatim from
the source text. Anything that fails becomes null.

The local backend does not generate. It reads a figure from a number that already sits next to
"dead", "injured", "missing" or "displaced", proposes place names that occur in the text, and
builds a summary out of the authority text those spans came from. Ollama remains available as one
chat request per document. Claude Sonnet 5 goes through the Message Batches API with a cached
system prefix and `output_config` (`format` plus `effort: low`). Before that batch is created,
month-to-date `llm_call.cost_usd` plus an estimate of the batch is compared with
`EWW_LLM_BUDGET_USD`. Over the cap, or with the cap at 0, the cloud client is never called and the
local backend runs instead.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from eww import config, countries, db, documents, extract, geo, geocode, lexicon, matching
from eww.clock import now_iso, now_utc, parse_iso, to_iso
from eww.ids import new_id

log = logging.getLogger(__name__)

try:
    import anthropic
except ImportError:  # optional: only the cloud backend imports it
    anthropic = None

_DIGIT = re.compile(r"\d")
_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
FIGURE_KEYS = ("dead", "injured", "missing", "displaced")


class LLMError(RuntimeError):
    """A model call failed. `retryable` means the document is left for the next run."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


# ----------------------------------------------------------------------------- output shape
class Figure(BaseModel):
    model_config = ConfigDict(extra="ignore")
    value: float
    evidence_span: str

    @field_validator("value", mode="before")
    @classmethod
    def _number(cls, value: Any) -> float:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)


class Figures(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dead: Figure | None = None
    injured: Figure | None = None
    missing: Figure | None = None
    displaced: Figure | None = None


class PlaceName(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    country_hint: str | None = None

    @field_validator("country_hint", mode="before")
    @classmethod
    def _hint(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class Extraction(BaseModel):
    """What `extract` returns, before the source-text and geocoding checks."""

    model_config = ConfigDict(extra="ignore")
    hazard_type: str | None = None
    places: list[PlaceName] = Field(default_factory=list)
    event_date: str | None = None
    figures: Figures = Field(default_factory=Figures)
    confidence: float = 0

    @field_validator("hazard_type", "event_date", mode="before")
    @classmethod
    def _blank(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("figures", mode="before")
    @classmethod
    def _figures(cls, value: Any) -> Any:
        return value or {}

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float:
        if value is None or value == "":
            return 0.0
        return float(value)


class SummarySentence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    evidence_span: str | None = None

    @field_validator("evidence_span", mode="before")
    @classmethod
    def _span(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class Summary(BaseModel):
    """What `summarise` returns, before sentences with untraceable numbers are dropped."""

    model_config = ConfigDict(extra="ignore")
    sentences: list[SummarySentence] = Field(default_factory=list)
    figures: Figures = Field(default_factory=Figures)

    @field_validator("figures", mode="before")
    @classmethod
    def _figures(cls, value: Any) -> Any:
        return value or {}


def _schema(model: type[BaseModel]) -> dict:
    """A JSON schema both Ollama (`format`) and Claude (`output_config.format`) can constrain to."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


EXTRACTION_SCHEMA = _schema(Extraction)
SUMMARY_SCHEMA = _schema(Summary)


def _hazard_enum_note() -> str:
    return ", ".join(config.HAZARD_TYPES)


# The cached prefix has to clear Sonnet 5's 1,024-token minimum (Anthropic prompt-caching docs).
# The padding is a stable reminder, not new instructions, so every request in a batch hashes the same prefix.
_PREFIX_PAD = (
    "Copy evidence_span character for character from the title, the excerpt or the authority text. "
    "Do not paraphrase, translate, round or retype a number inside the span. "
    "If you cannot copy such a span, the figure is null. A sentence that contains a digit and has no copied span is omitted.\n"
)


def estimate_tokens(text: str) -> int:
    """A cheap stand-in for a tokenizer: four characters to a token, used for the cache floor and the budget estimate."""
    return max(1, (len(text) + 3) // 4)


def system_prefix(purpose: str) -> str:
    """Fixed instructions, the schema and three examples. Identical for every request of this purpose."""
    if purpose == "summarise":
        task = (
            "You write a short summary of one hazard event for a private map. "
            f"Return at most {config.LLM_SUMMARY_MAX_SENTENCES} sentences plus casualty figures. "
            "Start from the authority text (GDACS, EONET, Copernicus or ReliefWeb). "
            "Use the attached documents only to add a fact the authority text does not already state. "
            "Every sentence that contains a digit must include evidence_span copied verbatim from one of the inputs. "
            "Drop a number you cannot point at."
        )
        schema = SUMMARY_SCHEMA
        examples = _SUMMARY_EXAMPLES
    else:
        task = (
            "You extract structured fields from one document about a natural hazard. "
            "hazard_type is one of the allowed names, or null when the document is not about a hazard. "
            "places are town, region or country names that the text actually states, each with a country_hint "
            "as an ISO 3166-1 alpha-3 code when you know it, otherwise null. "
            "event_date is an ISO date (YYYY-MM-DD) stated by the text, or null. "
            "figures are dead, injured, missing and displaced. Each is null, or an object with an integer value "
            "and evidence_span copied verbatim from the title or the excerpt. "
            "confidence is your certainty from 0 to 1. Do not invent a place, a date or a number."
        )
        schema = EXTRACTION_SCHEMA
        examples = _EXTRACT_EXAMPLES
    body = (
        f"{task}\n\n"
        f"Allowed hazard_type values: {_hazard_enum_note()}.\n\n"
        "The JSON object must match this schema. Extra keys are ignored. Use null, not a guess, when a field is absent.\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "Examples:\n"
        f"{examples}\n"
    )
    while estimate_tokens(body) < config.LLM_PREFIX_MIN_TOKENS:
        body += _PREFIX_PAD
    return body


_EXTRACT_EXAMPLES = """
Example 1
title: Flood in Bologna
excerpt: The flood in Bologna left 3 dead and 12 injured.
{"hazard_type":"flood","places":[{"name":"Bologna","country_hint":"ITA"}],"event_date":null,"figures":{"dead":{"value":3,"evidence_span":"3 dead"},"injured":{"value":12,"evidence_span":"12 injured"},"missing":null,"displaced":null},"confidence":0.9}

Example 2
title: Library budget in Lyon
excerpt: The council approved a new library budget in Lyon on Tuesday.
{"hazard_type":null,"places":[{"name":"Lyon","country_hint":"FRA"}],"event_date":null,"figures":{"dead":null,"injured":null,"missing":null,"displaced":null},"confidence":0.2}

Example 3
title: Earthquake in Quito
excerpt: The earthquake in Quito on 2026-03-04 left 8 dead, 40 injured, 1 missing and 200 displaced.
{"hazard_type":"earthquake","places":[{"name":"Quito","country_hint":"ECU"}],"event_date":"2026-03-04","figures":{"dead":{"value":8,"evidence_span":"8 dead"},"injured":{"value":40,"evidence_span":"40 injured"},"missing":{"value":1,"evidence_span":"1 missing"},"displaced":{"value":200,"evidence_span":"200 displaced"}},"confidence":0.95}
"""

_SUMMARY_EXAMPLES = """
Example 1
authority: GDACS reports the flood in Bologna left 3 dead and 12 injured.
{"sentences":[{"text":"The flood in Bologna left 3 dead and 12 injured.","evidence_span":"3 dead and 12 injured"}],"figures":{"dead":{"value":3,"evidence_span":"3 dead"},"injured":{"value":12,"evidence_span":"12 injured"},"missing":null,"displaced":null}}

Example 2
authority: Copernicus activated mapping for a wildfire near Coimbra.
{"sentences":[{"text":"Copernicus activated mapping for a wildfire near Coimbra.","evidence_span":null}],"figures":{"dead":null,"injured":null,"missing":null,"displaced":null}}

Example 3
authority: The tsunami warning for Suva was lifted with no deaths reported.
{"sentences":[{"text":"The tsunami warning for Suva was lifted with no deaths reported.","evidence_span":"no deaths reported"}],"figures":{"dead":null,"injured":null,"missing":null,"displaced":null}}
"""


def user_extract(document: Any, candidates: list[dict]) -> str:
    lines = [
        "Document",
        f"title: {_field(document, 'title') or ''}",
        f"excerpt: {_field(document, 'text_excerpt') or ''}",
        f"language: {_field(document, 'language') or ''}",
        f"published_at: {_field(document, 'published_at') or ''}",
        "",
        "Candidate events. Keep a place only when it belongs to one of these; do not invent an event.",
    ]
    for event in candidates[: config.LLM_CANDIDATES]:
        lines.append(
            "- {event_id} | {hazard_type} | {title} | {lat}, {lon} | {country} | {started}".format(
                event_id=event.get("event_id") or "",
                hazard_type=event.get("hazard_type") or "",
                title=event.get("title") or "",
                lat=event.get("lat"),
                lon=event.get("lon"),
                country=event.get("country_iso3") or "",
                started=event.get("started_at") or "",
            )
        )
    if not candidates:
        lines.append("- none")
    return "\n".join(lines)


def user_summary(event: Any, authority_text: str, docs: list[Any]) -> str:
    lines = [
        "Event",
        f"title: {_field(event, 'title') or ''}",
        f"hazard_type: {_field(event, 'hazard_type') or ''}",
        f"started_at: {_field(event, 'started_at') or ''}",
        "",
        "Authority text",
        authority_text or "(none)",
        "",
        "Attached documents, highest score first",
    ]
    for index, doc in enumerate(docs[: config.LLM_SUMMARY_MAX_DOCS], start=1):
        lines.append(f"{index}. title: {_field(doc, 'title') or ''}")
        lines.append(f"   excerpt: {_field(doc, 'text_excerpt') or ''}")
    if not docs:
        lines.append("(none)")
    return "\n".join(lines)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    try:
        return obj[name]
    except (KeyError, IndexError, TypeError):
        return default


# ----------------------------------------------------------------------------- cost and the cap
@dataclass
class Call:
    purpose: str
    backend: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0
    document_id: str | None = None
    event_id: str | None = None
    custom_id: str = ""
    error: str | None = None

    @property
    def cached_tokens(self) -> int:
        return self.cache_read_tokens + self.cache_write_tokens


def cost_usd(*, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """USD for one batched Claude call. Cache read is 0.1x and cache write is 1.25x, both after the 0.5 batch factor.

    `input_tokens` is the uncached input (Anthropic's usage.input_tokens). Ollama is always 0 and does not use this.
    """
    million = 1_000_000
    batch = config.LLM_BATCH_MULTIPLIER
    price_in = config.LLM_PRICE_INPUT_PER_M
    price_out = config.LLM_PRICE_OUTPUT_PER_M
    return (
        input_tokens * price_in / million * batch
        + cache_read_tokens * price_in / million * config.LLM_CACHE_READ_MULTIPLIER * batch
        + cache_write_tokens * price_in / million * config.LLM_CACHE_WRITE_MULTIPLIER * batch
        + output_tokens * price_out / million * batch
    )


def estimate_batch_usd(prefix: str, payloads: list[str]) -> float:
    """A high estimate: the whole prefix is priced as uncached input, so a cache miss cannot slip past the cap."""
    if not payloads:
        return 0.0
    prefix_tokens = estimate_tokens(prefix)
    million = 1_000_000
    price_in = config.LLM_PRICE_INPUT_PER_M / million * config.LLM_BATCH_MULTIPLIER
    price_out = config.LLM_PRICE_OUTPUT_PER_M / million * config.LLM_BATCH_MULTIPLIER
    total = 0.0
    for payload in payloads:
        total += (prefix_tokens + estimate_tokens(payload)) * price_in
        total += config.LLM_ESTIMATE_OUTPUT_TOKENS * price_out
    return total


def month_start(now: datetime | None = None) -> str:
    now = now or now_utc()
    return f"{now.year:04d}-{now.month:02d}-01T00:00:00Z"


def month_spend(conn: sqlite3.Connection, now: datetime | None = None) -> float:
    """The criterion's own query: SUM(cost_usd) for the current UTC month. Empty is 0."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_call WHERE called_at >= date('now', 'start of month')"
    ).fetchone()
    # `date('now')` follows the connection clock. Tests that inject `now` need the Python boundary instead,
    # and both agree when the process clock is UTC, which is how the CLI runs.
    if now is None:
        return float(row[0])
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_call WHERE called_at >= ?",
        (month_start(now),),
    ).fetchone()
    return float(row[0])


def budget_allows(conn: sqlite3.Connection, estimate: float, now: datetime | None = None) -> bool:
    """False when the cap is 0, or when month-to-date spend plus the estimate would cross it."""
    cap = config.LLM_BUDGET_USD
    if cap <= 0:
        return False
    if estimate <= 0:
        return True
    return month_spend(conn, now) + estimate <= cap


def write_call(conn: sqlite3.Connection, call: Call, *, when: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO llm_call (call_id, purpose, backend, model, input_tokens, cached_tokens, output_tokens, cost_usd, document_id, event_id, called_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            call.purpose,
            call.backend,
            call.model,
            call.input_tokens,
            call.cached_tokens,
            call.output_tokens,
            call.cost_usd,
            call.document_id,
            call.event_id,
            when or now_iso(),
        ),
    )


# ----------------------------------------------------------------------------- validation before a write
def plain(text: str | None) -> str:
    return " ".join(_TAG.sub(" ", text or "").split())


def span_in(span: str | None, texts: list[str]) -> bool:
    """True when `span` is a non-empty verbatim substring of one of the texts."""
    if not span or not span.strip():
        return False
    return any(span in text for text in texts if text)


def _as_number(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return float(value)


def accept_figures(figures: Figures | None, texts: list[str]) -> dict:
    """Each figure is kept only with a verbatim evidence span. The rest are null. A figure is never stored without one."""
    out: dict[str, dict | None] = {}
    for key in FIGURE_KEYS:
        figure = getattr(figures, key, None) if figures is not None else None
        if figure is None or not span_in(figure.evidence_span, texts):
            out[key] = None
            continue
        out[key] = {"value": _as_number(figure.value), "evidence_span": figure.evidence_span}
    return out


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _hint_iso3(hint: str | None) -> str | None:
    if not hint:
        return None
    if countries.iso2_for(hint):
        return hint.strip().upper()
    return countries.iso3_for_name(hint)


def _within_two_r(lat: float, lon: float, candidates: list[dict]) -> bool:
    for event in candidates:
        if event.get("lat") is None or event.get("lon") is None:
            continue
        block = matching.blocking_for(event.get("hazard_type"))
        radius = block[0] if block else config.aggregation_radius_km(event.get("hazard_type"))
        if geo.haversine_km(lat, lon, float(event["lat"]), float(event["lon"])) <= 2 * radius:
            return True
    return False


@dataclass
class AcceptedExtraction:
    hazard_type: str | None
    hazard_confidence: float | None
    places: list[dict]
    event_date: str | None
    figures: dict


def accept_extraction(
    raw: Extraction | None,
    *,
    title: str | None,
    excerpt: str | None,
    lexicon_hazard: str | None,
    lexicon_confidence: float | None,
    candidates: list[dict],
    geocoder: Any,
    fallback_date: str | None = None,
    remote: bool = True,
) -> AcceptedExtraction:
    """The checks that run before a row is written. A failing field becomes null; a lexicon hazard is kept."""
    texts = [title or "", excerpt or ""]
    if lexicon_hazard in config.HAZARD_TYPES:
        hazard: str | None = lexicon_hazard
        confidence = lexicon_confidence
    elif raw is not None and raw.hazard_type in config.HAZARD_TYPES:
        hazard = raw.hazard_type
        confidence = raw.confidence if 0 <= raw.confidence <= 1 else None
    else:
        hazard = None
        confidence = None
    places: list[dict] = []
    if raw is not None and geocoder is not None:
        for place in raw.places:
            name = " ".join((place.name or "").split())
            if len(name) < config.GEOCODE_MIN_NAME_CHARS:
                continue
            hint = _hint_iso3(place.country_hint)
            located = geocoder.lookup(name, hint, remote=remote)
            if located is None or located.lat is None:
                continue
            if not _within_two_r(located.lat, located.lon, candidates):
                continue
            places.append({
                "name": name,
                "country_hint": hint,
                "country_iso3": located.country_iso3,
                "lat": located.lat,
                "lon": located.lon,
                "precision": located.precision,
                "provider": located.provider,
                "display_name": located.display_name,
            })
    event_date = _iso_date(raw.event_date) if raw is not None else None
    if event_date is None:
        event_date = _iso_date(fallback_date) or (fallback_date if _iso_date((fallback_date or "")[:10]) else None)
        if event_date and len(event_date) > 10:
            event_date = _iso_date(event_date)
    return AcceptedExtraction(hazard, confidence, places, event_date, accept_figures(raw.figures if raw else None, texts))


def accept_summary(raw: Summary | None, texts: list[str]) -> Summary:
    """Drop any sentence that contains a digit but whose evidence span is not a verbatim substring. Keep at most three."""
    kept: list[SummarySentence] = []
    for sentence in raw.sentences if raw else []:
        text = " ".join((sentence.text or "").split())
        if not text:
            continue
        if _DIGIT.search(text) and not span_in(sentence.evidence_span, texts):
            continue
        kept.append(SummarySentence(text=text, evidence_span=sentence.evidence_span))
        if len(kept) >= config.LLM_SUMMARY_MAX_SENTENCES:
            break
    return Summary(sentences=kept, figures=Figures.model_validate(accept_figures(raw.figures if raw else None, texts)))


def summary_text(summary: Summary) -> str:
    return " ".join(sentence.text for sentence in summary.sentences).strip()


# ----------------------------------------------------------------------------- parsing model JSON
def parse_model(text: str, model: type[BaseModel]) -> BaseModel:
    body = _FENCE.sub("", (text or "").strip())
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON: {exc}", retryable=False) from exc
    try:
        return model.model_validate(payload)
    except Exception as exc:
        raise LLMError(f"model JSON did not match the schema: {exc}", retryable=False) from exc


def _usage_int(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        value = usage.get(name)
    else:
        value = getattr(usage, name, None)
    return int(value or 0)


def custom_id(purpose: str, row_id: str) -> str:
    """Batch results are matched on this id, never on position. The API allows 1–64 of [A-Za-z0-9_-]."""
    prefix = "x" if purpose == "extract" else "s"
    safe = re.sub(r"[^A-Za-z0-9_-]", "", row_id)[:62]
    return f"{prefix}-{safe}"[:64]


# ----------------------------------------------------------------------------- Ollama
class OllamaExtractor:
    """POST /api/chat. `format` is the JSON schema, temperature is 0, stream is off, cost is 0."""

    backend = "ollama"

    def __init__(self, client: httpx.Client | None = None):
        self.calls: list[Call] = []
        self.pulled = False
        self._client = client
        self._owns = client is None
        self._ready = False

    def close(self) -> None:
        if self._owns and self._client is not None:
            self._client.close()
            self._client = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=config.LLM_OLLAMA_TIMEOUT_S, trust_env=False)
        return self._client

    def ensure_model(self) -> None:
        if self._ready:
            return
        http = self._http()
        names = _ollama_models(http)
        wanted = config.LLM_OLLAMA_MODEL
        if not any(name == wanted or name.startswith(wanted + ":") for name in names):
            self.pulled = True
            log.warning("ollama model %s is missing; pulling it now", wanted)
            response = http.post(f"{config.LLM_OLLAMA_URL}/api/pull", json={"name": wanted, "stream": False}, timeout=config.LLM_OLLAMA_PULL_TIMEOUT_S)
            response.raise_for_status()
        self._ready = True

    def extract(self, document: Any, candidate_events: list[dict]) -> Extraction:
        return self._chat("extract", user_extract(document, candidate_events), EXTRACTION_SCHEMA, Extraction, document_id=_field(document, "document_id"))  # type: ignore[return-value]

    def summarise(self, event: Any, authority_text: str, docs: list[Any]) -> Summary:
        return self._chat("summarise", user_summary(event, authority_text, docs), SUMMARY_SCHEMA, Summary, event_id=_field(event, "event_id"))  # type: ignore[return-value]

    def extract_many(self, jobs: list[ExtractJob]) -> dict[str, Extraction | None]:
        return _many(self, jobs, "extract")

    def summarise_many(self, jobs: list[SummaryJob]) -> dict[str, Summary | None]:
        return _many(self, jobs, "summarise")

    def _chat(self, purpose: str, user: str, schema: dict, model: type[BaseModel], *, document_id: str | None = None, event_id: str | None = None) -> BaseModel:
        self.ensure_model()
        http = self._http()
        payload = {
            "model": config.LLM_OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_prefix(purpose)},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        try:
            response = http.post(f"{config.LLM_OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama request failed: {exc}", retryable=True) from exc
        text = ((body.get("message") or {}).get("content")) or ""
        call = Call(
            purpose=purpose,
            backend=self.backend,
            model=config.LLM_OLLAMA_MODEL,
            input_tokens=int(body.get("prompt_eval_count") or estimate_tokens(payload["messages"][0]["content"] + user)),
            output_tokens=int(body.get("eval_count") or estimate_tokens(text)),
            cost_usd=0,
            document_id=document_id,
            event_id=event_id,
            custom_id=custom_id(purpose, document_id or event_id or purpose),
        )
        try:
            parsed = parse_model(text, model)
        except LLMError as exc:
            call.error = str(exc)
            self.calls.append(call)
            raise
        self.calls.append(call)
        return parsed


def _ollama_models(http: httpx.Client) -> list[str]:
    try:
        response = http.get(f"{config.LLM_OLLAMA_URL}/api/tags")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"ollama is not reachable at {config.LLM_OLLAMA_URL}: {exc}", retryable=True) from exc
    return [str(item.get("name") or "") for item in response.json().get("models") or []]


# ----------------------------------------------------------------------------- Claude batches
def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class ClaudeBatchExtractor:
    """Sonnet 5 via `messages.batches`. The system prefix is cached; the document is the user message.

    Results are read by `custom_id`. `output_config` carries the JSON schema and `effort: low`.
    There is no assistant prefill and no `budget_tokens`.
    """

    backend = "anthropic"

    def __init__(self, client: Any = None):
        self.calls: list[Call] = []
        self.pulled = False
        self._client = client

    def close(self) -> None:
        return None

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        if anthropic is None:
            raise LLMError("the anthropic package is not installed (uv sync --group enrich)", retryable=True)
        self._client = anthropic.Anthropic()
        return self._client

    def extract(self, document: Any, candidate_events: list[dict]) -> Extraction:
        job = ExtractJob(_field(document, "document_id"), document, candidate_events)
        found = self.extract_many([job])
        parsed = found.get(job.document_id)
        if parsed is None:
            raise LLMError("claude batch returned no extraction", retryable=True)
        return parsed

    def summarise(self, event: Any, authority_text: str, docs: list[Any]) -> Summary:
        job = SummaryJob(_field(event, "event_id"), event, authority_text, docs)
        found = self.summarise_many([job])
        parsed = found.get(job.event_id)
        if parsed is None:
            raise LLMError("claude batch returned no summary", retryable=True)
        return parsed

    def extract_many(self, jobs: list[ExtractJob]) -> dict[str, Extraction | None]:
        items = [_extract_item(job) for job in jobs]
        by_id = self._batch(items)
        return _parsed_map(items, by_id, Extraction)

    def summarise_many(self, jobs: list[SummaryJob]) -> dict[str, Summary | None]:
        items = [_summary_item(job) for job in jobs]
        by_id = self._batch(items)
        return _parsed_map(items, by_id, Summary)

    def _batch(self, items: list[Work]) -> dict[str, Any]:
        if not items:
            return {}
        client = self._sdk()
        requests = [{"custom_id": item.custom_id, "params": _claude_params(item)} for item in items]
        batch = client.messages.batches.create(requests=requests)
        batch_id = _attr(batch, "id")
        deadline = time.monotonic() + config.LLM_BATCH_TIMEOUT_S
        while _attr(batch, "processing_status") != "ended":
            if time.monotonic() > deadline:
                raise LLMError(f"claude batch {batch_id} did not end within {config.LLM_BATCH_TIMEOUT_S:.0f}s", retryable=True)
            time.sleep(config.LLM_BATCH_POLL_S)
            batch = client.messages.batches.retrieve(batch_id)
        by_id: dict[str, Any] = {}
        for result in client.messages.batches.results(batch_id):
            by_id[_attr(result, "custom_id")] = result
        for item in items:
            self.calls.append(_call_from_result(item, by_id.get(item.custom_id)))
        return by_id


def _claude_params(item: "Work") -> dict:
    return {
        "model": config.LLM_ANTHROPIC_MODEL,
        "max_tokens": config.LLM_MAX_OUTPUT_TOKENS,
        "system": [
            {
                "type": "text",
                "text": item.prefix,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": item.user}],
        "output_config": {
            "format": {"type": "json_schema", "schema": item.schema},
            "effort": "low",
        },
    }


def _call_from_result(item: "Work", result: Any) -> Call:
    call = Call(
        purpose=item.purpose,
        backend="anthropic",
        model=config.LLM_ANTHROPIC_MODEL,
        input_tokens=0,
        output_tokens=0,
        document_id=item.document_id,
        event_id=item.event_id,
        custom_id=item.custom_id,
    )
    if result is None:
        call.error = "missing result"
        return call
    outcome = _attr(result, "result")
    if _attr(outcome, "type") != "succeeded":
        call.error = str(_attr(outcome, "type") or "failed")
        return call
    message = _attr(outcome, "message")
    usage = _attr(message, "usage")
    call.input_tokens = _usage_int(usage, "input_tokens")
    call.output_tokens = _usage_int(usage, "output_tokens")
    call.cache_read_tokens = _usage_int(usage, "cache_read_input_tokens")
    call.cache_write_tokens = _usage_int(usage, "cache_creation_input_tokens")
    call.cost_usd = cost_usd(
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cache_read_tokens=call.cache_read_tokens,
        cache_write_tokens=call.cache_write_tokens,
    )
    return call


def _message_text(result: Any) -> str:
    outcome = _attr(result, "result")
    message = _attr(outcome, "message")
    parts: list[str] = []
    for block in _attr(message, "content") or []:
        if _attr(block, "type", "text") == "text" and _attr(block, "text"):
            parts.append(_attr(block, "text"))
    return "".join(parts)


def _parsed_map(items: list["Work"], by_id: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        result = by_id.get(item.custom_id)
        key = item.document_id or item.event_id or item.custom_id
        if result is None or _attr(_attr(result, "result"), "type") != "succeeded":
            out[key] = None
            continue
        try:
            out[key] = parse_model(_message_text(result), model)
        except LLMError:
            out[key] = None
    return out


# ----------------------------------------------------------------------------- jobs
@dataclass
class Work:
    custom_id: str
    purpose: str
    prefix: str
    user: str
    schema: dict
    document_id: str | None = None
    event_id: str | None = None


@dataclass
class ExtractJob:
    document_id: str
    document: Any
    candidates: list[dict]


@dataclass
class SummaryJob:
    event_id: str
    event: Any
    authority_text: str
    documents: list[Any]


def _extract_item(job: ExtractJob) -> Work:
    return Work(custom_id("extract", job.document_id), "extract", system_prefix("extract"), user_extract(job.document, job.candidates), EXTRACTION_SCHEMA, document_id=job.document_id)


def _summary_item(job: SummaryJob) -> Work:
    return Work(custom_id("summarise", job.event_id), "summarise", system_prefix("summarise"), user_summary(job.event, job.authority_text, job.documents), SUMMARY_SCHEMA, event_id=job.event_id)


def _many(extractor: OllamaExtractor, jobs: list, purpose: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for job in jobs:
        key = job.document_id if purpose == "extract" else job.event_id
        try:
            if purpose == "extract":
                out[key] = extractor.extract(job.document, job.candidates)
            else:
                out[key] = extractor.summarise(job.event, job.authority_text, job.documents)
        except LLMError as exc:
            if exc.retryable:
                log.warning("llm %s %s left for the next run: %s", purpose, key, exc)
            else:
                log.warning("llm %s %s rejected: %s", purpose, key, exc)
            out[key] = None
    return out


class Extractor(Protocol):
    calls: list[Call]
    pulled: bool

    def extract(self, document: Any, candidate_events: list[dict]) -> Extraction: ...

    def summarise(self, event: Any, authority_text: str, documents: list[Any]) -> Summary: ...

    def extract_many(self, jobs: list[ExtractJob]) -> dict[str, Extraction | None]: ...

    def summarise_many(self, jobs: list[SummaryJob]) -> dict[str, Summary | None]: ...


# ----------------------------------------------------------------------------- local: spans, not generation
_FIGURE_SPAN = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+)\s+(dead|injured|missing|displaced)\b", re.IGNORECASE)
_CAPITAL = re.compile(r"\b[A-Z][a-zA-Z][A-Za-z'’-]{1,}\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_PLACE_SKIP = frozenset({
    "the", "this", "that", "these", "those", "and", "for", "from", "with", "after", "before",
    "gdacs", "eonet", "nasa", "copernicus", "reliefweb",
})


def figure_spans(texts: list[str]) -> dict:
    """A figure is the number already written next to dead, injured, missing or displaced. The match is the span."""
    found: dict[str, dict | None] = {key: None for key in FIGURE_KEYS}
    for text in texts:
        if not text:
            continue
        for match in _FIGURE_SPAN.finditer(text):
            key = match.group(2).lower()
            if found[key] is None:
                found[key] = {"value": int(match.group(1).replace(",", "")), "evidence_span": match.group(0)}
    return found


def place_names(text: str, language: str | None = None) -> list[str]:
    """Names the text states: spaCy when it is installed, plus each capitalised word the lexicon does not claim."""
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str | None) -> None:
        cleaned = extract.clean_name(name) if name else None
        if not cleaned or cleaned.casefold() in seen or cleaned.casefold() in _PLACE_SKIP:
            return
        seen.add(cleaned.casefold())
        found.append(cleaned)

    if text and text.strip():
        try:
            for name in extract.ner_places(text, language):
                add(name)
        except Exception as exc:  # spaCy is optional; the capitalised words still run
            log.debug("ner skipped: %s", exc)
        for match in _CAPITAL.finditer(text):
            add(match.group(0))
    return found


def _hint_for(name: str, candidates: list[dict]) -> str | None:
    folded = name.casefold()
    for event in candidates:
        title = (event.get("title") or "").casefold()
        if folded in title and event.get("country_iso3"):
            return event["country_iso3"]
    return None


def _local_call(purpose: str, *, document_id: str | None = None, event_id: str | None = None, text: str = "") -> Call:
    return Call(
        purpose=purpose,
        backend="local",
        model=config.LLM_LOCAL_MODEL,
        input_tokens=estimate_tokens(text),
        output_tokens=0,
        cost_usd=0,
        document_id=document_id,
        event_id=event_id,
        custom_id=custom_id(purpose, document_id or event_id or purpose),
    )


class LocalExtractor:
    """No generation. Hazard from the lexicon, figures from verbatim spans, places from names in the text.

    Summaries are sentences of the authority text. A sentence that contains a digit is kept only with a
    span copied from that text. cost_usd is 0.
    """

    backend = "local"
    pulled = False

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def close(self) -> None:
        return None

    def extract(self, document: Any, candidate_events: list[dict]) -> Extraction:
        title = _field(document, "title") or ""
        excerpt = _field(document, "text_excerpt") or ""
        text = f"{title}. {excerpt}"
        classification = lexicon.classify(text)
        names: list[str] = []
        seen: set[str] = set()
        for name in place_names(title, _field(document, "language")) + place_names(excerpt, _field(document, "language")):
            if name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        self.calls.append(_local_call("extract", document_id=_field(document, "document_id"), text=text))
        return Extraction(
            hazard_type=classification.hazard_type,
            places=[PlaceName(name=name, country_hint=_hint_for(name, candidate_events)) for name in names],
            figures=figure_spans([title, excerpt]),  # type: ignore[arg-type]
            confidence=classification.confidence,
        )

    def summarise(self, event: Any, authority_text: str, docs: list[Any]) -> Summary:
        texts = _inputs_for_summary(authority_text, docs)
        figures = figure_spans(texts)
        kept: list[SummarySentence] = []
        for chunk in _SENTENCE.split((authority_text or "").strip()):
            sentence = " ".join(chunk.split())
            if not sentence:
                continue
            span = None
            if _DIGIT.search(sentence):
                span = next((item["evidence_span"] for item in figures.values() if isinstance(item, dict) and item["evidence_span"] in sentence), None)
                if span is None:
                    continue
            kept.append(SummarySentence(text=sentence, evidence_span=span))
            if len(kept) >= config.LLM_SUMMARY_MAX_SENTENCES:
                break
        self.calls.append(_local_call("summarise", event_id=_field(event, "event_id"), text=authority_text or ""))
        return Summary(sentences=kept, figures=figures)  # type: ignore[arg-type]

    def extract_many(self, jobs: list[ExtractJob]) -> dict[str, Extraction | None]:
        return {job.document_id: self.extract(job.document, job.candidates) for job in jobs}

    def summarise_many(self, jobs: list[SummaryJob]) -> dict[str, Summary | None]:
        return {job.event_id: self.summarise(job.event, job.authority_text, job.documents) for job in jobs}


def make_extractor(conn: sqlite3.Connection, estimate: float, *, backend: str | None = None, ollama: httpx.Client | None = None, anthropic_client: Any = None, now: datetime | None = None) -> tuple[LocalExtractor | OllamaExtractor | ClaudeBatchExtractor, bool]:
    """The cloud client is constructed only when the estimate fits under the cap. Otherwise the local backend, and a log line.

    A cap of 0 never constructs it: `budget_allows` is false before any request is built.
    """
    name = (backend or config.LLM_BACKEND).strip().lower()
    if name == "anthropic":
        if not budget_allows(conn, max(estimate, 1e-9), now):
            log.warning("budget cap reached")
            return LocalExtractor(), True
        return ClaudeBatchExtractor(anthropic_client), False
    if name == "ollama":
        return OllamaExtractor(ollama), False
    if name == "local":
        return LocalExtractor(), False
    raise LLMError(f"unknown EWW_LLM_BACKEND {name!r} (expected local, ollama or anthropic)", retryable=False)


# ----------------------------------------------------------------------------- what the pipeline asks the model
def ambiguous_documents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Documents the lexicon-and-NER stage left without a hazard type or without a resolved place, not yet sent to a model."""
    rows = conn.execute(
        """
        SELECT d.*, x.hazard_type AS x_hazard, x.hazard_confidence AS x_confidence, x.places AS x_places, x.event_date AS x_date
        FROM document d
        JOIN document_extraction x ON x.document_id = d.document_id
        WHERE d.removed_at IS NULL AND x.method = ?
        ORDER BY d.document_id
        """,
        (config.EXTRACT_METHOD,),
    ).fetchall()
    chosen = []
    for row in rows:
        places = json.loads(row["x_places"] or "[]")
        located = any(place.get("lat") is not None for place in places)
        if row["x_hazard"] is None or not located:
            chosen.append(row)
    return chosen


def candidates_for(conn: sqlite3.Connection, doc: sqlite3.Row, hazard: str | None) -> list[dict]:
    """Up to LLM_CANDIDATES live events: the ones whose query fetched the document, then recent events of its hazard."""
    ids = documents.retrieval_events(conn, doc["document_id"])
    if hazard:
        more = conn.execute(
            """
            SELECT event_id FROM event
            WHERE merged_into_event_id IS NULL AND status IN ('active', 'ended') AND hazard_type = ?
            ORDER BY COALESCE(severity_score, 0) DESC, last_observed_at DESC
            LIMIT ?
            """,
            (hazard, config.LLM_CANDIDATES),
        ).fetchall()
        for row in more:
            if row[0] not in ids:
                ids.append(row[0])
    out = []
    for event_id in ids[: config.LLM_CANDIDATES]:
        event = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        if event is None:
            continue
        out.append({
            "event_id": event["event_id"],
            "hazard_type": event["hazard_type"],
            "title": event["title"],
            "lat": event["centroid_lat"],
            "lon": event["centroid_lon"],
            "country_iso3": event["country_iso3"],
            "started_at": event["started_at"],
        })
    return out


def authority_text(conn: sqlite3.Connection, event_id: str) -> str:
    """GDACS description, EONET description, Copernicus activation name, ReliefWeb narrative: whatever the event's records hold."""
    parts: list[str] = []
    rows = conn.execute(
        "SELECT source_id, title, payload FROM source_record WHERE event_id = ? ORDER BY observed_at, source_record_id",
        (event_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        text = _record_text(row["source_id"], payload, row["title"])
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def _record_text(source_id: str, payload: dict, title: str | None) -> str:
    if source_id == "gdacs":
        props = payload.get("properties") or payload
        severity = props.get("severitydata") or {}
        bits = [props.get("name"), props.get("description"), severity.get("severitytext") if isinstance(severity, dict) else None]
    elif source_id == "eonet":
        bits = [payload.get("title"), payload.get("description")]
    elif source_id == "copernicus":
        bits = [payload.get("name"), payload.get("category"), ", ".join(payload.get("countries") or [])]
    else:
        bits = [payload.get(key) for key in ("name", "title", "description", "body", "overview")]
    bits.append(title)
    seen: list[str] = []
    for bit in bits:
        text = plain(str(bit)) if bit else ""
        if text and text not in seen:
            seen.append(text)
    return " ".join(seen)


def _summary_evidence(event: sqlite3.Row) -> dict:
    if not event["summary_evidence"]:
        return {}
    try:
        return json.loads(event["summary_evidence"])
    except json.JSONDecodeError:
        return {}


def events_to_summarise(conn: sqlite3.Connection, now: datetime | None = None) -> list[sqlite3.Row]:
    """Active events with at least three attached documents that were not part of the last summary, and no summary in the last 24 hours."""
    now = now or now_utc()
    cutoff = to_iso(now - timedelta(hours=config.LLM_SUMMARY_REFRESH_HOURS))
    rows = conn.execute(
        """
        SELECT * FROM event
        WHERE status = 'active' AND merged_into_event_id IS NULL
          AND (summary_updated_at IS NULL OR summary_updated_at <= ?)
        ORDER BY event_id
        """,
        (cutoff,),
    ).fetchall()
    due = []
    for event in rows:
        used = set(_summary_evidence(event).get("document_ids") or [])
        attached = conn.execute(
            "SELECT document_id FROM event_document WHERE event_id = ? AND status = 'attached'",
            (event["event_id"],),
        ).fetchall()
        fresh = [row[0] for row in attached if row[0] not in used]
        if len(fresh) >= config.LLM_SUMMARY_MIN_NEW_DOCS:
            due.append(event)
    return due


def summary_documents(conn: sqlite3.Connection, event_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.*, ed.score FROM document d
        JOIN event_document ed ON ed.document_id = d.document_id
        WHERE ed.event_id = ? AND ed.status = 'attached' AND d.removed_at IS NULL
        ORDER BY ed.score DESC, d.document_id
        LIMIT ?
        """,
        (event_id, config.LLM_SUMMARY_MAX_DOCS),
    ).fetchall()


def _inputs_for_summary(authority: str, docs: list[Any]) -> list[str]:
    texts = [authority]
    for doc in docs:
        texts.append(_field(doc, "title") or "")
        texts.append(_field(doc, "text_excerpt") or "")
    return texts


# ----------------------------------------------------------------------------- the run
@dataclass
class LlmStats:
    documents: int = 0
    summarised: int = 0
    backend: str = ""
    cost_usd: float = 0
    fell_back: bool = False
    pulled: bool = False
    skipped: int = 0


def run(
    conn: sqlite3.Connection,
    *,
    geocoder: geocode.Geocoder | None = None,
    backend: str | None = None,
    ollama: httpx.Client | None = None,
    anthropic_client: Any = None,
    remote: bool = True,
    now: datetime | None = None,
) -> LlmStats:
    """Fill ambiguous documents, then summarise events that gained three new attachments. Idempotent."""
    stats = LlmStats()
    docs = ambiguous_documents(conn)
    due = events_to_summarise(conn, now)
    extract_jobs = []
    for doc in docs:
        hazard = doc["x_hazard"] or lexicon.classify(f"{doc['title'] or ''}. {doc['text_excerpt'] or ''}").hazard_type
        extract_jobs.append(ExtractJob(doc["document_id"], doc, candidates_for(conn, doc, hazard)))
    summary_jobs = []
    summary_docs: dict[str, list] = {}
    authorities: dict[str, str] = {}
    for event in due:
        authorities[event["event_id"]] = authority_text(conn, event["event_id"])
        summary_docs[event["event_id"]] = summary_documents(conn, event["event_id"])
        summary_jobs.append(SummaryJob(event["event_id"], event, authorities[event["event_id"]], summary_docs[event["event_id"]]))
    estimate = estimate_batch_usd(system_prefix("extract"), [user_extract(job.document, job.candidates) for job in extract_jobs])
    estimate += estimate_batch_usd(system_prefix("summarise"), [user_summary(job.event, job.authority_text, job.documents) for job in summary_jobs])
    if not extract_jobs and not summary_jobs:
        stats.backend = backend or config.LLM_BACKEND
        return stats
    extractor, fell_back = make_extractor(conn, estimate, backend=backend, ollama=ollama, anthropic_client=anthropic_client, now=now)
    stats.backend = extractor.backend
    stats.fell_back = fell_back
    own_geocoder = geocoder is None
    geocoder = geocoder or geocode.Geocoder(conn, tiers=config.GEOCODE_TIERS if remote else ["gazetteer"])
    try:
        extracted = extractor.extract_many(extract_jobs) if extract_jobs else {}
        summarised = extractor.summarise_many(summary_jobs) if summary_jobs else {}
        stats.pulled = extractor.pulled
        with conn:
            for job in extract_jobs:
                call = _call_for(extractor.calls, document_id=job.document_id)
                if _write_document(conn, job, extracted.get(job.document_id), geocoder, remote, call):
                    stats.documents += 1
                else:
                    stats.skipped += 1
            for job in summary_jobs:
                call = _call_for(extractor.calls, event_id=job.event_id)
                if _write_summary(conn, job, summarised.get(job.event_id), summary_docs[job.event_id], authorities[job.event_id], call, now):
                    stats.summarised += 1
            for call in extractor.calls:
                write_call(conn, call, when=to_iso(now) if now else None)
                stats.cost_usd += call.cost_usd
    finally:
        extractor.close()
        if own_geocoder:
            geocoder.close()
    log.info("llm backend=%s documents=%d summarised=%d cost_usd=%.6f fell_back=%s", stats.backend, stats.documents, stats.summarised, stats.cost_usd, stats.fell_back)
    return stats


def _call_for(calls: list[Call], *, document_id: str | None = None, event_id: str | None = None) -> Call | None:
    for call in calls:
        if document_id is not None and call.document_id == document_id and call.purpose == "extract":
            return call
        if event_id is not None and call.event_id == event_id and call.purpose == "summarise":
            return call
    return None


def _write_document(conn: sqlite3.Connection, job: ExtractJob, raw: Extraction | None, geocoder: Any, remote: bool, call: Call | None) -> bool:
    """Write the validated fields. A transport failure leaves no call, so the lexicon row stays and the next run retries."""
    if call is None:
        return False
    doc = job.document
    classification = lexicon.classify(f"{doc['title'] or ''}. {doc['text_excerpt'] or ''}")
    lexicon_hazard = doc["x_hazard"] or classification.hazard_type
    lexicon_confidence = doc["x_confidence"] if doc["x_hazard"] else classification.confidence
    accepted = accept_extraction(
        raw,
        title=doc["title"],
        excerpt=doc["text_excerpt"],
        lexicon_hazard=lexicon_hazard,
        lexicon_confidence=lexicon_confidence,
        candidates=job.candidates,
        geocoder=geocoder,
        fallback_date=doc["x_date"] or doc["published_at"],
        remote=remote,
    )
    conn.execute(
        """
        INSERT INTO document_extraction (document_id, method, model_version, hazard_type, hazard_confidence, places, event_date, figures, extracted_at, cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            method = excluded.method, model_version = excluded.model_version,
            hazard_type = excluded.hazard_type, hazard_confidence = excluded.hazard_confidence,
            places = excluded.places, event_date = excluded.event_date, figures = excluded.figures,
            extracted_at = excluded.extracted_at, cost_usd = excluded.cost_usd
        """,
        (
            doc["document_id"],
            f"llm:{call.model}",
            call.model,
            accepted.hazard_type,
            accepted.hazard_confidence,
            json.dumps(accepted.places, ensure_ascii=False),
            accepted.event_date,
            json.dumps(accepted.figures, ensure_ascii=False),
            now_iso(),
            call.cost_usd,
        ),
    )
    return True


def _write_summary(conn: sqlite3.Connection, job: SummaryJob, raw: Summary | None, docs: list, authority: str, call: Call | None, now: datetime | None) -> bool:
    if call is None:
        return False
    accepted = accept_summary(raw, _inputs_for_summary(authority, docs))
    when = to_iso(now) if now else now_iso()
    document_ids = [_field(doc, "document_id") for doc in docs if _field(doc, "document_id")]
    previous = job.event["summary"] if _field(job.event, "summary") else None
    text = summary_text(accepted)
    if text:
        evidence = {
            "sentences": [sentence.model_dump() for sentence in accepted.sentences],
            "figures": accepted.figures.model_dump(),
            "document_ids": document_ids,
        }
        prose = text
    elif previous:
        evidence = _summary_evidence(job.event)
        evidence["document_ids"] = document_ids
        prose = previous
    else:
        evidence = {"sentences": [], "figures": accepted.figures.model_dump(), "document_ids": document_ids}
        prose = None
    conn.execute(
        """
        UPDATE event SET summary = ?, summary_updated_at = ?, summary_method = ?, summary_evidence = ?, updated_at = ?
        WHERE event_id = ?
        """,
        (prose, when, f"llm:{call.model}", json.dumps(evidence, ensure_ascii=False), when, job.event_id),
    )
    return True


# ----------------------------------------------------------------------------- doctor
def awaiting_extraction(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Documents with no extraction row whose published (else fetched) time is older than 24 hours."""
    cutoff = to_iso((now or now_utc()) - timedelta(hours=config.LLM_AWAITING_HOURS))
    return conn.execute(
        """
        SELECT COUNT(*) FROM document d
        WHERE d.removed_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM document_extraction x WHERE x.document_id = d.document_id)
          AND COALESCE(d.published_at, d.fetched_at) <= ?
        """,
        (cutoff,),
    ).fetchone()[0]


def evidence_violations(conn: sqlite3.Connection) -> list[dict]:
    """Figures and numbered summary sentences whose evidence span is not a verbatim substring of a source text."""
    found: list[dict] = []
    for row in conn.execute(
        """
        SELECT d.document_id, d.title, d.text_excerpt, x.figures
        FROM document_extraction x JOIN document d ON d.document_id = x.document_id
        WHERE x.figures IS NOT NULL
        """
    ):
        try:
            figures = json.loads(row["figures"] or "{}")
        except json.JSONDecodeError:
            found.append({"kind": "document", "id": row["document_id"], "field": "figures"})
            continue
        texts = [row["title"] or "", row["text_excerpt"] or ""]
        for key, figure in figures.items():
            if isinstance(figure, dict) and not span_in(figure.get("evidence_span"), texts):
                found.append({"kind": "document", "id": row["document_id"], "field": key})
    for event in conn.execute("SELECT event_id, title, summary, summary_evidence FROM event WHERE summary IS NOT NULL OR summary_evidence IS NOT NULL"):
        try:
            evidence = json.loads(event["summary_evidence"] or "{}")
        except json.JSONDecodeError:
            found.append({"kind": "summary", "id": event["event_id"], "field": "summary_evidence"})
            continue
        sentences = evidence.get("sentences") or []
        if event["summary"] and _DIGIT.search(event["summary"]) and not any(_DIGIT.search(s.get("text") or "") for s in sentences):
            found.append({"kind": "summary", "id": event["event_id"], "field": "summary"})
        texts = _summary_source_texts(conn, event["event_id"], evidence.get("document_ids") or [])
        for index, sentence in enumerate(sentences):
            if _DIGIT.search(sentence.get("text") or "") and not span_in(sentence.get("evidence_span"), texts):
                found.append({"kind": "summary", "id": event["event_id"], "field": f"sentence {index + 1}"})
        for key, figure in (evidence.get("figures") or {}).items():
            if isinstance(figure, dict) and not span_in(figure.get("evidence_span"), texts):
                found.append({"kind": "summary", "id": event["event_id"], "field": key})
    return found


def _summary_source_texts(conn: sqlite3.Connection, event_id: str, document_ids: list[str]) -> list[str]:
    texts = [authority_text(conn, event_id)]
    if document_ids:
        marks = ", ".join("?" * len(document_ids))
        for row in conn.execute(f"SELECT title, text_excerpt FROM document WHERE document_id IN ({marks})", document_ids):
            texts.append(row["title"] or "")
            texts.append(row["text_excerpt"] or "")
    return texts


def doctor_lines(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    spend = month_spend(conn, now)
    awaiting = awaiting_extraction(conn, now)
    violations = evidence_violations(conn)
    anthropic = conn.execute(
        "SELECT COUNT(*) FROM llm_call WHERE backend = 'anthropic' AND called_at >= date('now', 'start of month')"
    ).fetchone()[0]
    return [
        f"llm spend this UTC month: ${spend:.4f} of ${config.LLM_BUDGET_USD:g} cap (backend {config.LLM_BACKEND}; anthropic rows this month: {anthropic})",
        f"documents awaiting extraction older than {config.LLM_AWAITING_HOURS} h: {awaiting}",
        f"evidence-span violations: {len(violations)}" + ("" if not violations else " " + "; ".join(f"{v['kind']} {v['id']} {v['field']}" for v in violations[:10])),
    ]


# ----------------------------------------------------------------------------- golden set
def _num_eq(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return False


def _expected_figures(raw: dict | None) -> dict:
    raw = raw or {}
    return {key: raw.get(key) for key in FIGURE_KEYS}


def _got_figures(figures: dict) -> dict:
    out = {}
    for key in FIGURE_KEYS:
        item = figures.get(key)
        out[key] = None if not isinstance(item, dict) else item.get("value")
    return out


def load_jsonl(path) -> list[dict]:
    rows = []
    for line in _read_lines(path):
        rows.append(json.loads(line))
    return rows


def _read_lines(path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _seed_gazetteer(conn: sqlite3.Connection, places: list[dict]) -> None:
    for index, place in enumerate(places, start=1):
        if place.get("lat") is None or not place.get("name"):
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO gazetteer_place
                (geonameid, name, asciiname, alternatenames, lat, lon, feature_class, feature_code, country_iso2, admin1_code, population)
            VALUES (?, ?, ?, '', ?, ?, 'P', 'PPL', ?, NULL, 100000)
            """,
            (9_000_000_000 + index, place["name"], place["name"], float(place["lat"]), float(place["lon"]), place.get("iso2") or "IT"),
        )


def _place_hit(name: str, places: list[dict]) -> bool:
    wanted = lexicon.fold(name)
    return any(lexicon.fold(place.get("name")) == wanted for place in places)


def _count_bad_spans(figures: dict, texts: list[str]) -> int:
    bad = 0
    for key in FIGURE_KEYS:
        item = figures.get(key)
        if isinstance(item, dict) and not span_in(item.get("evidence_span"), texts):
            bad += 1
    return bad


def evaluate(conn: sqlite3.Connection, extractor: Extractor, *, documents_path=None, events_path=None) -> dict:
    """Run the golden set through `extractor` and score it. Calls are ledgered with no document or event id."""
    documents_path = documents_path or config.GOLDEN_DOCUMENTS
    events_path = events_path or config.GOLDEN_EVENTS
    document_rows = load_jsonl(documents_path)
    event_rows = load_jsonl(events_path)
    geo_conn = db.connect(":memory:")
    db.init_db(geo_conn)
    seeded = [place for row in document_rows for place in (row.get("places") or [])]
    _seed_gazetteer(geo_conn, seeded)
    geocoder = geocode.Geocoder(geo_conn, tiers=["gazetteer"])
    error = None
    extracted: dict = {}
    summarised: dict = {}
    try:
        extract_jobs = [ExtractJob(row["document_id"], row, row.get("candidates") or []) for row in document_rows]
        summary_jobs = [SummaryJob(row["event_id"], row, row.get("authority_text") or "", row.get("documents") or []) for row in event_rows]
        try:
            extracted = extractor.extract_many(extract_jobs) if extract_jobs else {}
            summarised = extractor.summarise_many(summary_jobs) if summary_jobs else {}
        except LLMError as exc:
            error = str(exc)
        scored = _score(document_rows, event_rows, extracted, summarised, geocoder)
    finally:
        geocoder.close()
        geo_conn.close()
    with conn:
        for call in extractor.calls:
            call.document_id = None
            call.event_id = None
            write_call(conn, call)
    scored["cost_usd"] = sum(call.cost_usd for call in extractor.calls)
    scored["error"] = error
    scored["backend"] = getattr(extractor, "backend", "")
    scored["calls"] = len(extractor.calls)
    return scored


def _score(document_rows: list[dict], event_rows: list[dict], extracted: dict, summarised: dict, geocoder: Any) -> dict:
    hazard_correct = places_resolved = places_expected = figure_correct = span_violations = 0
    for row in document_rows:
        classification = lexicon.classify(f"{row.get('title') or ''}. {row.get('text_excerpt') or ''}")
        accepted = accept_extraction(
            extracted.get(row["document_id"]),
            title=row.get("title"),
            excerpt=row.get("text_excerpt"),
            lexicon_hazard=classification.hazard_type,
            lexicon_confidence=classification.confidence,
            candidates=row.get("candidates") or [],
            geocoder=geocoder,
            remote=False,
        )
        expected = row.get("expected") or {}
        if accepted.hazard_type == expected.get("hazard_type"):
            hazard_correct += 1
        texts = [row.get("title") or "", row.get("text_excerpt") or ""]
        span_violations += _count_bad_spans(accepted.figures, texts)
        if all(_num_eq(_got_figures(accepted.figures)[key], _expected_figures(expected.get("figures")).get(key)) for key in FIGURE_KEYS):
            figure_correct += 1
        for place in row.get("places") or []:
            if not place.get("name"):
                continue
            places_expected += 1
            if _place_hit(place["name"], accepted.places):
                places_resolved += 1
    for row in event_rows:
        docs = row.get("documents") or []
        authority = row.get("authority_text") or ""
        accepted = accept_summary(summarised.get(row["event_id"]), _inputs_for_summary(authority, docs))
        texts = _inputs_for_summary(authority, docs)
        span_violations += _count_bad_spans(accepted.figures.model_dump(), texts)
        for index, sentence in enumerate(accepted.sentences):
            if _DIGIT.search(sentence.text) and not span_in(sentence.evidence_span, texts):
                span_violations += 1
        expected = _expected_figures((row.get("expected") or {}).get("figures"))
        got = _got_figures(accepted.figures.model_dump())
        if all(_num_eq(got[key], expected.get(key)) for key in FIGURE_KEYS):
            figure_correct += 1
    documents_n = len(document_rows)
    figure_total = documents_n + len(event_rows)
    hazard_accuracy = hazard_correct / documents_n if documents_n else 0.0
    place_resolution = places_resolved / places_expected if places_expected else 0.0
    figures_exact = figure_correct / figure_total if figure_total else 0.0
    criteria = [
        {"name": "hazard accuracy >= 90%", "value": hazard_accuracy, "target": config.EVAL_HAZARD_ACCURACY, "passed": hazard_accuracy >= config.EVAL_HAZARD_ACCURACY},
        {"name": "place resolution >= 80%", "value": place_resolution, "target": config.EVAL_PLACE_RESOLUTION, "passed": place_resolution >= config.EVAL_PLACE_RESOLUTION},
        {"name": "figures exact-match >= 80%", "value": figures_exact, "target": config.EVAL_FIGURES_EXACT, "passed": figures_exact >= config.EVAL_FIGURES_EXACT},
        {"name": "every kept figure span is verbatim", "value": span_violations, "target": 0, "passed": span_violations == 0},
    ]
    return {
        "documents": documents_n,
        "events": len(event_rows),
        "hazard_correct": hazard_correct,
        "hazard_accuracy": hazard_accuracy,
        "places_resolved": places_resolved,
        "places_expected": places_expected,
        "place_resolution": place_resolution,
        "figure_correct": figure_correct,
        "figure_total": figure_total,
        "figures_exact": figures_exact,
        "span_violations": span_violations,
        "criteria": criteria,
        "passed": all(item["passed"] for item in criteria),
    }


def render_eval(result: dict, live: dict) -> str:
    """The M4 record, in the same measured shape as docs/m2.md."""
    lines = [
        "# M4: summaries and numbers, under a hard budget",
        "",
        f"Generated {live['generated_at']} by `eww eval extraction --backend {result.get('backend') or live['backend']}` against `{live['database']}`.",
        "",
        "## Golden set",
        "",
        f"{result['documents']} documents (`tests/golden/documents.jsonl`) and {result['events']} events (`tests/golden/events.jsonl`). "
        "A figure is kept only when its evidence span is a verbatim substring of the title, the excerpt or the authority text; a place is kept only when it geocodes within twice the candidate event's blocking radius. "
        "Where the lexicon already named a hazard, that type is the one scored.",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Hazard accuracy | {result['hazard_correct']} / {result['documents']} ({100 * result['hazard_accuracy']:.1f}%) |",
        f"| Place resolution | {result['places_resolved']} / {result['places_expected']} ({100 * result['place_resolution']:.1f}%) |",
        f"| Figures exact-match | {result['figure_correct']} / {result['figure_total']} ({100 * result['figures_exact']:.1f}%) |",
        f"| Evidence-span violations in the kept output | {result['span_violations']} |",
        f"| Cost of the run | ${result.get('cost_usd', 0):.4f} |",
        f"| Model calls | {result.get('calls', 0)} |",
        "",
    ]
    if result.get("error"):
        lines.append(f"The run stopped with an error: {result['error']}")
        lines.append("")
    lines += [
        "## Exit criteria",
        "",
        "| Criterion | Result |",
        "|---|---|",
    ]
    for item in result["criteria"]:
        shown = item["value"] if isinstance(item["value"], int) else f"{100 * item['value']:.1f}%"
        lines.append(f"| {item['name']} | {'PASS' if item['passed'] else 'FAIL'} ({shown}) |")
    lines += [
        "",
        "## This database",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Month-to-date `llm_call.cost_usd` | ${live['spend']:.4f} (cap ${live['cap']:g}) |",
        f"| Rows with `backend = 'anthropic'` this month | {live['anthropic']} |",
        f"| Documents awaiting extraction older than 24 h | {live['awaiting']} |",
        f"| Evidence-span violations | {live['violations']} |",
        "",
        "```",
        f"month spend ${live['spend']:.4f} <= {live['cap']:g}: {'PASS' if live['spend'] <= live['cap'] else 'FAIL'}",
        f"evidence-span violations {live['violations']}: {'PASS' if live['violations'] == 0 else 'FAIL'}",
        f"documents awaiting extraction > 24 h: {live['awaiting']}",
        "```",
        "",
        "## How to re-measure",
        "",
        "1. Edit the labels in `tests/golden/documents.jsonl` and `tests/golden/events.jsonl`.",
        "2. Re-run `uv run eww eval extraction` (the default backend is `local`; `ollama` and `anthropic` are optional, and Anthropic still stops at the cap).",
        "",
    ]
    return "\n".join(lines)


def live_numbers(conn: sqlite3.Connection, database: str, backend: str) -> dict:
    return {
        "generated_at": now_iso(),
        "database": database,
        "backend": backend,
        "spend": month_spend(conn),
        "cap": config.LLM_BUDGET_USD,
        "anthropic": conn.execute(
            "SELECT COUNT(*) FROM llm_call WHERE backend = 'anthropic' AND called_at >= date('now', 'start of month')"
        ).fetchone()[0],
        "awaiting": awaiting_extraction(conn),
        "violations": len(evidence_violations(conn)),
    }


def write_eval_report(conn: sqlite3.Connection, result: dict, database: str, path=None) -> Any:
    """Write docs/m4.md. Returns the path."""
    target = path or config.milestone_doc(4)
    text = render_eval(result, live_numbers(conn, database, result.get("backend") or config.LLM_BACKEND))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
