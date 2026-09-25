"""Attach: document -> event (docs/architecture.md §3, step 2). Documents never create events.

    attach_document(conn, doc, ctx) -> Decision      run(conn, rebuild=False) -> AttachStats

Candidates are live events of the document's hazard class whose window [started_at - 2 d,
coalesce(ended_at, last_observed_at) + 7 d] overlaps the document's [published_at - 7 d, published_at + 1 d],
and that a resolved place puts within 2R of (any position of) the event, or that share its country, or
whose own query retrieved the document. For each: spatial is 1.0 within R, 0.5 within 2R or the same
country, else 0; temporal is 1.0 inside the event window and decays linearly to 0 over the next 7 days;
text is the cosine of the document vector and the event's centroid vector (title plus attached
documents, running mean, updated on every attach); s = 0.45 spatial + 0.25 temporal + 0.30 text, or with
no resolved place s = 0.20 temporal + 0.80 text with text at least 0.60; +0.10 when the event's query
fetched the document. The best candidate at or above 0.75 is attached, at or above 0.55 a candidate for
the Review tab, below that nothing is written. Every number is in identity.yaml (attachment:).

Determinism: documents are processed in document_id order (ULIDs: fetch order) and the centroid grows
as they attach, so `run(rebuild=True)` after deleting every pipeline decision replays the same sequence
and reproduces the same rows for the same events; human decisions are kept and skipped.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from eww import config, embed, geo, matching
from eww.clock import now_iso, now_utc, parse_iso, to_iso
from eww.events import canonical_event_id
from eww.ids import new_id

log = logging.getLogger(__name__)

PIPELINE = "pipeline"


@dataclass
class Decision:
    document_id: str
    status: str | None  # 'attached' | 'candidate' | None
    event_id: str | None = None
    score: float | None = None
    parts: dict = field(default_factory=dict)
    candidates: int = 0
    reason: str | None = None


@dataclass
class AttachStats:
    documents: int = 0
    attached: int = 0
    candidates: int = 0
    none: int = 0
    no_hazard: int = 0
    no_embedding: int = 0
    no_candidates: int = 0
    mentions: int = 0
    rebuilt_rows_deleted: int = 0


class RunContext:
    """Per-run state: event rows, positions, centroid vectors; everything computed once and updated on attach."""

    def __init__(self, conn: sqlite3.Connection, now: datetime | None = None):
        self.conn = conn
        self.now = now or now_utc()
        self.settings = config.ATTACHMENT
        self._events: dict[str, sqlite3.Row] = {}
        self._positions: dict[str, tuple[tuple[float, float], ...]] = {}
        self._centroid: dict[str, tuple[np.ndarray, int]] = {}  # event_id -> (sum of unit vectors, count)

    # ------------------------------------------------------------------ events
    def event(self, event_id: str) -> sqlite3.Row | None:
        if event_id not in self._events:
            self._events[event_id] = self.conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
        return self._events[event_id]

    def positions(self, event: sqlite3.Row) -> tuple[tuple[float, float], ...]:
        event_id = event["event_id"]
        if event_id not in self._positions:
            entity = matching.entity_from_event(self.conn, event)
            self._positions[event_id] = entity.all_positions
        return self._positions[event_id]

    def centroid(self, event: sqlite3.Row) -> np.ndarray | None:
        event_id = event["event_id"]
        if event_id not in self._centroid:
            vectors = [embed.title_vector(event["title"] or "")]
            attached = [
                row[0]
                for row in self.conn.execute("SELECT document_id FROM event_document WHERE event_id = ? AND status = 'attached' ORDER BY document_id", (event_id,))
            ]
            vectors += list(embed.vectors_for(self.conn, attached).values())
            total = np.sum(np.stack([embed.unit(v) for v in vectors]), axis=0).astype(embed.DTYPE)
            self._centroid[event_id] = (total, len(vectors))
        total, count = self._centroid[event_id]
        return embed.unit(total) if count else None

    def add_to_centroid(self, event_id: str, vector: np.ndarray) -> None:
        total, count = self._centroid[event_id]
        self._centroid[event_id] = (total + embed.unit(vector).astype(embed.DTYPE), count + 1)

    def centroid_size(self, event_id: str) -> int:
        return self._centroid.get(event_id, (None, 0))[1]


# ----------------------------------------------------------------------------- helpers
def _window(published: datetime, settings: dict) -> tuple[datetime, datetime]:
    return published - timedelta(days=settings["doc_before_days"]), published + timedelta(days=settings["doc_after_days"])


def _event_window(event: sqlite3.Row, settings: dict) -> tuple[datetime, datetime, datetime]:
    """(start - before, end, end + after) where end is coalesce(ended_at, last_observed_at)."""
    start = parse_iso(event["started_at"]) - timedelta(days=settings["event_before_days"])
    end = parse_iso(event["ended_at"] or event["last_observed_at"])
    return start, end, end + timedelta(days=settings["event_after_days"])


def temporal_score(published: datetime, event: sqlite3.Row, settings: dict) -> float:
    start, end, _ = _event_window(event, settings)
    decay = timedelta(days=settings["decay_days"])
    if start <= published <= end:
        return 1.0
    gap = (published - end) if published > end else (start - published)
    return round(max(0.0, 1.0 - gap / decay), 4)


def spatial_score(places: list[dict], event: sqlite3.Row, positions, settings: dict) -> tuple[float, float | None, str | None]:
    """(score, closest distance km, place name): 1.0 within R, 0.5 within 2R or the same country, else 0."""
    block = matching.blocking_for(event["hazard_type"])
    radius = block[0] if block else config.aggregation_radius_km(event["hazard_type"])
    best: tuple[float, str] | None = None
    same_country = False
    for place in places:
        if place.get("country_iso3") and event["country_iso3"] and place["country_iso3"] == event["country_iso3"]:
            same_country = True
        if place.get("lat") is None or place.get("lon") is None or not positions:
            continue
        distance = min(geo.haversine_km(float(place["lat"]), float(place["lon"]), lat, lon) for lat, lon in positions)
        if best is None or distance < best[0]:
            best = (distance, place["name"])
    if best is not None and best[0] <= radius:
        return settings["spatial_within_radius"], round(best[0], 1), best[1]
    if (best is not None and best[0] <= 2 * radius) or same_country:
        return settings["spatial_within_double_or_country"], (round(best[0], 1) if best else None), (best[1] if best else None)
    return 0.0, (round(best[0], 1) if best else None), (best[1] if best else None)


def candidate_events(conn: sqlite3.Connection, hazard_type: str, published: datetime, settings: dict) -> list[sqlite3.Row]:
    """Live events of the hazard class whose window overlaps the document's; the spatial/country/query test follows."""
    members = matching.class_members(hazard_type)
    if not members:
        return []
    win_start, win_end = _window(published, settings)
    # overlap of [event_start - before, event_end + after] with [win_start, win_end], rearranged for SQL
    params: dict = {
        "ev_start_limit": to_iso(win_end + timedelta(days=settings["event_before_days"])),
        "ev_end_limit": to_iso(win_start - timedelta(days=settings["event_after_days"])),
    }
    params.update({f"h{i}": h for i, h in enumerate(members)})
    return conn.execute(
        f"""
        SELECT e.* FROM event e
        WHERE e.merged_into_event_id IS NULL AND e.status IN ('active', 'ended')
          AND e.hazard_type IN ({', '.join(':h' + str(i) for i in range(len(members)))})
          AND e.started_at <= :ev_start_limit
          AND COALESCE(e.ended_at, e.last_observed_at) >= :ev_end_limit
        ORDER BY e.started_at, e.event_id
        """,
        params,
    ).fetchall()


