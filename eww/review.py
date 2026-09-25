"""The Review tab's backend: what it lists and its write actions (accept, reject, revert; and since M3
accept_attachment / reject_attachment for candidate headlines).

The viewer imports eww.api and this module only. Each function opens its own connection when
none is given, performs one action inside one transaction and returns plain values, so app.py
holds no SQL. Accepting a proposal merges the newer event (event_a, created by resolve) into
the older one (event_b) through eww.merge; reverting undoes one lineage row.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

import eww.attach as attach_mod
from eww import config, db
from eww import merge as merge_mod
from eww.clock import now_iso

HUMAN = "human"
RECENT_LIMIT = config.REVIEW_RECENT_MERGES  # how many merges the Review tab lists


@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    own = conn is None
    connection = conn or db.connect()
    try:
        yield connection
    finally:
        if own:
            connection.close()


def _source_ids(conn: sqlite3.Connection, event_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {e: set() for e in event_ids}
    for start in range(0, len(event_ids), 400):
        chunk = event_ids[start : start + 400]
        marks = ", ".join("?" * len(chunk))
        for row in conn.execute(f"SELECT event_id, source_id FROM source_record WHERE event_id IN ({marks})", chunk):
            out[row["event_id"]].add(row["source_id"])
    return {e: sorted(s) for e, s in out.items()}


def _event_summary(row: sqlite3.Row, prefix: str, sources: dict[str, list[str]]) -> dict:
    event_id = row[f"{prefix}_id"]
    return {
        "event_id": event_id,
        "title": row[f"{prefix}_title"],
        "hazard_type": row[f"{prefix}_hazard"],
        "status": row[f"{prefix}_status"],
        "started_at": row[f"{prefix}_started"],
        "last_observed_at": row[f"{prefix}_observed"],
        "severity_label": row[f"{prefix}_severity"],
        "country_iso3": row[f"{prefix}_country"],
        "source_ids": sources.get(event_id, []),
    }


_EVENT_COLUMNS = "{a}.event_id AS {p}_id, {a}.title AS {p}_title, {a}.hazard_type AS {p}_hazard, {a}.status AS {p}_status, {a}.started_at AS {p}_started, {a}.last_observed_at AS {p}_observed, {a}.severity_label AS {p}_severity, {a}.country_iso3 AS {p}_country"


# ----------------------------------------------------------------------------- lists
def open_proposals(conn: sqlite3.Connection | None = None, limit: int = 200) -> list[dict]:
    """Open proposals whose two events are both still live, best score first, with their evidence."""
    with _connection(conn) as c:
        rows = c.execute(
            f"""
            SELECT p.proposal_id, p.score, p.evidence, p.created_at, {_EVENT_COLUMNS.format(a='a', p='a')}, {_EVENT_COLUMNS.format(a='b', p='b')}
            FROM merge_proposal p
            JOIN event a ON a.event_id = p.event_a
            JOIN event b ON b.event_id = p.event_b
            WHERE p.status = 'open' AND a.merged_into_event_id IS NULL AND b.merged_into_event_id IS NULL
            ORDER BY p.score DESC, p.created_at, p.proposal_id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        sources = _source_ids(c, sorted({r["a_id"] for r in rows} | {r["b_id"] for r in rows}))
        out = []
        for row in rows:
            evidence = json.loads(row["evidence"]) if row["evidence"] else {}
            out.append(
                {
                    "proposal_id": row["proposal_id"],
                    "score": row["score"],
                    "created_at": row["created_at"],
                    "distance_km": evidence.get("distance_km"),
                    "days_apart": evidence.get("days_apart"),
                    "text_sim": evidence.get("text_sim"),
                    "rule": evidence.get("rule"),
                    "keys": [tuple(k) for k in evidence.get("keys") or []],
                    "aggregation_radius_km": evidence.get("aggregation_radius_km"),
                    "within_aggregation_radius": evidence.get("within_aggregation_radius"),
                    "evidence": evidence,
                    "event_a": _event_summary(row, "a", sources),
                    "event_b": _event_summary(row, "b", sources),
                }
            )
        return out


