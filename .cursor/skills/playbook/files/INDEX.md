# Playbook index

Standalone helpers. How to add one: `.cursor/skills/playbook/SKILL.md`.

- `validate-column-list.py` — emit SQL that checks a column-name list (JSON or CLI) against a Unity Catalog table's `information_schema.columns`. Added 2026-09-17.
- `rebuild-database.py` — rebuild the SQLite file from the snapshots (data branch + local), optionally collect a source and swap it in with a backup; the way changed identity rules reach old data. Added 2026-09-17.
- `seed-merge-labels.py` — seed data/labels/merge_pairs.csv from merge_candidates.csv with labels that follow from feed facts only (storm names, cited ids, key conflicts); refuses to overwrite without --force. Added 2026-09-17.
- `verify-attach-rebuild.py` — copy the live database, run `eww attach --rebuild` on the copy and diff event_document against the live rows (M3 exit criterion 6); exit 1 on any difference. Added 2026-09-21.
