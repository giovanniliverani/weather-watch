"""The hazard lexicon (eww/data/lexicon.yaml): loading, compiling and matching (M3).

    classify(text, prefer=None) -> Classification(hazard_type, confidence, hits)

Every strong, weak and negative pattern is compiled once into a word-boundary regular expression over
folded text (lower case, diacritics removed, whitespace collapsed). A negative pattern vetoes every
positive hit whose span it overlaps. The hazard with the highest score wins; `prefer` (the hazard of the
event whose query fetched the document) breaks ties. Country names and demonyms give the geocoder its
country hint (`country_hints`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from eww import config, countries

STRENGTH_POINTS = {"strong": 2, "weak": 1}


def fold(text: str | None) -> str:
    """Lower case, diacritics removed, curly quotes straightened, whitespace collapsed."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _pattern(term: str) -> re.Pattern:
    words = fold(term).split(" ")
    body = r"\s+".join(re.escape(w) for w in words if w)
    return re.compile(rf"(?<![\w'])({body})(?![\w])")


@dataclass
class Classification:
    hazard_type: str | None
    confidence: float
    hits: dict[str, list[str]] = field(default_factory=dict)  # hazard -> matched terms
    vetoed: list[str] = field(default_factory=list)


@dataclass
class Lexicon:
    path: str
    positives: dict[str, list[tuple[re.Pattern, str, str]]]  # hazard -> [(pattern, term, strength)]
    negatives: dict[str, list[re.Pattern]]
    queries: dict[str, list[str]]
    country_names_it: dict[str, list[str]]
    country_patterns: list[tuple[re.Pattern, str]]  # (pattern, iso3), longest names first

    def query_terms(self, hazard_type: str) -> list[str]:
        return list(self.queries.get(hazard_type) or [])

    def hazards(self) -> list[str]:
        return [h for h in config.HAZARD_TYPES if h in self.positives]

    # ------------------------------------------------------------------ classification
    def classify(self, text: str | None, prefer: str | None = None) -> Classification:
        folded = fold(text)
        if not folded:
            return Classification(None, 0.0)
        scores: dict[str, int] = {}
        hits: dict[str, list[str]] = {}
        strong_terms: dict[str, set[str]] = {}
        vetoed: list[str] = []
        for hazard, patterns in self.positives.items():
            negative_spans = [m.span() for pattern in self.negatives.get(hazard, []) for m in pattern.finditer(folded)]
            strong_spans: list[tuple[int, int]] = []
            for pattern, term, strength in sorted(patterns, key=lambda item: item[2] != "strong"):  # strong terms first
                for match in pattern.finditer(folded):
                    if any(_overlaps(match.span(), span) for span in negative_spans):
                        vetoed.append(term)
                        continue
                    if strength == "weak" and any(_overlaps(match.span(), span) for span in strong_spans):
                        continue  # "storm" inside "storm damage" is one hit, not two
                    scores[hazard] = scores.get(hazard, 0) + STRENGTH_POINTS[strength]
                    hits.setdefault(hazard, []).append(term)
                    if strength == "strong":
                        strong_terms.setdefault(hazard, set()).add(term)
                        strong_spans.append(match.span())
        if not scores:
            return Classification(None, 0.0, {}, vetoed)
        order = {h: i for i, h in enumerate(config.HAZARD_TYPES)}
        ranked = sorted(scores, key=lambda h: (-scores[h], 0 if h == prefer else 1, order.get(h, 99)))
        best = ranked[0]
        levels = config.LEXICON_CONFIDENCE
        if strong_terms.get(best):
            confidence = levels["strong_repeated"] if len(strong_terms[best]) >= 2 else levels["strong"]
        else:
            confidence = levels["weak"]
        if len(ranked) > 1 and strong_terms.get(ranked[1]) and _class(ranked[1]) != _class(best):
            confidence = max(0.3, confidence - levels["ambiguous_penalty"])
        return Classification(best, round(confidence, 2), {h: sorted(set(v)) for h, v in hits.items()}, vetoed)

    # ------------------------------------------------------------------ countries
    def country_hints(self, text: str | None) -> list[str]:
        """ISO3 codes of the countries the text names (English names, Italian names, demonyms), first mention first."""
        folded = fold(text)
        if not folded:
            return []
        found: list[tuple[int, str]] = []
        for pattern, iso3 in self.country_patterns:
            match = pattern.search(folded)
            if match:
                found.append((match.start(), iso3))
        return list(dict.fromkeys(iso3 for _, iso3 in sorted(found)))


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _class(hazard: str) -> str | None:
    return config.HAZARD_CLASS.get(hazard)


# ----------------------------------------------------------------------------- loading
def _load(path: Path) -> Lexicon:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    hazards_raw = raw.get("hazards") or {}
    positives: dict[str, list[tuple[re.Pattern, str, str]]] = {}
    negatives: dict[str, list[re.Pattern]] = {}
    queries: dict[str, list[str]] = {}
    for hazard, spec in hazards_raw.items():
        if hazard not in config.HAZARD_TYPES:
            raise ValueError(f"{path}: unknown hazard {hazard!r} in the lexicon")
        entries: list[tuple[re.Pattern, str, str]] = []
        for strength in ("strong", "weak"):
            for language, terms in (spec.get(strength) or {}).items():
                for term in terms or []:
                    entries.append((_pattern(str(term)), str(term), strength))
        positives[hazard] = entries
        negatives[hazard] = [_pattern(str(term)) for term in spec.get("negative") or []]
        queries[hazard] = [str(t) for t in spec.get("query") or []]
    names_it: dict[str, list[str]] = {}
    country_patterns: list[tuple[re.Pattern, str]] = []
    seen: set[tuple[str, str]] = set()
    for iso3, spec in (raw.get("countries") or {}).items():
        iso3 = str(iso3).upper()
        names_it[iso3] = [str(n) for n in (spec or {}).get("it") or []]
        for name in [*names_it[iso3], *((spec or {}).get("demonyms") or [])]:
            key = (fold(name), iso3)
            if key not in seen and len(key[0]) >= 3:
                seen.add(key)
                country_patterns.append((_pattern(str(name)), iso3))
    for iso3, name in countries.names_by_iso3().items():
        key = (fold(name), iso3)
        if key not in seen and len(key[0]) >= 3:
            seen.add(key)
            country_patterns.append((_pattern(name), iso3))
    for alias, iso3 in config.COUNTRY_NAME_ALIASES.items():
        key = (fold(alias), iso3)
        if key not in seen and len(key[0]) >= 3:
            seen.add(key)
            country_patterns.append((_pattern(alias), iso3))
    country_patterns.sort(key=lambda item: -len(item[0].pattern))
    return Lexicon(str(path), positives, negatives, queries, names_it, country_patterns)


@lru_cache(maxsize=4)
def _cached(path_text: str) -> Lexicon:
    return _load(Path(path_text))


def load(path: Path | None = None) -> Lexicon:
    return _cached(str(path or config.LEXICON_FILE))


def classify(text: str | None, prefer: str | None = None) -> Classification:
    return load().classify(text, prefer)


def country_hints(text: str | None) -> list[str]:
    return load().country_hints(text)
