-- Heartbeat counts spine collectors only (schema version 5, M5).
-- YouTube writes one collector_run row per search.list call so the 100-search UTC-day cap
-- survives a restart. Those rows must not mark a 3-hour spine slot as served.
DROP VIEW IF EXISTS heartbeat;
CREATE VIEW heartbeat AS
WITH RECURSIVE
  bounds AS (
    SELECT substr(MIN(started_at), 1, 11)
             || printf('%02d', (CAST(substr(MIN(started_at), 12, 2) AS INTEGER) / 3) * 3) || ':00:00Z' AS first_slot,
           strftime('%Y-%m-%dT', 'now')
             || printf('%02d', (CAST(strftime('%H', 'now') AS INTEGER) / 3) * 3) || ':00:00Z' AS last_slot
    FROM collector_run
    WHERE source_id IN ('gdacs', 'eonet', 'copernicus')
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
         WHERE r.status = 'ok' AND r.source_id IN ('gdacs', 'eonet', 'copernicus') AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS ok_runs,
       (SELECT COUNT(DISTINCT r.source_id) FROM collector_run r
         WHERE r.status = 'ok' AND r.source_id IN ('gdacs', 'eonet', 'copernicus') AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS sources_ok,
       EXISTS (SELECT 1 FROM collector_run r
         WHERE r.status = 'ok' AND r.source_id IN ('gdacs', 'eonet', 'copernicus') AND r.started_at >= s.scheduled_for
           AND r.started_at < strftime('%Y-%m-%dT%H:%M:%SZ', s.scheduled_for, '+45 minutes')) AS served
FROM slots s;
