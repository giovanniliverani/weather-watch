-- ============================================================ summary evidence (schema version 4, M4)
-- The prose lives in event.summary. This column keeps the sentences, their evidence spans, the figures
-- and the document ids that went into the summary, so eww doctor can re-check every number and the next
-- run can see which attached documents were already part of it.
ALTER TABLE event ADD COLUMN summary_evidence TEXT;
