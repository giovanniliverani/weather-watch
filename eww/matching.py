"""Cross-source blocking and scoring (docs/architecture.md §3, step 1).

An `Entity` is what gets compared: one unresolved record, or one live event summarised from its
records. `candidate_events()` blocks on hazard class, |Δstart| <= T and distance <= R (config.BLOCKING,
keyed by the incoming entity's hazard type; a named storm uses config.STORM_NAME_BLOCK_KM because
"names decide" for cyclones) and only considers events that have no record from the entity's own
sources: identity within one feed is that feed's business. Distance is the smallest one between any
two positions of the entities, so a storm track is compared as a track. `score()` is

    max( 1.0  if the GLIDE numbers are equal,
         0.95 if both are named storms with the same normalised name (inside T),
         0.5 * (1 - d/R) + 0.3 * (1 - Δt/T) + 0.2 * title_similarity )

with the weights and constants in config. `key_conflict()` spots pairs the sources themselves keep
apart (EONET saying "I mirror GDACS 1031315" next to GDACS 1031202); resolve never scores those.
`title_similarity()` is token Jaccard by default; config.TITLE_SIMILARITY = "embedding" (M3) swaps in the
cosine similarity of the local embedding model (eww.embed).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from eww import collectors, config, events, geo
from eww.clock import parse_iso, to_iso

_TOKEN_RE = re.compile(r"[^0-9a-z]+")
_LETTERS_RE = re.compile(r"[^a-z]+")


@dataclass(frozen=True)
class Entity:
    hazard_type: str
    started_at: str | None
    lat: float | None
    lon: float | None
    title: str
    storm_name: str | None = None
    glide_number: str | None = None
    source_ids: frozenset[str] = frozenset()
    event_id: str | None = None
    positions: tuple[tuple[float, float], ...] = ()  # every located record as (lat, lon); the centroid when empty
    external_ids: frozenset[tuple[str, str]] = frozenset()  # (source_id, external_id) of the records behind the entity
    linked_ids: frozenset[tuple[str, str]] = frozenset()  # deterministic keys those records carry

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None

    @property
    def all_positions(self) -> tuple[tuple[float, float], ...]:
        if self.positions:
            return self.positions
        return ((float(self.lat), float(self.lon)),) if self.located else ()


@dataclass(frozen=True)
class Score:
    score: float
    rule: str  # 'glide' | 'storm_name' | 'weighted' | 'none'
    distance_km: float | None
    days_apart: float | None
    spatial: float
    temporal: float
    text_sim: float
    name_match: bool
    glide_match: bool
    radius_km: float | None
    window_days: float | None
    names_differ: bool = False  # two named storms with different names: never merged automatically
    key_match: bool = False  # one entity cites the other's id (Copernicus gdacsId, the GDACS URL EONET carries)


# ----------------------------------------------------------------------------- hazards
def hazard_class(hazard_type: str | None) -> str | None:
    return config.HAZARD_CLASS.get(hazard_type or "")


def class_members(hazard_type: str) -> list[str]:
    klass = hazard_class(hazard_type)
    return [h for h, c in config.HAZARD_CLASS.items() if c == klass] if klass else []


def blocking_for(hazard_type: str | None) -> tuple[float, float] | None:
    """(radius km, window days) for a hazard type; None when it never blocks ('other')."""
    return config.BLOCKING.get(hazard_type or "")


def blocking_radius(entity: Entity) -> float | None:
    """The radius an entity blocks with: basin-scale for a named storm, the hazard's R otherwise."""
    block = blocking_for(entity.hazard_type)
    if block is None:
        return None
    if entity.storm_name and hazard_class(entity.hazard_type) == "storm":
        return config.STORM_NAME_BLOCK_KM
    return block[0]


# ----------------------------------------------------------------------------- names and titles
def normalise_storm_name(raw: str | None) -> str | None:
    """'Tropical Cyclone NORBERT-26' -> 'norbert', 'Hurricane Norbert' -> 'norbert', 'Bang-Lang' -> 'banglang'."""
    if not raw:
        return None
    text = config.STORM_NAME_YEAR_SUFFIX_RE.sub("", str(raw).strip())
    words = [w for w in _LETTERS_RE.split(text.lower()) if w and w not in config.STORM_WORDS]
    name = "".join(words)
    return name if len(name) >= 3 else None


