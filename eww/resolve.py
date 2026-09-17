"""Resolve: source_record -> event. `create_event()` is the only INSERT INTO event in the codebase.

The identity rules of docs/architecture.md §3 ("How identity is decided"), applied to every
unresolved record in config.RESOLVE_SOURCE_ORDER (GDACS before the feeds that point at it). Two
feeds' records share one pin automatically only when their positions lie inside the aggregation
radius of identity.yaml (config.aggregation_radius_km, closest pair of positions); beyond it the
pipeline keeps two pins and writes a merge proposal, whatever a shared id or a score says.

1. GLIDE: the live event carrying the record's GLIDE number, unless that event already holds a
   record of the same source under a different external id (GDACS gave two cyclones one GLIDE on
   2026-09-17). Inside the aggregation radius the record attaches; beyond it the event stays a
   candidate for step 4 and ends up as a proposal.
2. Sibling: the event of an earlier record with the same (source_id, external_id): a new GDACS
   episode, a new EONET geometry entry. Never gated: one feed's own item is one event. The event is
   then re-scored (step 4) with the new record in its track, because a storm's first point may be
   far from the other feed's current position and a GDACS depression is named in a later episode.
3. Deterministic cross-source key, both ways: the record's `linked_ids()` (Copernicus `gdacsId`,
   the GDACS report URL among EONET's sources) name an event holding that GDACS eventid, or an
   already-resolved record names this one (LinkIndex). Inside the radius: attach; beyond: step 4.
4. Blocking and scoring (eww.matching): same hazard class, |Δstart| <= T, distance <= R, other
   sources only, never a pair the sources themselves keep apart (key_conflict); the id-named
   events of steps 1 and 3 join the candidates and score by their rule ('key', 'glide'). The best
   candidate at or above config.AUTO_MERGE_THRESHOLD merges at once when it is inside the
   aggregation radius, the storms are not differently named and no person has undone that merge
   before; every id-named candidate and the best scored one at or above config.PROPOSAL_THRESHOLD
   otherwise become merge proposals.

Steps 1 to 3 attach without a lineage row: they follow what the sources themselves assert. Step 4
is the pipeline's inference, so it goes through eww.merge, leaves an event_lineage row and can be
undone in the Review tab. Afterwards eww.events.refresh_event() recomputes every touched event
from its records; running `eww resolve` twice is a no-op the second time.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from eww import collectors, config, matching
from eww import merge as merge_mod
from eww.clock import now_iso, now_utc, to_iso
from eww.events import canonical_event_id, derive, event_records, payload_of, refresh_event  # noqa: F401  (re-exported)
from eww.ids import new_id

log = logging.getLogger(__name__)

ACTIONS = ("created", "attached", "glide", "linked", "merged", "proposed")
ID_RULES = ("key", "glide")  # rules that rest on an identifier one feed states, not on a score

Candidate = tuple[sqlite3.Row, matching.Score, matching.Entity]


@dataclass
class ResolveStats:
    records_resolved: int = 0
    events_created: int = 0  # event rows inserted, including those merged or proposed straight away
    events_attached: int = 0  # attached to an existing event by GLIDE, sibling or deterministic key
    events_linked: int = 0  # of which through a deterministic cross-source key
    events_merged: int = 0  # merged automatically (score >= AUTO_MERGE_THRESHOLD), at creation or on re-scoring
    proposals_created: int = 0
    records_gated: int = 0  # records whose id-named event lay beyond the aggregation radius: proposed, not joined
    events_refreshed: int = 0
    events_changed: int = 0


@dataclass
class Outcome:
    event_id: str  # the live event the record ended up on
    action: str  # one of ACTIONS
    score: float | None = None
    created_event_id: str | None = None  # for 'merged': the row created and folded into event_id
    follow_up: str | None = None  # 'merged' | 'proposed' when re-scoring after a sibling attach did something
    proposals: int = 0  # merge_proposal rows this record caused
    gated: int = 0  # id-named events beyond the aggregation radius that were kept apart


class LinkIndex:
    """(source_id, external_id) -> event_id for every deterministic key an already-resolved record carries.

    Lets a GDACS record that arrives after its EONET or Copernicus mirror find the event the mirror
    created. Built once per resolve() pass and kept current as records are attached.
    """

    def __init__(self) -> None:
        self._map: dict[tuple[str, str], str] = {}

    @classmethod
    def build(cls, conn: sqlite3.Connection) -> "LinkIndex":
        index = cls()
        if not collectors.LINKING_SOURCES:
            return index
        marks = ", ".join("?" * len(collectors.LINKING_SOURCES))
        rows = conn.execute(
            f"SELECT source_id, event_id, payload FROM source_record WHERE event_id IS NOT NULL AND source_id IN ({marks}) "
            "ORDER BY observed_at, source_record_id",
            collectors.LINKING_SOURCES,
        )
        for row in rows:
            index.add_record(row["source_id"], payload_of(row), row["event_id"])
        return index

    def add_record(self, source_id: str, payload: dict, event_id: str) -> None:
        for key in collectors.get(source_id).linked_ids(payload):
            self._map.setdefault((key[0], str(key[1])), event_id)

    def lookup(self, source_id: str, external_id: str) -> str | None:
        return self._map.get((source_id, str(external_id)))

    def __len__(self) -> int:
        return len(self._map)


def resolve(conn: sqlite3.Connection, *, refresh_days: int | None = 30) -> ResolveStats:
    stats = ResolveStats()
    touched: set[str] = set()
    unresolved = conn.execute("SELECT * FROM source_record WHERE event_id IS NULL").fetchall()
    order = {source_id: i for i, source_id in enumerate(config.RESOLVE_SOURCE_ORDER)}
    unresolved.sort(key=lambda r: (order.get(r["source_id"], len(order)), r["source_id"], r["external_id"], r["observed_at"], r["source_record_id"]))
    links = LinkIndex.build(conn)
    with conn:
        for rec in unresolved:
            outcome = resolve_record(conn, rec, links)
            touched.add(outcome.event_id)
            if outcome.created_event_id:
                touched.add(outcome.created_event_id)
            stats.records_resolved += 1
            if outcome.action in ("created", "merged", "proposed"):
                stats.events_created += 1
            else:
                stats.events_attached += 1
            if outcome.action == "linked":
                stats.events_linked += 1
            if outcome.action == "merged" or outcome.follow_up == "merged":
                stats.events_merged += 1
            stats.proposals_created += outcome.proposals
            if outcome.gated:
                stats.records_gated += 1
    if refresh_days is not None:
        since = to_iso(now_utc() - timedelta(days=refresh_days))
        for row in conn.execute(
            "SELECT DISTINCT event_id FROM source_record WHERE event_id IS NOT NULL AND last_seen_at >= ?", (since,)
        ):
            touched.add(row[0])
    with conn:
        for event_id in sorted(touched):
            stats.events_refreshed += 1
            if refresh_event(conn, event_id):
                stats.events_changed += 1
    log.info(
        "resolve records=%d created=%d attached=%d linked=%d merged=%d proposed=%d gated=%d refreshed=%d changed=%d",
        stats.records_resolved, stats.events_created, stats.events_attached, stats.events_linked, stats.events_merged,
        stats.proposals_created, stats.records_gated, stats.events_refreshed, stats.events_changed,
    )
    return stats


def resolve_record(conn: sqlite3.Connection, rec: sqlite3.Row, links: LinkIndex | None = None) -> Outcome:
    """Attach one unresolved record to an event, creating (and possibly merging) the event if needed."""
    links = links if links is not None else LinkIndex.build(conn)
    payload = payload_of(rec)
    entity = matching.entity_from_record(rec)
    beyond: list[tuple[sqlite3.Row, matching.Entity]] = []  # id-named live events beyond the aggregation radius

    # 1. GLIDE, unless the source itself keeps this record apart from the event holding the number
    if rec["glide_number"]:
        row = conn.execute("SELECT * FROM event WHERE glide_number = ? AND merged_into_event_id IS NULL", (rec["glide_number"],)).fetchone()
        if row:
            clash = conn.execute(
                "SELECT 1 FROM source_record WHERE event_id = ? AND source_id = ? AND external_id <> ? LIMIT 1",
                (row["event_id"], rec["source_id"], rec["external_id"]),
            ).fetchone()
            if clash is None:
                other = matching.entity_from_event(conn, row)
                inside, distance, radius = matching.within_aggregation_radius(entity, other)
                if inside:
                    return _finish(conn, rec, payload, row["event_id"], "glide", links)
                log.info("glide %s: event %s lies %s km away, beyond the %.0f km aggregation radius; kept apart", rec["glide_number"], row["event_id"], distance, radius)
                beyond.append((row, other))
            else:
                log.info("glide %s shared by two %s ids; %s stays apart from event %s", rec["glide_number"], rec["source_id"], rec["external_id"], row["event_id"])

    # 2. an earlier record of the same feed item (never gated), then a re-score with the track grown by one point
    row = conn.execute(
        """
        SELECT event_id FROM source_record
        WHERE source_id = ? AND external_id = ? AND event_id IS NOT NULL
        ORDER BY observed_at DESC, source_record_id DESC LIMIT 1
        """,
        (rec["source_id"], rec["external_id"]),
    ).fetchone()
    if row:
        outcome = _finish(conn, rec, payload, canonical_event_id(conn, row["event_id"]), "attached", links)
        outcome.gated = len(beyond)
        decision, score, proposals = _rescore_event(conn, outcome.event_id, links, beyond)
        outcome.follow_up, outcome.proposals = decision, proposals
        if score is not None:
            outcome.score = score
        outcome.event_id = canonical_event_id(conn, outcome.event_id)
        return outcome

    # 3. deterministic cross-source keys, forward (this record names another feed's id) and reverse
    for row in _id_named_events(conn, entity, links, exclude={r["event_id"] for r, _ in beyond}):
        other = matching.entity_from_event(conn, row)
        inside, distance, radius = matching.within_aggregation_radius(entity, other)
        if inside:
            outcome = _finish(conn, rec, payload, row["event_id"], "linked", links)
            outcome.gated = len(beyond)
            outcome.proposals = _propose_id_named(conn, row["event_id"], entity, beyond)
            return outcome
        log.info("%s/%s names event %s, %s km away, beyond the %.0f km aggregation radius; kept apart", rec["source_id"], rec["external_id"], row["event_id"], distance, radius)
        beyond.append((row, other))

    # 4. blocking and scoring, with the id-named events beyond the radius among the candidates
    ranked = _ranked_candidates(conn, entity, beyond)
    event_id = create_event(conn, rec)
    _attach(conn, rec["source_record_id"], event_id)
    decision, proposals = _decide(conn, event_id, entity, ranked)
    final_event_id = canonical_event_id(conn, event_id)
    links.add_record(rec["source_id"], payload, final_event_id)
    action = "merged" if decision == "merged" else ("proposed" if proposals else "created")
    return Outcome(
        final_event_id,
        action,
        ranked[0][1].score if ranked else None,
        created_event_id=event_id if decision == "merged" else None,
        proposals=proposals,
        gated=len(beyond),
    )


def _id_named_events(conn: sqlite3.Connection, entity: matching.Entity, links: LinkIndex, exclude: set[str] = frozenset()) -> list[sqlite3.Row]:
    """Live events another feed's key ties to this entity: the ids it cites, and the records citing its ids."""
    event_ids: list[str] = []
    for source_id, external_id in sorted(entity.linked_ids):
        row = conn.execute(
            """
            SELECT event_id FROM source_record WHERE source_id = ? AND external_id = ? AND event_id IS NOT NULL
            ORDER BY observed_at DESC, source_record_id DESC LIMIT 1
            """,
            (source_id, external_id),
        ).fetchone()
        if row:
            event_ids.append(canonical_event_id(conn, row["event_id"]))
    for source_id, external_id in sorted(entity.external_ids):
        reverse = links.lookup(source_id, external_id)
        if reverse:
            event_ids.append(canonical_event_id(conn, reverse))
    rows: list[sqlite3.Row] = []
    for event_id in dict.fromkeys(event_ids):
        if event_id in exclude or event_id == entity.event_id:
            continue
        row = conn.execute("SELECT * FROM event WHERE event_id = ? AND merged_into_event_id IS NULL", (event_id,)).fetchone()
        if row is not None:
            rows.append(row)
    return rows


