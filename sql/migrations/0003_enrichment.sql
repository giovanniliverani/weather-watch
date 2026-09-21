-- ============================================================ enrichment provenance (schema version 3, M3)
-- Per (event, source): when the laptop last queried an enrichment provider for an event, so the next
-- query starts where the last successful one ended (docs/architecture.md §1, "the laptop problem").
CREATE TABLE enrichment_run (
  event_id        TEXT NOT NULL REFERENCES event(event_id),
  source_id       TEXT NOT NULL REFERENCES source(source_id),
  last_success_at TEXT NOT NULL,    -- the end of the last successful query window; the next startdatetime
  queried_at      TEXT NOT NULL,
  query           TEXT,             -- the query string sent, for eyeballing
  items_seen      INTEGER NOT NULL DEFAULT 0,
  documents_new   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (event_id, source_id)
) STRICT;

-- Which per-event query fetched a document: the evidence behind event_document.method = 'query' and the
-- "+0.10 query prior" of attach_document(). One document may have been retrieved for several events.
CREATE TABLE document_retrieval (
  document_id  TEXT NOT NULL REFERENCES document(document_id),
  event_id     TEXT NOT NULL REFERENCES event(event_id),
  source_id    TEXT NOT NULL REFERENCES source(source_id),
  retrieved_at TEXT NOT NULL,
  PRIMARY KEY (document_id, event_id)
) STRICT;
CREATE INDEX document_retrieval_event ON document_retrieval (event_id);