def record_storm_name(source_id: str, payload: dict) -> str | None:
    collector = collectors.get(source_id)
    raw = getattr(collector, "storm_name", lambda p: None)(payload)
    return normalise_storm_name(raw)


def tokens(title: str | None) -> set[str]:
    """Lower-cased word tokens without numbers (EONET titles end in a GDACS id) and stopwords."""
    if not title:
        return set()
    return {
        t for t in _TOKEN_RE.split(str(title).lower())
        if t and not t.isdigit() and len(t) > 1 and t not in config.TITLE_STOPWORDS
    }


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(a: str | None, b: str | None) -> float:
    """Similarity of two titles in [0, 1]. Jaccard now; M3 adds config.TITLE_SIMILARITY = 'embedding'."""
    method = config.TITLE_SIMILARITY
    if method == "jaccard":
        return jaccard(tokens(a), tokens(b))
    if method == "embedding":  # M3: cosine of the local model's vectors (EWW_TITLE_SIMILARITY=embedding)
        from eww import embed

        return embed.title_similarity(a, b)
    raise NotImplementedError(f"title similarity {method!r} is not available; use 'jaccard' or 'embedding'")


# ----------------------------------------------------------------------------- entities
def entity_from_record(rec) -> Entity:
    payload = events.payload_of(rec)
    storm = record_storm_name(rec["source_id"], payload) if hazard_class(rec["hazard_type"]) == "storm" else None
    located = rec["lat"] is not None and rec["lon"] is not None
    return Entity(
        hazard_type=rec["hazard_type"],
        started_at=rec["started_at"] or rec["observed_at"],
        lat=rec["lat"],
        lon=rec["lon"],
        title=rec["title"] or "",
        storm_name=storm,
        glide_number=rec["glide_number"],
        source_ids=frozenset({rec["source_id"]}),
        positions=((float(rec["lat"]), float(rec["lon"])),) if located else (),
        external_ids=frozenset({(rec["source_id"], str(rec["external_id"]))}),
        linked_ids=frozenset((s, str(e)) for s, e in collectors.get(rec["source_id"]).linked_ids(payload)),
    )


def entity_from_records(recs, *, hazard_type=None, started_at=None, lat=None, lon=None, title=None, glide_number=None, event_id=None) -> Entity:
    """An entity over several records (an event, or one feed item's records); explicit values win over derived ones."""
    latest = recs[-1]
    hazard = hazard_type or latest["hazard_type"]
    storm = None
    if hazard_class(hazard) == "storm":
        for rec in reversed(recs):
            storm = record_storm_name(rec["source_id"], events.payload_of(rec))
            if storm:
                break
    located = [r for r in recs if r["lat"] is not None and r["lon"] is not None]
    positions = tuple(dict.fromkeys((float(r["lat"]), float(r["lon"])) for r in located))
    linked: set[tuple[str, str]] = set()
    for rec in recs:
        linked.update((s, str(e)) for s, e in collectors.get(rec["source_id"]).linked_ids(events.payload_of(rec)))
    if lat is None and located:
        lat, lon = located[-1]["lat"], located[-1]["lon"]
    return Entity(
        hazard_type=hazard,
        started_at=started_at or min(r["started_at"] or r["observed_at"] for r in recs),
        lat=lat,
        lon=lon,
        title=(title if title is not None else latest["title"]) or "",
        storm_name=storm,
        glide_number=glide_number or next((r["glide_number"] for r in reversed(recs) if r["glide_number"]), None),
        source_ids=frozenset(r["source_id"] for r in recs),
        event_id=event_id,
        positions=positions,
        external_ids=frozenset((r["source_id"], str(r["external_id"])) for r in recs),
        linked_ids=frozenset(linked),
    )


def entity_from_event(conn: sqlite3.Connection, event, recs=None) -> Entity:
    recs = events.event_records(conn, event["event_id"]) if recs is None else recs
    return entity_from_records(
        recs,
        hazard_type=event["hazard_type"],
        started_at=event["started_at"],
        lat=event["centroid_lat"],
        lon=event["centroid_lon"],
        title=event["title"],
        glide_number=event["glide_number"],
        event_id=event["event_id"],
    )


