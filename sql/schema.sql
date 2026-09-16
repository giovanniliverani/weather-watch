PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT;

-- ============================================================ provenance
CREATE TABLE source (
  source_id     TEXT PRIMARY KEY,   -- 'gdacs','eonet','reliefweb','gdelt','bluesky','reddit','youtube','user'
  kind          TEXT NOT NULL CHECK (kind IN ('authority','news','social','video','user')),
  display_name  TEXT NOT NULL,
  terms_url     TEXT,
  attribution   TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE collector_run (        -- heartbeat: one row per (run, source), copied from runs/*.jsonl
  run_id        TEXT NOT NULL,      -- ULID minted by the collector
  source_id     TEXT NOT NULL REFERENCES source(source_id),
  scheduled_for TEXT NOT NULL,      -- the 3-hour slot this run served, e.g. 2026-09-16T09:00:00Z
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL CHECK (status IN ('ok','partial','failed')),
  http_status   INTEGER,
  items_seen    INTEGER NOT NULL DEFAULT 0,
  snapshot_path TEXT,               -- path inside the data branch; NULL when the fetch failed
  error         TEXT,
  PRIMARY KEY (run_id, source_id)
) STRICT;
CREATE INDEX collector_run_slot ON collector_run (source_id, scheduled_for);

CREATE TABLE snapshot_ingest (      -- which raw files the laptop has replayed
  snapshot_path TEXT PRIMARY KEY,
  ingested_at   TEXT NOT NULL,
  items_seen    INTEGER NOT NULL,
  items_new     INTEGER NOT NULL,
  items_changed INTEGER NOT NULL
) STRICT;

-- ============================================================ the spine
CREATE TABLE event (
  event_id             TEXT PRIMARY KEY,   -- ULID; never reused, never deleted
  hazard_type          TEXT NOT NULL CHECK (hazard_type IN (
                         'flood','tropical_cyclone','severe_storm','wildfire','heatwave','coldwave',
                         'drought','landslide','volcano','earthquake','tsunami','other')),
  title                TEXT NOT NULL,
  status               TEXT NOT NULL CHECK (status IN ('candidate','active','ended','merged','rejected')),
  started_at           TEXT NOT NULL,
  ended_at             TEXT,
  last_observed_at     TEXT NOT NULL,
  severity_score       REAL CHECK (severity_score BETWEEN 0 AND 1),   -- normalised across sources
  severity_label       TEXT,               -- as the primary source says it: 'Orange', 'M 6.1', '12,000 ha'
  centroid_lat         REAL,               -- denormalised from the primary geometry for cheap bbox filters
  centroid_lon         REAL,
  country_iso3         TEXT,
  glide_number         TEXT,               -- cross-source key when any source supplies one
  summary              TEXT,
  summary_updated_at   TEXT,
  summary_method       TEXT,               -- 'authority', 'llm:qwen2.5-7b', 'llm:claude-sonnet-5'
  merged_into_event_id TEXT REFERENCES event(event_id),   -- soft merge: the row stays, the pointer moves
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  created_by           TEXT NOT NULL DEFAULT 'pipeline',  -- FUTURE: contributor_id
  revision             INTEGER NOT NULL DEFAULT 1         -- FUTURE: optimistic locking for edits
) STRICT;
CREATE UNIQUE INDEX event_glide  ON event (glide_number) WHERE glide_number IS NOT NULL AND merged_into_event_id IS NULL;
CREATE INDEX event_bbox   ON event (centroid_lat, centroid_lon);
CREATE INDEX event_window ON event (hazard_type, status, last_observed_at);

CREATE TABLE source_record (        -- one typed observation from an authoritative feed
  source_record_id TEXT PRIMARY KEY,
  source_id        TEXT NOT NULL REFERENCES source(source_id),
  external_id      TEXT NOT NULL,   -- GDACS eventid, EONET id, ReliefWeb disaster id
  external_episode TEXT NOT NULL DEFAULT '',  -- GDACS episodeid, EONET geometry date; '' when the source has none
  event_id         TEXT REFERENCES event(event_id),   -- NULL only between ingest and resolve
  hazard_type      TEXT NOT NULL,
  title            TEXT,
  observed_at      TEXT NOT NULL,
  started_at       TEXT,
  ended_at         TEXT,
  lat              REAL,
  lon              REAL,
  severity_raw     TEXT,            -- JSON: whatever the source says (alert level, score, magnitude, area)
  glide_number     TEXT,
  payload          TEXT NOT NULL,   -- the raw item, verbatim
  payload_hash     TEXT NOT NULL,
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  UNIQUE (source_id, external_id, external_episode)
) STRICT;
CREATE INDEX source_record_unresolved ON source_record (source_id) WHERE event_id IS NULL;
CREATE INDEX source_record_event      ON source_record (event_id);

-- ============================================================ attachments: news, reports, posts, videos
CREATE TABLE document (
  document_id    TEXT PRIMARY KEY,
  source_id      TEXT NOT NULL REFERENCES source(source_id),
  kind           TEXT NOT NULL CHECK (kind IN ('article','report','post','video')),
  url            TEXT NOT NULL,
  url_canonical  TEXT NOT NULL,     -- lower-cased host, https, tracking parameters stripped, no fragment
  external_id    TEXT,              -- AT-proto URI, Reddit id, YouTube id, ReliefWeb report id
  title          TEXT,
  text_excerpt   TEXT CHECK (length(text_excerpt) <= 2000),   -- never a full article body
  language       TEXT,
  author         TEXT,
  publisher      TEXT,              -- domain or handle
  published_at   TEXT,
  fetched_at     TEXT NOT NULL,
  media_url      TEXT,              -- REFERENCE ONLY: og:image, post image, video thumbnail
  media_kind     TEXT CHECK (media_kind IN ('image','video','none')),
  payload        TEXT,              -- the raw API item, verbatim
  removed_at     TEXT,              -- set when the source reports a deletion; hidden everywhere after that
  UNIQUE (source_id, url_canonical)
) STRICT;
CREATE INDEX document_published ON document (published_at);
CREATE INDEX document_external  ON document (source_id, external_id);

CREATE TABLE event_geometry (       -- many per event, typed. "Multiple pins per event" live here.
  geometry_id      TEXT PRIMARY KEY,
  event_id         TEXT NOT NULL REFERENCES event(event_id),
  role             TEXT NOT NULL CHECK (role IN ('centroid','footprint','track','impact_area','mention')),
  geojson          TEXT NOT NULL,   -- a GeoJSON geometry object
  min_lat REAL NOT NULL, min_lon REAL NOT NULL, max_lat REAL NOT NULL, max_lon REAL NOT NULL,
  precision        TEXT NOT NULL CHECK (precision IN ('exact','street','city','admin1','country','unresolved')),
  observed_at      TEXT,
  source_id        TEXT REFERENCES source(source_id),
  source_record_id TEXT REFERENCES source_record(source_record_id),
  document_id      TEXT REFERENCES document(document_id),   -- set for role = 'mention'
  is_primary       INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1))
) STRICT;
CREATE INDEX        event_geometry_event   ON event_geometry (event_id, role);
CREATE UNIQUE INDEX event_geometry_primary ON event_geometry (event_id) WHERE is_primary = 1;

CREATE TABLE document_extraction (
  document_id       TEXT PRIMARY KEY REFERENCES document(document_id),
  method            TEXT NOT NULL,  -- 'lexicon+ner', 'llm:qwen2.5-7b', 'llm:claude-sonnet-5'
  model_version     TEXT,
  hazard_type       TEXT,
  hazard_confidence REAL,
  places            TEXT NOT NULL DEFAULT '[]',   -- JSON [{name, country_hint, lat, lon, precision, provider}]
  event_date        TEXT,
  figures           TEXT,           -- JSON {dead, injured, missing, displaced, evidence_span}
  extracted_at      TEXT NOT NULL,
  cost_usd          REAL NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE document_embedding (
  document_id TEXT PRIMARY KEY REFERENCES document(document_id),
  model       TEXT NOT NULL,        -- 'paraphrase-multilingual-MiniLM-L12-v2'
  dim         INTEGER NOT NULL,
  vector      BLOB NOT NULL         -- float32 little-endian; brute-force cosine in numpy at v1 scale
) STRICT;

CREATE TABLE event_document (
  event_id    TEXT NOT NULL REFERENCES event(event_id),
  document_id TEXT NOT NULL REFERENCES document(document_id),
  status      TEXT NOT NULL CHECK (status IN ('attached','candidate','rejected')),
  score       REAL,
  score_parts TEXT,                 -- JSON {spatial, temporal, text, hazard}
  method      TEXT NOT NULL,        -- 'rule', 'embedding', 'query' (retrieved for this event), 'human'
  decided_at  TEXT NOT NULL,
  decided_by  TEXT NOT NULL DEFAULT 'pipeline',   -- FUTURE: contributor_id
  PRIMARY KEY (event_id, document_id)
) STRICT;
CREATE INDEX event_document_by_doc ON event_document (document_id);
CREATE INDEX event_document_review ON event_document (status) WHERE status = 'candidate';

-- ============================================================ identity changes, always reversible
CREATE TABLE merge_proposal (       -- the review queue for cross-source identity
  proposal_id TEXT PRIMARY KEY,
  event_a     TEXT NOT NULL REFERENCES event(event_id),
  event_b     TEXT NOT NULL REFERENCES event(event_id),
  score       REAL NOT NULL,
  evidence    TEXT NOT NULL,        -- JSON: distance_km, days_apart, name_match, glide_match, text_sim
  status      TEXT NOT NULL CHECK (status IN ('open','accepted','rejected')),
  created_at  TEXT NOT NULL,
  decided_at  TEXT,
  UNIQUE (event_a, event_b)
) STRICT;

CREATE TABLE event_lineage (
  lineage_id             TEXT PRIMARY KEY,
  action                 TEXT NOT NULL CHECK (action IN ('merge','split','reassign','revert')),
  from_event_id          TEXT NOT NULL REFERENCES event(event_id),
  to_event_id            TEXT NOT NULL REFERENCES event(event_id),
  moved_source_records   TEXT NOT NULL DEFAULT '[]',  -- JSON list of source_record_id, so a revert is exact
  moved_documents        TEXT NOT NULL DEFAULT '[]',
  score                  REAL,
  evidence               TEXT,
  performed_by           TEXT NOT NULL,   -- 'pipeline' | 'human'   (FUTURE: contributor_id)
  performed_at           TEXT NOT NULL,
  reverted_by_lineage_id TEXT REFERENCES event_lineage(lineage_id)
) STRICT;

-- ============================================================ geocoding
CREATE TABLE gazetteer_place (      -- GeoNames cities500 + admin1 + country rows, loaded once (CC BY 4.0)
  geonameid      INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  asciiname      TEXT NOT NULL,
  alternatenames TEXT,              -- comma-separated, as shipped by GeoNames
  lat            REAL NOT NULL,
  lon            REAL NOT NULL,
  feature_class  TEXT NOT NULL,
  feature_code   TEXT NOT NULL,
  country_iso2   TEXT NOT NULL,
  admin1_code    TEXT,
  population     INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX gazetteer_ascii ON gazetteer_place (asciiname, country_iso2);
CREATE VIRTUAL TABLE gazetteer_fts USING fts5(name, asciiname, alternatenames,
  content='gazetteer_place', content_rowid='geonameid');   -- run the 'rebuild' command once after loading

CREATE TABLE geocode_cache (
  query_norm   TEXT NOT NULL,       -- lower-cased, diacritics folded, whitespace collapsed
  country_hint TEXT NOT NULL DEFAULT '',
  provider     TEXT NOT NULL,       -- 'gazetteer' | 'nominatim'
  lat          REAL,                -- NULL together with lon = provider found nothing (negative cache)
  lon          REAL,
  precision    TEXT NOT NULL,
  display_name TEXT,
  bbox         TEXT,                -- JSON [min_lon, min_lat, max_lon, max_lat]
  resolved_at  TEXT NOT NULL,
  PRIMARY KEY (query_norm, country_hint, provider)
) STRICT;

-- ============================================================ spend ledger
CREATE TABLE llm_call (
  call_id       TEXT PRIMARY KEY,
  purpose       TEXT NOT NULL CHECK (purpose IN ('extract','summarise','other')),
  backend       TEXT NOT NULL,      -- 'ollama' | 'anthropic'
  model         TEXT NOT NULL,
  input_tokens  INTEGER NOT NULL,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL,
  cost_usd      REAL NOT NULL,      -- 0 for ollama
  document_id   TEXT REFERENCES document(document_id),
  event_id      TEXT REFERENCES event(event_id),
  called_at     TEXT NOT NULL
) STRICT;
CREATE INDEX llm_call_month ON llm_call (called_at);

-- ============================================================ FUTURE: contributions (created now, empty until v2)
CREATE TABLE contributor (
  contributor_id TEXT PRIMARY KEY,
  handle         TEXT NOT NULL UNIQUE,
  role           TEXT NOT NULL CHECK (role IN ('owner','editor','contributor')),
  created_at     TEXT NOT NULL
) STRICT;

CREATE TABLE contribution (         -- anything a person submits, held until reviewed
  contribution_id TEXT PRIMARY KEY,
  contributor_id  TEXT NOT NULL REFERENCES contributor(contributor_id),
  event_id        TEXT REFERENCES event(event_id),   -- NULL = proposes a new event
  kind            TEXT NOT NULL CHECK (kind IN ('new_event','document','geometry','field_edit','merge','split')),
  payload         TEXT NOT NULL,    -- JSON shaped like the target row(s)
  status          TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected')),
  submitted_at    TEXT NOT NULL,
  reviewed_at     TEXT,
  reviewed_by     TEXT REFERENCES contributor(contributor_id)
) STRICT;

CREATE TABLE event_revision (       -- field-level edit history; the pipeline may write it too
  revision_id     TEXT PRIMARY KEY,
  event_id        TEXT NOT NULL REFERENCES event(event_id),
  revision        INTEGER NOT NULL,
  field           TEXT NOT NULL,
  old_value       TEXT,
  new_value       TEXT,
  changed_by      TEXT NOT NULL,
  changed_at      TEXT NOT NULL,
  contribution_id TEXT REFERENCES contribution(contribution_id),
  UNIQUE (event_id, revision, field)
) STRICT;

-- ============================================================ heartbeat (schema version 2)
-- Every 3-hour slot from the first collector run to now, and whether an 'ok' run started inside the
-- slot's 45-minute grace window (docs/architecture.md §4, M1). Plain SQL apart from strftime/printf.
CREATE VIEW heartbeat AS
WITH RECURSIVE
  bounds AS (
    SELECT substr(MIN(started_at), 1, 11)
             || printf('%02d', (CAST(substr(MIN(started_at), 12, 2) AS INTEGER) / 3) * 3) || ':00:00Z' AS first_slot,
           strftime('%Y-%m-%dT', 'now')
             || printf('%02d', (CAST(strftime('%H', 'now') AS INTEGER) / 3) * 3) || ':00:00Z' AS last_slot
    FROM collector_run
  ),
  slots(scheduled_for) AS (
    SELECT first_slot FROM bounds WHERE first_slot IS NOT NULL
    UNION ALL
    SELECT strftime('%Y-%m-%dT%H:%M:%SZ', scheduled_for, '+3 hours') FROM slots
    WHERE scheduled_for < (SELECT last_slot FROM bounds)
  )
SELECT s.scheduled_for,
       strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes') AS deadline,
       (SELECT COUNT(*) FROM collector_run r
         WHERE r.status = 'ok' AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS ok_runs,
       (SELECT COUNT(DISTINCT r.source_id) FROM collector_run r
         WHERE r.status = 'ok' AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS sources_ok,
       EXISTS (SELECT 1 FROM collector_run r
         WHERE r.status = 'ok' AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS served
FROM slots s;