def _ranked_candidates(conn: sqlite3.Connection, entity: matching.Entity, id_named: list[tuple[sqlite3.Row, matching.Entity]]) -> list[Candidate]:
    """Blocked candidates plus the id-named events, scored, best first (older event first on a tie)."""
    scored: list[Candidate] = []
    seen = {row["event_id"] for row, _ in id_named}
    for row, other in matching.candidate_events(conn, entity):
        if row["event_id"] in seen:
            continue
        if matching.key_conflict(entity, other):  # the sources themselves keep these two apart
            log.debug("skip candidate %s: conflicting deterministic keys", row["event_id"])
            continue
        scored.append((row, matching.score(entity, other), other))
    for row, other in id_named:
        scored.append((row, matching.score(entity, other), other))
    scored.sort(key=lambda item: (-item[1].score, item[0]["started_at"], item[0]["event_id"]))
    return scored


def _decide(conn: sqlite3.Connection, event_id: str, entity: matching.Entity, ranked: list[Candidate]) -> tuple[str | None, int]:
    """Merge `event_id` into the best mergeable candidate, then propose what is left. Returns (decision, proposals)."""
    merged_into: str | None = None
    for row, scored, other in ranked:
        if scored.score < config.AUTO_MERGE_THRESHOLD:
            break
        if scored.names_differ:
            continue
        inside, distance, radius = matching.within_aggregation_radius(entity, other)
        if not inside:
            log.info("event %s ~ %s scores %.2f (%s) but lies %s km apart, beyond the %.0f km aggregation radius: proposed, not merged", event_id, row["event_id"], scored.score, scored.rule, distance, radius)
            continue
        if merge_mod.pair_reverted_by_human(conn, event_id, row["event_id"]):
            log.info("event %s ~ %s: a person reverted this merge before; leaving it alone", event_id, row["event_id"])
            continue
        merge_mod.merge(conn, event_id, row["event_id"], "pipeline", score=scored.score, evidence=matching.evidence(entity, other, scored))
        log.info("auto-merge event %s -> %s score=%.3f rule=%s", event_id, row["event_id"], scored.score, scored.rule)
        merged_into = row["event_id"]
        break
    origin = merged_into or event_id
    proposals = 0
    scored_proposal_written = False
    for row, scored, other in ranked:
        if scored.score < config.PROPOSAL_THRESHOLD:
            break
        if row["event_id"] in (event_id, merged_into):
            continue
        id_based = scored.rule in ID_RULES
        if not id_based and (scored_proposal_written or merged_into is not None):
            continue  # one scored proposal per record, and none once it has found a home
        if merge_mod.pair_reverted_by_human(conn, origin, row["event_id"]):
            continue
        if merge_mod.propose(conn, origin, row["event_id"], scored.score, matching.evidence(entity, other, scored)) is not None:
            proposals += 1
        if not id_based:
            scored_proposal_written = True
    return ("merged" if merged_into else ("proposed" if proposals else None)), proposals