# ----------------------------------------------------------------------------- distances and conflicts
def min_distance_km(a: Entity, b: Entity) -> float | None:
    """The smallest great-circle distance between any position of `a` and any position of `b`."""
    if not a.all_positions or not b.all_positions:
        return None
    return min(geo.haversine_km(la, lo, lb, lob) for la, lo in a.all_positions for lb, lob in b.all_positions)


def shared_keys(a: Entity, b: Entity) -> list[tuple[str, str]]:
    """The (source_id, external_id) keys one entity cites that name a record of the other."""
    keys = [k for k in sorted(a.linked_ids) if k in b.external_ids] + [k for k in sorted(b.linked_ids) if k in a.external_ids]
    return list(dict.fromkeys(keys))


def aggregation_radius(entity: Entity) -> float:
    """How close another feed's position must be for its record to share this entity's pin automatically."""
    return config.aggregation_radius_km(entity.hazard_type)


def within_aggregation_radius(a: Entity, b: Entity) -> tuple[bool, float | None, float]:
    """(inside, distance_km, radius_km). Without coordinates on one side there is nothing to contradict: inside."""
    radius = aggregation_radius(a)
    distance = min_distance_km(a, b)
    return (distance is None or distance <= radius), (None if distance is None else round(distance, 1)), radius


def key_conflict(a: Entity, b: Entity) -> bool:
    """True when one entity's deterministic key names a *different* record of a source the other entity holds."""
    for source_id, external_id in a.linked_ids:
        if source_id in b.source_ids and (source_id, external_id) not in b.external_ids:
            return True
    for source_id, external_id in b.linked_ids:
        if source_id in a.source_ids and (source_id, external_id) not in a.external_ids:
            return True
    return False


# ----------------------------------------------------------------------------- blocking
def candidate_events(conn: sqlite3.Connection, entity: Entity, *, exclude_event_ids=()) -> list[tuple[sqlite3.Row, Entity]]:
    """Live events in the entity's block, oldest first, each with its Entity: same hazard class,
    |Δstart| <= T, distance <= blocking_radius(entity) to the event's centroid or any of its records,
    and no record from any of the entity's own sources."""
    block = blocking_for(entity.hazard_type)
    radius_km = blocking_radius(entity)
    if block is None or radius_km is None or not entity.located or not entity.started_at:
        return []
    window_days = block[1]
    members = class_members(entity.hazard_type)
    dlat, dlon = geo.degree_window(entity.lat, radius_km)
    start = parse_iso(entity.started_at)
    params: dict = {
        "lat_min": entity.lat - dlat,
        "lat_max": entity.lat + dlat,
        "t_min": to_iso(start - timedelta(days=window_days)),
        "t_max": to_iso(start + timedelta(days=window_days)),
    }
    params.update({f"h{i}": h for i, h in enumerate(members)})
    if entity.lon - dlon > -180 and entity.lon + dlon < 180:  # near the antimeridian the haversine check alone decides
        params.update(lon_min=entity.lon - dlon, lon_max=entity.lon + dlon)
        centroid_box = "e.centroid_lat BETWEEN :lat_min AND :lat_max AND e.centroid_lon BETWEEN :lon_min AND :lon_max"
        record_box = "r.lat BETWEEN :lat_min AND :lat_max AND r.lon BETWEEN :lon_min AND :lon_max"
    else:
        centroid_box = "e.centroid_lat BETWEEN :lat_min AND :lat_max"
        record_box = "r.lat BETWEEN :lat_min AND :lat_max"
    clauses = [
        "e.merged_into_event_id IS NULL",
        "e.status IN ('active', 'ended')",
        f"e.hazard_type IN ({', '.join(':h' + str(i) for i in range(len(members)))})",
        "e.started_at BETWEEN :t_min AND :t_max",
        f"(({centroid_box}) OR EXISTS (SELECT 1 FROM source_record r WHERE r.event_id = e.event_id AND {record_box}))",
    ]
    if entity.source_ids:
        names = [f":s{i}" for i in range(len(entity.source_ids))]
        clauses.append(f"NOT EXISTS (SELECT 1 FROM source_record r WHERE r.event_id = e.event_id AND r.source_id IN ({', '.join(names)}))")
        params.update({f"s{i}": s for i, s in enumerate(sorted(entity.source_ids))})
    excluded = [e for e in exclude_event_ids if e]
    if excluded:
        names = [f":x{i}" for i in range(len(excluded))]
        clauses.append(f"e.event_id NOT IN ({', '.join(names)})")
        params.update({f"x{i}": e for i, e in enumerate(excluded)})
    rows = conn.execute(f"SELECT e.* FROM event e WHERE {' AND '.join(clauses)} ORDER BY e.started_at, e.event_id", params).fetchall()
    out: list[tuple[sqlite3.Row, Entity]] = []
    for row in rows:
        other = entity_from_event(conn, row)
        distance = min_distance_km(entity, other)
        if distance is not None and distance <= radius_km:
            out.append((row, other))
    return out