def retrieval_event_ids(conn: sqlite3.Connection, document_id: str) -> set[str]:
    return {canonical_event_id(conn, row[0]) for row in conn.execute("SELECT event_id FROM document_retrieval WHERE document_id = ?", (document_id,))}


# ----------------------------------------------------------------------------- the decision
def attach_document(conn: sqlite3.Connection, doc: sqlite3.Row, ctx: RunContext, *, write: bool = True) -> Decision:
    extraction = conn.execute("SELECT * FROM document_extraction WHERE document_id = ?", (doc["document_id"],)).fetchone()
    if extraction is None or not extraction["hazard_type"]:
        return Decision(doc["document_id"], None, reason="no hazard")
    vectors = embed.vectors_for(conn, [doc["document_id"]])
    vector = vectors.get(doc["document_id"])
    if vector is None:
        return Decision(doc["document_id"], None, reason="no embedding")
    settings = ctx.settings
    published_iso = doc["published_at"] or doc["fetched_at"]
    published = parse_iso(published_iso)
    places = json.loads(extraction["places"] or "[]")
    located = [p for p in places if p.get("lat") is not None and p.get("lon") is not None]
    retrieved_for = retrieval_event_ids(conn, doc["document_id"])
    weights, no_place_weights = settings["weights"], settings["no_place_weights"]
    scored: list[tuple[float, str, dict]] = []
    for event in candidate_events(conn, extraction["hazard_type"], published, settings):
        positions = ctx.positions(event)
        spatial, distance, place = spatial_score(places, event, positions, settings)
        queried = event["event_id"] in retrieved_for
        if spatial <= 0.0 and not queried:
            continue  # neither within 2R, nor the same country, nor retrieved for this event
        centroid = ctx.centroid(event)
        text = round(max(0.0, min(1.0, embed.cosine(vector, centroid))), 4) if centroid is not None else 0.0
        temporal = temporal_score(published, event, settings)
        if located:
            score = weights["spatial"] * spatial + weights["temporal"] * temporal + weights["text"] * text
            no_place = False
        else:
            if text < settings["no_place_min_text"]:
                continue
            score = no_place_weights["temporal"] * temporal + no_place_weights["text"] * text
            no_place = True
        prior = settings["query_prior"] if queried else 0.0
        score = round(min(1.0, score + prior), 4)
        parts = {
            "spatial": spatial,
            "temporal": temporal,
            "text": text,
            "hazard": extraction["hazard_type"],
            "hazard_confidence": extraction["hazard_confidence"],
            "query_prior": prior,
            "no_place": no_place,
            "distance_km": distance,
            "place": place,
            "centroid_documents": max(0, ctx.centroid_size(event["event_id"]) - 1),
            "event_hazard": event["hazard_type"],
        }
        scored.append((score, event["event_id"], parts))
    if not scored:
        return Decision(doc["document_id"], None, reason="no candidates", candidates=0)
    scored.sort(key=lambda item: (-item[0], item[1]))
    score, event_id, parts = scored[0]
    parts["candidates"] = len(scored)
    parts["runner_up"] = round(scored[1][0], 4) if len(scored) > 1 else None
    if score >= settings["attach_threshold"]:
        status = "attached"
    elif score >= settings["candidate_threshold"]:
        status = "candidate"
    else:
        return Decision(doc["document_id"], None, event_id, score, parts, len(scored), reason="below the candidate threshold")
    decision = Decision(doc["document_id"], status, event_id, score, parts, len(scored))
    if write:
        method = "query" if event_id in retrieved_for else "embedding"
        write_decision(conn, decision, method)
        if status == "attached":
            ctx.add_to_centroid(event_id, vector)
            write_mentions(conn, event_id, doc, located)
    return decision


