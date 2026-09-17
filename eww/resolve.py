"""Resolve: source_record -> event. `create_event()` is the only INSERT INTO event in the codebase.

The identity rules of docs/architecture.md §3 ("How identity is decided"), applied to every
unresolved record in config.RESOLVE_SOURCE_ORDER (GDACS before the feeds that point at it):

1. GLIDE: the live event carrying the record's GLIDE number, unless that event already holds a
   record of the same source under a different external id (GDACS gave two cyclones one GLIDE on
   2026-09-17; the source's own identity wins over the shared number).
2. Sibling: the event of an earlier record with the same (source_id, external_id): a new GDACS
   episode, a new EONET geometry entry. The event is then re-scored (step 4) with the new record in
   its track, because a storm's first point may be far from the other feed's current position and a
   GDACS depression gets its name in a later episode.
3. Deterministic cross-source key, both ways: the record's `linked_ids()` (Copernicus `gdacsId`,
   the GDACS report URL among EONET's sources) name an event holding that GDACS eventid, or an
   already-resolved record names this one (LinkIndex).
4. Blocking and scoring (eww.matching): same hazard class, |Δstart| <= T, distance <= R, other
   sources only, never a pair the sources themselves keep apart (key_conflict). score >=
   config.AUTO_MERGE_THRESHOLD: merge at once, unless the two are differently named storms or a
   person has undone that very merge before; >= config.PROPOSAL_THRESHOLD: write a merge_proposal.

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


@dataclass
class ResolveStats:
    records_resolved: int = 0
    events_created: int = 0  # event rows inserted, including those merged or proposed straight away
    events_attached: int = 0  # attached to an existing event by GLIDE, sibling or deterministic key
    events_linked: int = 0  # of which through a deterministic cross-source key
    events_merged: int = 0  # merged automatically (score >= AUTO_MERGE_THRESHOLD), at creation or on re-scoring
    proposals_created: int = 0
    events_refreshed: int = 0
    events_changed: int = 0


@dataclass
class Outcome:
    event_id: str  # the live event the record ended up on
    action: str  # one of ACTIONS
    score: float | None = None
    created_event_id: str | None = None  # for 'merged': the row created and folded into event_id
    follow_up: str | None = None  # 'merged' | 'proposed' when re-scoring after a sibling attach did something


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
            if outcome.action == "proposed" or outcome.follow_up == "proposed":
                stats.proposals_created += 1
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
        "resolve records=%d created=%d attached=%d linked=%d merged=%d proposed=%d refreshed=%d changed=%d",
        stats.records_resolved, stats.events_created, stats.events_attached, stats.events_linked, stats.events_merged,
        stats.proposals_created, stats.events_refreshed, stats.events_changed,
    )
    return stats


def resolve_record(conn: sqlite3.Connection, rec: sqlite3.Row, links: LinkIndex | None = None) -> Outcome:
    """Attach one unresolved record to an event, creating (and possibly merging) the event if needed."""
    links = links if links is not None else LinkIndex.build(conn)
    payload = payload_of(rec)
    collector = collectors.get(rec["source_id"])

    # 1. GLIDE, unless the source itself keeps this record apart from the event holding the number
    if rec["glide_number"]:
        row = conn.execute(
            "SELECT event_id FROM event WHERE glide_number = ? AND merged_into_event_id IS NULL", (rec["glide_number"],)
        ).fetchone()
        if row:
            clash = conn.execute(
                "SELECT 1 FROM source_record WHERE event_id = ? AND source_id = ? AND external_id <> ? LIMIT 1",
                (row["event_id"], rec["source_id"], rec["external_id"]),
            ).fetchone()
            if clash is None:
                return _finish(conn, rec, payload, row["event_id"], "glide", links)
            log.info("glide %s shared by two %s ids; %s stays apart from event %s", rec["glide_number"], rec["source_id"], rec["external_id"], row["event_id"])

    # 2. an earlier record of the same feed item, then a re-score with the track grown by one point
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
        follow_up = _rescore_event(conn, outcome.event_id, links)
        if follow_up is not None:
            outcome.follow_up, outcome.score = follow_up
            outcome.event_id = canonical_event_id(conn, outcome.event_id)
        return outcome

    # 3. deterministic cross-source keys, forward (this record names another feed's id) and reverse
    for source_id, external_id in collector.linked_ids(payload):
        row = conn.execute(
            """
            SELECT event_id FROM source_record WHERE source_id = ? AND external_id = ? AND event_id IS NOT NULL
            ORDER BY observed_at DESC, source_record_id DESC LIMIT 1
            """,
            (source_id, str(external_id)),
        ).fetchone()
        if row:
            return _finish(conn, rec, payload, canonical_event_id(conn, row["event_id"]), "linked", links)
    reverse = links.lookup(rec["source_id"], rec["external_id"])
    if reverse:
        return _finish(conn, rec, payload, canonical_event_id(conn, reverse), "linked", links)

    # 4. blocking and scoring against events other feeds created
    entity = matching.entity_from_record(rec)
    best = _best_candidate(conn, entity)
    event_id = create_event(conn, rec)
    _attach(conn, rec["source_record_id"], event_id)
    decision = _decide(conn, event_id, entity, best) if best else None
    if decision == "merged":
        candidate = best[0]
        links.add_record(rec["source_id"], payload, candidate["event_id"])
        return Outcome(candidate["event_id"], "merged", best[1].score, created_event_id=event_id)
    links.add_record(rec["source_id"], payload, event_id)
    if decision == "proposed":
        return Outcome(event_id, "proposed", best[1].score)
    return Outcome(event_id, "created", best[1].score if best else None)


def _best_candidate(conn: sqlite3.Connection, entity: matching.Entity, *, exclude=()) -> tuple[sqlite3.Row, matching.Score, matching.Entity] | None:
    best: tuple[sqlite3.Row, matching.Score, matching.Entity] | None = None
    for candidate, other in matching.candidate_events(conn, entity, exclude_event_ids=exclude):
        if matching.key_conflict(entity, other):  # the sources themselves keep these two apart
            log.debug("skip candidate %s: conflicting deterministic keys", candidate["event_id"])
            continue
        scored = matching.score(entity, other)
        if best is None or scored.score > best[1].score:
            best = (candidate, scored, other)
    return best


def _decide(conn: sqlite3.Connection, event_id: str, entity: matching.Entity, best) -> str | None:
    """Merge `event_id` into the best candidate, propose the pair, or do nothing. Returns what happened."""
    candidate, scored, other = best
    if scored.score < config.PROPOSAL_THRESHOLD:
        return None
    if merge_mod.pair_reverted_by_human(conn, event_id, candidate["event_id"]):
        log.info("event %s ~ %s: a person reverted this merge before; leaving it alone", event_id, candidate["event_id"])
        return None
    evidence = matching.evidence(entity, other, scored)
    if scored.score >= config.AUTO_MERGE_THRESHOLD and not scored.names_differ:
        merge_mod.merge(conn, event_id, candidate["event_id"], "pipeline", score=scored.score, evidence=evidence)
        log.info("auto-merge event %s -> %s score=%.3f rule=%s", event_id, candidate["event_id"], scored.score, scored.rule)
        return "merged"
    if merge_mod.propose(conn, event_id, candidate["event_id"], scored.score, evidence) is not None:
        return "proposed"
    return None


def _rescore_event(conn: sqlite3.Connection, event_id: str, links: LinkIndex) -> tuple[str, float] | None:
    """After a sibling attach: score the live event, with its grown track, against other feeds' events."""
    event = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
    if event is None or event["merged_into_event_id"] is not None:
        return None
    recs = event_records(conn, event_id)
    entity = matching.entity_from_event(conn, event, recs)
    if len(entity.source_ids) > 1:  # already a cross-source event: nothing left to find among the other feeds
        return None
    best = _best_candidate(conn, entity)
    if best is None:
        return None
    decision = _decide(conn, event_id, entity, best)
    if decision == "merged":
        for rec in recs:
            links.add_record(rec["source_id"], payload_of(rec), best[0]["event_id"])
    return (decision, best[1].score) if decision else None


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