# ----------------------------------------------------------------------------- scoring
def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))


def score(a: Entity, b: Entity) -> Score:
    """Score entity `a` (the incoming one; its hazard type picks R and T) against `b`."""
    block = blocking_for(a.hazard_type) or blocking_for(b.hazard_type)
    radius_km, window_days = block if block else (None, None)
    distance = min_distance_km(a, b)
    days = None
    if a.started_at and b.started_at:
        days = abs((parse_iso(a.started_at) - parse_iso(b.started_at)).total_seconds()) / 86400.0
    spatial = _clip(1 - distance / radius_km) if distance is not None and radius_km else 0.0
    temporal = _clip(1 - days / window_days) if days is not None and window_days else 0.0
    text = title_similarity(a.title, b.title)
    glide_match = bool(a.glide_number and b.glide_number and a.glide_number == b.glide_number)
    same_class = hazard_class(a.hazard_type) is not None and hazard_class(a.hazard_type) == hazard_class(b.hazard_type)
    both_named = bool(same_class and hazard_class(a.hazard_type) == "storm" and a.storm_name and b.storm_name)
    name_match = bool(both_named and a.storm_name == b.storm_name and (days is None or window_days is None or days <= window_days))
    names_differ = bool(both_named and a.storm_name != b.storm_name)
    key_match = bool(shared_keys(a, b))
    weights = config.SCORE_WEIGHTS
    weighted = weights["spatial"] * spatial + weights["temporal"] * temporal + weights["text"] * text
    best, rule = (weighted, "weighted") if same_class else (0.0, "none")
    if name_match and config.SCORE_STORM_NAME_EQUAL > best:
        best, rule = config.SCORE_STORM_NAME_EQUAL, "storm_name"
    if glide_match and config.SCORE_GLIDE_EQUAL > best:
        best, rule = config.SCORE_GLIDE_EQUAL, "glide"
    if key_match and config.SCORE_KEY_EQUAL > best:
        best, rule = config.SCORE_KEY_EQUAL, "key"
    return Score(
        score=round(best, 4),
        rule=rule,
        distance_km=None if distance is None else round(distance, 1),
        days_apart=None if days is None else round(days, 2),
        spatial=round(spatial, 3),
        temporal=round(temporal, 3),
        text_sim=round(text, 3),
        name_match=name_match,
        glide_match=glide_match,
        radius_km=radius_km,
        window_days=window_days,
        names_differ=names_differ,
        key_match=key_match,
    )


def evidence(a: Entity, b: Entity, s: Score) -> dict:
    """What merge_proposal.evidence and event_lineage.evidence record for a scored pair."""
    inside, _, radius = within_aggregation_radius(a, b)
    return {
        "rule": s.rule,
        "score": s.score,
        "distance_km": s.distance_km,
        "aggregation_radius_km": radius,
        "within_aggregation_radius": inside,
        "keys": [list(k) for k in shared_keys(a, b)],
        "days_apart": s.days_apart,
        "spatial": s.spatial,
        "temporal": s.temporal,
        "text_sim": s.text_sim,
        "name_match": s.name_match,
        "names_differ": s.names_differ,
        "glide_match": s.glide_match,
        "radius_km": s.radius_km,
        "window_days": s.window_days,
        "title_a": a.title,
        "title_b": b.title,
        "hazard_a": a.hazard_type,
        "hazard_b": b.hazard_type,
        "started_a": a.started_at,
        "started_b": b.started_at,
        "source_a": sorted(a.source_ids),
        "source_b": sorted(b.source_ids),
        "storm_name_a": a.storm_name,
        "storm_name_b": b.storm_name,
        "similarity_method": config.TITLE_SIMILARITY,
    }
