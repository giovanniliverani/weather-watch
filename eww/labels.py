"""Hand-labelled merge pairs (M2 exit criteria 1 and 2).

`candidate_pairs()` compares every pair of source-level entities (one per (source_id,
external_id)) observed in the window that block on each other, cross-source only, and gives each
pair its evidence and the pipeline's current verdict; `same_event` stays empty for the human.
Rows are keyed by (source, external_id) on both sides, so labels survive rebuilds and merges.

`evaluate()` re-reads the database for every labelled pair: the pair is 'merged' when both
entities sit on the same canonical event, 'proposed' when an open merge_proposal joins their
events, 'rejected' or 'separate' otherwise. A merge counts as automatic when no human lineage
row moved the records involved (`merged_by` is 'pipeline' for a scored merge, 'key' for a
deterministic link). Precision and recall are those of the automatic merges against the labels.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from eww import config, events, matching
from eww import merge as merge_mod
from eww.clock import now_utc, parse_iso, to_iso

CANDIDATE_COLUMNS = [
    "source_a", "external_id_a", "title_a", "hazard_a", "started_a",
    "source_b", "external_id_b", "title_b", "hazard_b", "started_b",
    "distance_km", "days_apart", "text_sim", "name_match", "glide_match", "linked", "key_conflict",
    "score", "rule", "pipeline", "merged_by", "same_event",
]
YES = {"yes", "y", "true", "1", "same"}
NO = {"no", "n", "false", "0", "different"}


@dataclass
class SourceEntity:
    source_id: str
    external_id: str
    entity: matching.Entity
    record_ids: list[str] = field(default_factory=list)
    event_id: str | None = None
    links: set[tuple[str, str]] = field(default_factory=set)

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_id, self.external_id)


# ----------------------------------------------------------------------------- entities
def _entity_from_records(source_id: str, external_id: str, recs: list[sqlite3.Row]) -> SourceEntity:
    entity = matching.entity_from_records(recs)
    return SourceEntity(source_id, external_id, entity, [r["source_record_id"] for r in recs], recs[-1]["event_id"], set(entity.linked_ids))


def source_entities(conn: sqlite3.Connection, since_iso: str) -> list[SourceEntity]:
    """One entity per (source_id, external_id) with a record observed since `since_iso`, built from all of its records."""
    rows = conn.execute(
        """
        SELECT r.* FROM source_record r
        WHERE EXISTS (SELECT 1 FROM source_record w WHERE w.source_id = r.source_id AND w.external_id = r.external_id AND w.observed_at >= ?)
        ORDER BY r.source_id, r.external_id, r.observed_at, r.source_record_id
        """,
        (since_iso,),
    ).fetchall()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[(row["source_id"], row["external_id"])].append(row)
    return [_entity_from_records(s, e, recs) for (s, e), recs in groups.items()]


def source_entity(conn: sqlite3.Connection, source_id: str, external_id: str) -> SourceEntity | None:
    recs = conn.execute(
        "SELECT * FROM source_record WHERE source_id = ? AND external_id = ? ORDER BY observed_at, source_record_id", (source_id, external_id)
    ).fetchall()
    return _entity_from_records(source_id, external_id, recs) if recs else None


# ----------------------------------------------------------------------------- verdicts
def lineage_map(conn: sqlite3.Connection) -> dict[str, str]:
    """source_record_id -> performed_by of the live (not reverted) merge that moved it; 'human' wins."""
    out: dict[str, str] = {}
    for row in conn.execute("SELECT moved_source_records, performed_by FROM event_lineage WHERE action = 'merge' AND reverted_by_lineage_id IS NULL"):
        for record_id in json.loads(row["moved_source_records"] or "[]"):
            if out.get(record_id) != "human":
                out[record_id] = row["performed_by"]
    return out


def verdict(conn: sqlite3.Connection, a: SourceEntity, b: SourceEntity, moved: dict[str, str]) -> tuple[str, str | None]:
    """('merged', 'pipeline'|'key'|'human') | ('proposed', None) | ('rejected', None) | ('separate', None)."""
    if not a.event_id or not b.event_id:
        return "separate", None
    canonical_a = events.canonical_event_id(conn, a.event_id)
    canonical_b = events.canonical_event_id(conn, b.event_id)
    if canonical_a == canonical_b:
        performers = {moved[r] for r in [*a.record_ids, *b.record_ids] if r in moved}
        merged_by = "human" if "human" in performers else ("pipeline" if performers else "key")
        return "merged", merged_by
    proposal = merge_mod.find_proposal(conn, canonical_a, canonical_b)
    if proposal is not None and proposal["status"] == "open":
        return "proposed", None
    if proposal is not None and proposal["status"] == "rejected":
        return "rejected", None
    return "separate", None


# ----------------------------------------------------------------------------- candidates
def _ordered(a: SourceEntity, b: SourceEntity) -> tuple[SourceEntity, SourceEntity]:
    order = {s: i for i, s in enumerate(config.RESOLVE_SOURCE_ORDER)}
    return (a, b) if (order.get(a.source_id, 99), a.source_id) <= (order.get(b.source_id, 99), b.source_id) else (b, a)


def _row(first: SourceEntity, second: SourceEntity, scored: matching.Score, pipeline: str, merged_by: str | None) -> dict:
    linked = second.key in first.links or first.key in second.links
    return {
        "source_a": first.source_id, "external_id_a": first.external_id, "title_a": first.entity.title,
        "hazard_a": first.entity.hazard_type, "started_a": first.entity.started_at,
        "source_b": second.source_id, "external_id_b": second.external_id, "title_b": second.entity.title,
        "hazard_b": second.entity.hazard_type, "started_b": second.entity.started_at,
        "distance_km": scored.distance_km, "days_apart": scored.days_apart, "text_sim": scored.text_sim,
        "name_match": "yes" if scored.name_match else "no", "glide_match": "yes" if scored.glide_match else "no",
        "linked": "yes" if linked else "no", "key_conflict": "yes" if matching.key_conflict(first.entity, second.entity) else "no",
        "score": scored.score, "rule": scored.rule,
        "pipeline": pipeline, "merged_by": merged_by or "", "same_event": "",
    }


def candidate_pairs(conn: sqlite3.Connection, days: int = 30, now: datetime | None = None) -> list[dict]:
    """Every cross-source pair of entities in the window that blocks on either side's radius and window."""
    now = now or now_utc()
    entities = source_entities(conn, to_iso(now - timedelta(days=days)))
    moved = lineage_map(conn)
    by_class: dict[str, list[SourceEntity]] = defaultdict(list)
    for entity in entities:
        klass = matching.hazard_class(entity.entity.hazard_type)
        if klass and entity.entity.located and entity.entity.started_at:
            by_class[klass].append(entity)
    rows: list[dict] = []
    for klass, members in by_class.items():
        members.sort(key=lambda e: (e.entity.started_at, e.source_id, e.external_id))
        widest_window = max((matching.blocking_for(m.entity.hazard_type) or (0.0, 0.0))[1] for m in members)
        for i, a in enumerate(members):
            block_a = matching.blocking_for(a.entity.hazard_type) or (0.0, 0.0)
            radius_a = matching.blocking_radius(a.entity) or 0.0
            start_a = parse_iso(a.entity.started_at)
            for b in members[i + 1 :]:
                days_apart = (parse_iso(b.entity.started_at) - start_a).total_seconds() / 86400.0
                if days_apart > widest_window:
                    break
                if b.source_id == a.source_id:
                    continue
                block_b = matching.blocking_for(b.entity.hazard_type) or (0.0, 0.0)
                radius = max(radius_a, matching.blocking_radius(b.entity) or 0.0)
                window = max(block_a[1], block_b[1])
                if days_apart > window:
                    continue
                distance = matching.min_distance_km(a.entity, b.entity)
                if distance is None or distance > radius:
                    continue
                first, second = _ordered(a, b)
                scored = matching.score(first.entity, second.entity)
                pipeline, merged_by = verdict(conn, first, second, moved)
                rows.append(_row(first, second, scored, pipeline, merged_by))
    rows.sort(key=lambda r: (-r["score"], r["source_a"], r["external_id_a"], r["source_b"], r["external_id_b"]))
    return rows