def _propose_id_named(conn: sqlite3.Connection, event_id: str, entity: matching.Entity, beyond: list[tuple[sqlite3.Row, matching.Entity]]) -> int:
    """Proposals between the event a record joined and the id-named events it could not join."""
    proposals = 0
    for row, other in beyond:
        if row["event_id"] == event_id or merge_mod.pair_reverted_by_human(conn, event_id, row["event_id"]):
            continue
        scored = matching.score(entity, other)
        if merge_mod.propose(conn, event_id, row["event_id"], scored.score, matching.evidence(entity, other, scored)) is not None:
            proposals += 1
    return proposals


def _rescore_event(
    conn: sqlite3.Connection, event_id: str, links: LinkIndex, beyond: list[tuple[sqlite3.Row, matching.Entity]] = ()
) -> tuple[str | None, float | None, int]:
    """After a sibling attach: score the live single-source event, with its grown track, against other feeds' events."""
    event = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
    if event is None or event["merged_into_event_id"] is not None:
        return None, None, 0
    recs = event_records(conn, event_id)
    entity = matching.entity_from_event(conn, event, recs)
    if len(entity.source_ids) > 1:  # already a cross-source event: nothing left to find among the other feeds
        return None, None, 0
    id_named = list(beyond)
    seen = {row["event_id"] for row, _ in id_named}
    for row in _id_named_events(conn, entity, links, exclude=seen):
        id_named.append((row, matching.entity_from_event(conn, row)))
    ranked = _ranked_candidates(conn, entity, id_named)
    if not ranked:
        return None, None, 0
    decision, proposals = _decide(conn, event_id, entity, ranked)
    if decision == "merged":
        canonical = canonical_event_id(conn, event_id)
        for rec in recs:
            links.add_record(rec["source_id"], payload_of(rec), canonical)
    return decision, ranked[0][1].score, proposals