def write_decision(conn: sqlite3.Connection, decision: Decision, method: str, decided_by: str = PIPELINE) -> None:
    conn.execute(
        """
        INSERT INTO event_document (event_id, document_id, status, score, score_parts, method, decided_at, decided_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id, document_id) DO UPDATE SET status = excluded.status, score = excluded.score,
            score_parts = excluded.score_parts, method = excluded.method, decided_at = excluded.decided_at, decided_by = excluded.decided_by
        """,
        (decision.event_id, decision.document_id, decision.status, decision.score, json.dumps(decision.parts, ensure_ascii=False), method, now_iso(), decided_by),
    )


def write_mentions(conn: sqlite3.Connection, event_id: str, doc: sqlite3.Row, located: list[dict]) -> int:
    """One event_geometry row (role 'mention') per resolved place of an attached document; replaces earlier ones."""
    conn.execute("DELETE FROM event_geometry WHERE role = 'mention' AND document_id = ?", (doc["document_id"],))
    written = 0
    for place in located:
        lat, lon = float(place["lat"]), float(place["lon"])
        conn.execute(
            """
            INSERT INTO event_geometry (geometry_id, event_id, role, geojson, min_lat, min_lon, max_lat, max_lon, precision, observed_at, source_id, source_record_id, document_id, is_primary)
            VALUES (?, ?, 'mention', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0)
            """,
            (new_id(), event_id, json.dumps({"type": "Point", "coordinates": [lon, lat]}), lat, lon, lat, lon, place.get("precision") or "unresolved", doc["published_at"] or doc["fetched_at"], doc["source_id"], doc["document_id"]),
        )
        written += 1
    return written


def human_decide(conn: sqlite3.Connection, event_id: str, document_id: str, status: str) -> None:
    """The Review tab's accept/reject: the row becomes a human decision and mentions follow the status."""
    if status not in ("attached", "rejected"):
        raise ValueError("status must be 'attached' or 'rejected'")
    row = conn.execute("SELECT * FROM event_document WHERE event_id = ? AND document_id = ?", (event_id, document_id)).fetchone()
    if row is None:
        raise ValueError(f"no event_document row for event {event_id} and document {document_id}")
    parts = json.loads(row["score_parts"]) if row["score_parts"] else {}
    parts["human"] = status
    decision = Decision(document_id, status, event_id, row["score"], parts)
    write_decision(conn, decision, "human", "human")
    doc = conn.execute("SELECT * FROM document WHERE document_id = ?", (document_id,)).fetchone()
    if status == "attached":
        extraction = conn.execute("SELECT places FROM document_extraction WHERE document_id = ?", (document_id,)).fetchone()
        places = json.loads(extraction["places"]) if extraction and extraction["places"] else []
        write_mentions(conn, event_id, doc, [p for p in places if p.get("lat") is not None])
    else:
        conn.execute("DELETE FROM event_geometry WHERE role = 'mention' AND document_id = ? AND event_id = ?", (document_id, event_id))