def write_candidates(rows: list[dict], path: Path | None = None) -> Path:
    path = path or config.LABEL_CANDIDATES_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in CANDIDATE_COLUMNS})
    return path


def read_pairs(path: Path | None = None) -> list[dict]:
    path = path or config.LABEL_PAIRS_CSV
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def parse_label(value) -> bool | None:
    text = str(value or "").strip().lower()
    if text in YES:
        return True
    if text in NO:
        return False
    return None


# ----------------------------------------------------------------------------- evaluation
def evaluate(conn: sqlite3.Connection, pairs: list[dict]) -> dict:
    moved = lineage_map(conn)
    labelled: list[dict] = []
    unknown_pairs = 0
    for row in pairs:
        label = parse_label(row.get("same_event"))
        if label is None:
            continue
        a = source_entity(conn, row["source_a"], row["external_id_a"])
        b = source_entity(conn, row["source_b"], row["external_id_b"])
        if a is None or b is None:
            unknown_pairs += 1
            continue
        pipeline, merged_by = verdict(conn, a, b, moved)
        labelled.append({**row, "label": label, "pipeline": pipeline, "merged_by": merged_by or ""})
    true_pairs = [r for r in labelled if r["label"]]
    non_pairs = [r for r in labelled if not r["label"]]
    auto = lambda r: r["pipeline"] == "merged" and r["merged_by"] in ("pipeline", "key")  # noqa: E731
    tp = sum(1 for r in true_pairs if auto(r))
    fp = [r for r in non_pairs if auto(r)]
    human_merged = sum(1 for r in true_pairs if r["pipeline"] == "merged" and r["merged_by"] == "human")
    false_merges = [r for r in non_pairs if r["pipeline"] == "merged"]
    proposed = [r for r in true_pairs if r["pipeline"] == "proposed"]
    missed = [r for r in true_pairs if r["pipeline"] in ("separate", "rejected")]
    precision = tp / (tp + len(fp)) if tp + len(fp) else None
    recall = tp / len(true_pairs) if true_pairs else None
    return {
        "labelled": len(labelled),
        "unknown_pairs": unknown_pairs,
        "true_pairs": len(true_pairs),
        "non_pairs": len(non_pairs),
        "auto_true": tp,
        "auto_false": len(fp),
        "human_merged": human_merged,
        "proposed_true": len(proposed),
        "proposed_false": sum(1 for r in non_pairs if r["pipeline"] == "proposed"),
        "precision": precision,
        "recall": recall,
        "false_merges": false_merges,
        "missed": missed,
        "enough_labels": len(true_pairs) >= 20 and len(non_pairs) >= 20,
        "passed": not false_merges and recall is not None and recall >= 0.6 and not missed,
    }