def recent_merges(conn: sqlite3.Connection | None = None, limit: int = config.REVIEW_RECENT_MERGES) -> list[dict]:
    """The last merges (action='merge'), newest first, each saying whether it can still be reverted."""
    with _connection(conn) as c:
        rows = c.execute(
            """
            SELECT l.*, f.title AS from_title, f.merged_into_event_id AS from_pointer, t.title AS to_title,
                   t.merged_into_event_id AS to_pointer
            FROM event_lineage l
            JOIN event f ON f.event_id = l.from_event_id
            JOIN event t ON t.event_id = l.to_event_id
            WHERE l.action = 'merge'
            ORDER BY l.performed_at DESC, l.lineage_id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for row in rows:
            reverted = row["reverted_by_lineage_id"] is not None
            evidence = json.loads(row["evidence"]) if row["evidence"] else {}
            out.append(
                {
                    "lineage_id": row["lineage_id"],
                    "from_event_id": row["from_event_id"],
                    "to_event_id": row["to_event_id"],
                    "from_title": row["from_title"],
                    "to_title": row["to_title"],
                    "performed_by": row["performed_by"],
                    "performed_at": row["performed_at"],
                    "score": row["score"],
                    "rule": evidence.get("rule"),
                    "records_moved": len(json.loads(row["moved_source_records"] or "[]")),
                    "reverted": reverted,
                    "reverted_by_lineage_id": row["reverted_by_lineage_id"],
                    "revertable": not reverted and row["from_pointer"] == row["to_event_id"] and row["to_pointer"] is None,
                    "evidence": evidence,
                }
            )
        return out


def counts(conn: sqlite3.Connection | None = None) -> dict:
    with _connection(conn) as c:
        by_status = {row[0]: row[1] for row in c.execute("SELECT status, COUNT(*) FROM merge_proposal GROUP BY 1")}
        merges = c.execute("SELECT COUNT(*) FROM event_lineage WHERE action = 'merge'").fetchone()[0]
        reverted = c.execute("SELECT COUNT(*) FROM event_lineage WHERE action = 'merge' AND reverted_by_lineage_id IS NOT NULL").fetchone()[0]
        attachments = {row[0]: row[1] for row in c.execute("SELECT status, COUNT(*) FROM event_document GROUP BY 1")}
        return {
            "open_proposals": by_status.get("open", 0),
            "accepted_proposals": by_status.get("accepted", 0),
            "rejected_proposals": by_status.get("rejected", 0),
            "merges": merges,
            "merges_reverted": reverted,
            "candidate_attachments": attachments.get("candidate", 0),
            "attached_documents": attachments.get("attached", 0),
            "rejected_documents": attachments.get("rejected", 0),
        }


# ----------------------------------------------------------------------------- actions
def accept(proposal_id: str, conn: sqlite3.Connection | None = None, performed_by: str = HUMAN) -> str:
    """Merge event_a into event_b and mark the proposal accepted. Returns the lineage_id."""
    with _connection(conn) as c:
        row = c.execute("SELECT * FROM merge_proposal WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise merge_mod.MergeError(f"proposal {proposal_id} does not exist")
        if row["status"] != "open":
            raise merge_mod.MergeError(f"proposal {proposal_id} is already {row['status']}")
        evidence = json.loads(row["evidence"]) if row["evidence"] else {}
        with c:
            return merge_mod.merge(c, row["event_a"], row["event_b"], performed_by, score=row["score"], evidence=evidence, proposal_id=proposal_id)


def reject(proposal_id: str, conn: sqlite3.Connection | None = None) -> None:
    """Close the proposal without merging; resolve never proposes the same pair again."""
    with _connection(conn) as c:
        with c:
            cursor = c.execute(
                "UPDATE merge_proposal SET status = 'rejected', decided_at = ? WHERE proposal_id = ? AND status = 'open'",
                (now_iso(), proposal_id),
            )
        if cursor.rowcount == 0:
            raise merge_mod.MergeError(f"proposal {proposal_id} is not open")


def revert(lineage_id: str, conn: sqlite3.Connection | None = None, performed_by: str = HUMAN) -> str:
    """Undo one merge. Returns the new lineage_id (action='revert')."""
    with _connection(conn) as c:
        with c:
            return merge_mod.revert(c, lineage_id, performed_by)


# ----------------------------------------------------------------------------- M3: candidate attachments
def candidate_attachments(conn: sqlite3.Connection | None = None, limit: int = 200) -> list[dict]:
    """Documents scored between the candidate and attach thresholds, best score first, with their evidence."""
    with _connection(conn) as c:
        rows = c.execute(
            """
            SELECT ed.event_id, ed.document_id, ed.score, ed.score_parts, ed.method, ed.decided_at,
                   e.title AS event_title, e.hazard_type, e.country_iso3, e.started_at, e.severity_label,
                   d.title, d.url, d.publisher, d.published_at, d.source_id, d.kind, d.language, d.media_url, d.media_kind
            FROM event_document ed
            JOIN event e ON e.event_id = ed.event_id
            JOIN document d ON d.document_id = ed.document_id
            WHERE ed.status = 'candidate' AND d.removed_at IS NULL
            ORDER BY ed.score DESC, ed.decided_at, ed.document_id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for row in rows:
            parts = json.loads(row["score_parts"]) if row["score_parts"] else {}
            out.append({**dict(row), "parts": parts})
        return out


def accept_attachment(event_id: str, document_id: str, conn: sqlite3.Connection | None = None) -> None:
    """The headline belongs to the event: status 'attached', decided by a person, mention geometries written."""
    with _connection(conn) as c:
        with c:
            attach_mod.human_decide(c, event_id, document_id, "attached")


def reject_attachment(event_id: str, document_id: str, conn: sqlite3.Connection | None = None) -> None:
    """The headline is not about the event: status 'rejected'; the pipeline never re-proposes the pair."""
    with _connection(conn) as c:
        with c:
            attach_mod.human_decide(c, event_id, document_id, "rejected")