# ----------------------------------------------------------------------------- the run
def pending_documents(conn: sqlite3.Connection, *, rebuild: bool, days: int | None, now: datetime) -> list[sqlite3.Row]:
    """Documents with an extraction and a vector and no decision yet; the incremental run keeps to recent ones."""
    clauses = [
        "d.removed_at IS NULL",
        "EXISTS (SELECT 1 FROM document_extraction x WHERE x.document_id = d.document_id AND x.hazard_type IS NOT NULL)",
        "EXISTS (SELECT 1 FROM document_embedding v WHERE v.document_id = d.document_id)",
        "NOT EXISTS (SELECT 1 FROM event_document ed WHERE ed.document_id = d.document_id)",
    ]
    params: dict = {}
    if not rebuild and days:
        clauses.append("COALESCE(d.published_at, d.fetched_at) >= :since")
        params["since"] = to_iso(now - timedelta(days=days))
    return conn.execute(f"SELECT d.* FROM document d WHERE {' AND '.join(clauses)} ORDER BY d.document_id", params).fetchall()


def run(conn: sqlite3.Connection, *, rebuild: bool = False, days: int | None = None, now: datetime | None = None) -> AttachStats:
    now = now or now_utc()
    days = config.ATTACH_RETRY_DAYS if days is None else days
    stats = AttachStats()
    if rebuild:
        with conn:
            stats.rebuilt_rows_deleted = conn.execute("DELETE FROM event_document WHERE decided_by = ?", (PIPELINE,)).rowcount
            conn.execute(
                "DELETE FROM event_geometry WHERE role = 'mention' AND NOT EXISTS (SELECT 1 FROM event_document ed WHERE ed.document_id = event_geometry.document_id AND ed.event_id = event_geometry.event_id AND ed.status = 'attached')"
            )
    ctx = RunContext(conn, now)
    docs = pending_documents(conn, rebuild=rebuild, days=days, now=now)
    for doc in docs:
        with conn:
            decision = attach_document(conn, doc, ctx)
        stats.documents += 1
        if decision.status == "attached":
            stats.attached += 1
        elif decision.status == "candidate":
            stats.candidates += 1
        elif decision.reason == "no hazard":
            stats.no_hazard += 1
        elif decision.reason == "no embedding":
            stats.no_embedding += 1
        elif decision.reason == "no candidates":
            stats.no_candidates += 1
        else:
            stats.none += 1
    stats.mentions = conn.execute("SELECT COUNT(*) FROM event_geometry WHERE role = 'mention'").fetchone()[0]
    log.info(
        "attach documents=%d attached=%d candidates=%d none=%d no_candidates=%d rebuild=%s deleted=%d",
        stats.documents, stats.attached, stats.candidates, stats.none, stats.no_candidates, rebuild, stats.rebuilt_rows_deleted,
    )
    return stats


# ----------------------------------------------------------------------------- what `eww doctor` reports
def coverage(conn: sqlite3.Connection, *, min_severity: float = 0.66, days: int = 14, min_documents: int = 3, now: datetime | None = None) -> dict:
    """Exit criterion 1: the share of severe events active in the window with at least `min_documents` attached."""
    now = now or now_utc()
    since = to_iso(now - timedelta(days=days))
    rows = conn.execute(
        """
        SELECT e.event_id, e.title, e.hazard_type, e.severity_score, e.country_iso3,
               (SELECT COUNT(*) FROM event_document ed JOIN document d ON d.document_id = ed.document_id
                 WHERE ed.event_id = e.event_id AND ed.status = 'attached' AND d.removed_at IS NULL) AS attached,
               (SELECT COUNT(*) FROM event_document ed WHERE ed.event_id = e.event_id AND ed.status = 'candidate') AS candidates,
               (SELECT COUNT(*) FROM enrichment_run r WHERE r.event_id = e.event_id) AS queried
        FROM event e
        WHERE e.merged_into_event_id IS NULL AND e.status IN ('active', 'ended')
          AND COALESCE(e.severity_score, 0) >= :min_severity
          AND COALESCE(e.ended_at, e.last_observed_at) >= :since
        ORDER BY e.severity_score DESC, e.last_observed_at DESC
        """,
        {"min_severity": min_severity, "since": since},
    ).fetchall()
    covered = [dict(r) for r in rows if r["attached"] >= min_documents]
    return {
        "since": since,
        "min_severity": min_severity,
        "min_documents": min_documents,
        "events": len(rows),
        "covered": len(covered),
        "share": (len(covered) / len(rows)) if rows else None,
        "rows": [dict(r) for r in rows],
    }
