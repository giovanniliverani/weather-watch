"""Write one enrichment document and, when it came from an event's query, the retrieval row.

The retrieval row is what makes attach_document() record method='query' and add the query prior.
"""

from __future__ import annotations

import sqlite3

from eww import documents


def store_document(conn: sqlite3.Connection, *, event_id: str | None = None, **fields) -> documents.UpsertResult:
    result = documents.upsert_document(conn, **fields)
    if event_id:
        documents.record_retrieval(conn, result.document_id, event_id, fields["source_id"], fields.get("fetched_at"))
    return result
