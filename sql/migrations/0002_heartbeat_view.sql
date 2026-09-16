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