def render_evaluation(result: dict) -> str:
    def pct(value):
        return "n/a" if value is None else f"{100 * value:.1f}%"

    def pair(row):
        return f"{row['source_a']}/{row['external_id_a']} \"{row.get('title_a', '')}\" ~ {row['source_b']}/{row['external_id_b']} \"{row.get('title_b', '')}\" [{row['pipeline']}{(' by ' + row['merged_by']) if row.get('merged_by') else ''}]"

    lines = [
        f"labelled pairs: {result['labelled']} (true: {result['true_pairs']}, non-pairs: {result['non_pairs']}; "
        f"rows whose ids are not in the database: {result['unknown_pairs']})",
        f"automatic merges on labelled pairs: {result['auto_true']} true, {result['auto_false']} false; "
        f"merged by hand: {result['human_merged']}; true pairs waiting as open proposals: {result['proposed_true']}",
        f"precision of the auto-merge rule: {pct(result['precision'])}  recall: {pct(result['recall'])}",
        f"{'PASS' if result['enough_labels'] else 'FAIL'}  at least 20 true pairs and 20 non-pairs labelled",
        f"{'PASS' if not result['false_merges'] else 'FAIL'}  zero false merges ({len(result['false_merges'])})",
        f"{'PASS' if result['recall'] is not None and result['recall'] >= 0.6 else 'FAIL'}  recall at least 60%",
        f"{'PASS' if not result['missed'] else 'FAIL'}  every remaining true pair is merged or an open proposal ({len(result['missed'])} missed)",
    ]
    if result["false_merges"]:
        lines.append("false merges (labelled 'no' but on one event):")
        lines += [f"  {pair(row)}" for row in result["false_merges"]]
    if result["missed"]:
        lines.append("true pairs neither merged nor proposed:")
        lines += [f"  {pair(row)}" for row in result["missed"]]
    lines.append(f"verdict: {'PASS' if result['passed'] and result['enough_labels'] else 'FAIL'}")
    return "\n".join(lines)
