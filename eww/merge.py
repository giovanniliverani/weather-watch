"""Identity changes: merge, revert, propose. Every one of them is a pointer move plus a lineage row.

merge(from, to) moves the source_record rows (and attached documents) of `from` onto `to`, marks
`from` as merged with `merged_into_event_id`, records the exact ids it moved in `event_lineage`, and
recomputes `to`. revert(lineage_id) is the inverse computed only from that lineage row, and writes a
second lineage row with action='revert'. No event row is ever deleted or rewritten beyond its
status, pointer and derived columns. Callers own the transaction (`with conn:`).
"""

from __future__ import annotations

import json
import logging
import sqlite3

from eww import events
from eww.clock import now_iso
from eww.ids import new_id

log = logging.getLogger(__name__)

LINEAGE_COLUMNS = (
    "lineage_id", "action", "from_event_id", "to_event_id", "moved_source_records", "moved_documents",
    "score", "evidence", "performed_by", "performed_at",
)


class MergeError(ValueError):
    """The requested identity change is not possible in the database's current state."""


def _event(conn: sqlite3.Connection, event_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM event WHERE event_id = ?", (event_id,)).fetchone()
    if row is None:
        raise MergeError(f"event {event_id} does not exist")
    return row


def _chunks(items: list, size: int = 400):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _insert_lineage(conn: sqlite3.Connection, values: dict) -> None:
    conn.execute(
        f"INSERT INTO event_lineage ({', '.join(LINEAGE_COLUMNS)}) VALUES ({', '.join(':' + c for c in LINEAGE_COLUMNS)})",
        values,
    )


# ----------------------------------------------------------------------------- merge
def merge(
    conn: sqlite3.Connection,
    from_event_id: str,
    to_event_id: str,
    performed_by: str,
    *,
    score: float | None = None,
    evidence: dict | None = None,
    proposal_id: str | None = None,
) -> str:
    """Fold `from_event_id` into the live event behind `to_event_id`. Returns the lineage_id."""
    to_event_id = events.canonical_event_id(conn, to_event_id)
    source = _event(conn, from_event_id)
    _event(conn, to_event_id)
    if from_event_id == to_event_id:
        raise MergeError("an event cannot be merged into itself")
    if source["merged_into_event_id"] is not None:
        raise MergeError(f"event {from_event_id} is already merged into {source['merged_into_event_id']}")
    now = now_iso()
    moved_records = [row[0] for row in conn.execute("SELECT source_record_id FROM source_record WHERE event_id = ? ORDER BY 1", (from_event_id,))]
    conn.execute("UPDATE source_record SET event_id = ? WHERE event_id = ?", (to_event_id, from_event_id))
    moved_documents = [
        row[0]
        for row in conn.execute(
            """
            SELECT ed.document_id FROM event_document ed WHERE ed.event_id = ?
              AND NOT EXISTS (SELECT 1 FROM event_document x WHERE x.event_id = ? AND x.document_id = ed.document_id)
            ORDER BY 1
            """,
            (from_event_id, to_event_id),
        )
    ]
    for chunk in _chunks(moved_documents):
        marks = ", ".join("?" * len(chunk))
        conn.execute(f"UPDATE event_document SET event_id = ? WHERE event_id = ? AND document_id IN ({marks})", (to_event_id, from_event_id, *chunk))
    conn.execute(
        "UPDATE event SET status = 'merged', merged_into_event_id = ?, updated_at = ? WHERE event_id = ?",
        (to_event_id, now, from_event_id),
    )
    payload = dict(evidence or {})
    if proposal_id:
        payload["proposal_id"] = proposal_id
    lineage_id = new_id()
    _insert_lineage(
        conn,
        {
            "lineage_id": lineage_id,
            "action": "merge",
            "from_event_id": from_event_id,
            "to_event_id": to_event_id,
            "moved_source_records": json.dumps(moved_records),
            "moved_documents": json.dumps(moved_documents),
            "score": score,
            "evidence": json.dumps(payload, ensure_ascii=False) if payload else None,
            "performed_by": performed_by,
            "performed_at": now,
        },
    )
    conn.execute(
        """
        UPDATE merge_proposal SET status = 'accepted', decided_at = ?
        WHERE status = 'open' AND (proposal_id = ? OR (event_a = ? AND event_b = ?) OR (event_a = ? AND event_b = ?))
        """,
        (now, proposal_id or "", from_event_id, to_event_id, to_event_id, from_event_id),
    )
    events.refresh_event(conn, to_event_id)
    log.info("merge from=%s to=%s by=%s records=%d documents=%d score=%s lineage=%s", from_event_id, to_event_id, performed_by, len(moved_records), len(moved_documents), score, lineage_id)
    return lineage_id


# ----------------------------------------------------------------------------- revert
def revert(conn: sqlite3.Connection, lineage_id: str, performed_by: str) -> str:
    """Undo one merge using only what its lineage row recorded. Returns the new lineage_id (action='revert')."""
    row = conn.execute("SELECT * FROM event_lineage WHERE lineage_id = ?", (lineage_id,)).fetchone()
    if row is None:
        raise MergeError(f"lineage row {lineage_id} does not exist")
    if row["action"] != "merge":
        raise MergeError(f"lineage row {lineage_id} is a {row['action']!r}, only merges can be reverted")
    if row["reverted_by_lineage_id"] is not None:
        raise MergeError(f"merge {lineage_id} was already reverted by {row['reverted_by_lineage_id']}")
    from_event_id, to_event_id = row["from_event_id"], row["to_event_id"]
    source, target = _event(conn, from_event_id), _event(conn, to_event_id)
    if source["merged_into_event_id"] != to_event_id:
        raise MergeError(f"event {from_event_id} no longer points at {to_event_id}")
    if target["merged_into_event_id"] is not None:
        raise MergeError(f"event {to_event_id} was merged into {target['merged_into_event_id']} since; revert that merge first")
    now = now_iso()
    moved_records = json.loads(row["moved_source_records"] or "[]")
    moved_documents = json.loads(row["moved_documents"] or "[]")
    for chunk in _chunks(moved_records):
        marks = ", ".join("?" * len(chunk))
        conn.execute(f"UPDATE source_record SET event_id = ? WHERE event_id = ? AND source_record_id IN ({marks})", (from_event_id, to_event_id, *chunk))
    for chunk in _chunks(moved_documents):
        marks = ", ".join("?" * len(chunk))
        conn.execute(f"UPDATE event_document SET event_id = ? WHERE event_id = ? AND document_id IN ({marks})", (from_event_id, to_event_id, *chunk))
    conn.execute(
        "UPDATE event SET status = 'active', merged_into_event_id = NULL, updated_at = ? WHERE event_id = ?",
        (now, from_event_id),
    )
    new_lineage_id = new_id()
    previous = json.loads(row["evidence"]) if row["evidence"] else {}
    _insert_lineage(
        conn,
        {
            "lineage_id": new_lineage_id,
            "action": "revert",
            "from_event_id": to_event_id,
            "to_event_id": from_event_id,
            "moved_source_records": row["moved_source_records"],
            "moved_documents": row["moved_documents"],
            "score": row["score"],
            "evidence": json.dumps({"reverts": lineage_id, "merged_by": row["performed_by"], "merged_at": row["performed_at"], **({"proposal_id": previous["proposal_id"]} if previous.get("proposal_id") else {})}),
            "performed_by": performed_by,
            "performed_at": now,
        },
    )
    conn.execute("UPDATE event_lineage SET reverted_by_lineage_id = ? WHERE lineage_id = ?", (new_lineage_id, lineage_id))
    # undo restores the queue as well: a proposal this merge accepted is open again for a fresh decision
    conn.execute(
        """
        UPDATE merge_proposal SET status = 'open', decided_at = NULL
        WHERE status = 'accepted' AND (proposal_id = ? OR (event_a = ? AND event_b = ?) OR (event_a = ? AND event_b = ?))
        """,
        (previous.get("proposal_id") or "", from_event_id, to_event_id, to_event_id, from_event_id),
    )
    events.refresh_event(conn, from_event_id)
    events.refresh_event(conn, to_event_id)
    log.info("revert lineage=%s from=%s to=%s by=%s records=%d new_lineage=%s", lineage_id, to_event_id, from_event_id, performed_by, len(moved_records), new_lineage_id)
    return new_lineage_id


def pair_reverted_by_human(conn: sqlite3.Connection, event_a: str, event_b: str) -> bool:
    """True when a person has undone a merge between these two events: the pipeline never re-merges them."""
    return (
        conn.execute(
            """
            SELECT 1 FROM event_lineage WHERE action = 'revert' AND performed_by = 'human'
              AND ((from_event_id = ? AND to_event_id = ?) OR (from_event_id = ? AND to_event_id = ?)) LIMIT 1
            """,
            (event_a, event_b, event_b, event_a),
        ).fetchone()
        is not None
    )


# ----------------------------------------------------------------------------- proposals
def find_proposal(conn: sqlite3.Connection, event_a: str, event_b: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM merge_proposal WHERE (event_a = ? AND event_b = ?) OR (event_a = ? AND event_b = ?) LIMIT 1",
        (event_a, event_b, event_b, event_a),
    ).fetchone()


def propose(conn: sqlite3.Connection, event_a: str, event_b: str, score: float, evidence: dict) -> str | None:
    """Write an open merge_proposal for the pair; None when one exists already, in any state or order."""
    if event_a == event_b or find_proposal(conn, event_a, event_b) is not None:
        return None
    proposal_id = new_id()
    conn.execute(
        """
        INSERT INTO merge_proposal (proposal_id, event_a, event_b, score, evidence, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'open', ?)
        """,
        (proposal_id, event_a, event_b, float(score), json.dumps(evidence, ensure_ascii=False), now_iso()),
    )
    log.info("proposal %s: %s ~ %s score=%.3f", proposal_id, event_a, event_b, score)
    return proposal_id