def _finish(conn: sqlite3.Connection, rec: sqlite3.Row, payload: dict, event_id: str, action: str, links: LinkIndex) -> Outcome:
    _attach(conn, rec["source_record_id"], event_id)
    links.add_record(rec["source_id"], payload, event_id)
    return Outcome(event_id, action)


def _attach(conn: sqlite3.Connection, source_record_id: str, event_id: str) -> None:
    conn.execute("UPDATE source_record SET event_id = ? WHERE source_record_id = ?", (event_id, source_record_id))


def create_event(conn: sqlite3.Connection, rec: sqlite3.Row) -> str:
    """THE ONLY INSERT INTO event. Derived columns are filled by refresh_event() right after."""
    event_id = new_id()
    now = now_iso()
    conn.execute(
        """
        INSERT INTO event (event_id, hazard_type, title, status, started_at, ended_at, last_observed_at,
                           centroid_lat, centroid_lon, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            rec["hazard_type"],
            rec["title"] or f"{rec['hazard_type']} {rec['external_id']}",
            "active" if rec["ended_at"] is None else "ended",
            rec["started_at"] or rec["observed_at"],
            rec["ended_at"],
            rec["observed_at"],
            rec["lat"],
            rec["lon"],
            now,
            now,
        ),
    )
    return event_id


def unresolved_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM source_record WHERE event_id IS NULL").fetchone()[0]
