---
name: sync-claude-to-cursor
description: >
  Discovers every .claude folder in the monorepo and plans mirroring its
  harness (skills, rules, hooks, agents, commands) into a sibling .cursor
  folder at the same level. Always dry-runs first, reports conflicts with
  mtimes/fresher verdicts, and asks the user before any copy. Use when the
  user wants to sync Claude → Cursor, mirror .claude into .cursor, copy
  skills/rules/hooks across tools, or keep Cursor harnesses aligned with
  Claude Code folders.
disable-model-invocation: true
---

# Sync Claude → Cursor

Mirror Claude Code harness trees into Cursor project harness trees **at the
same directory level**:

```text
<path>/.claude/...  ->  <path>/.cursor/...
```

Examples in this repo:

- `.claude/` -> `.cursor/`
- `databricks/.claude/` -> `databricks/.cursor/`
- `databricks/delta_live_tables/.claude/` -> `databricks/delta_live_tables/.cursor/`

## Hard rules

1. **Never copy until the user confirms** the plan (especially conflicts).
2. **Always run the planner first** (no `--apply`).
3. Present **conflicts prominently** with mtimes and which side looks fresher.
4. Default: mirror `skills`, `rules`, `hooks`, `agents`, `commands` only.
   Do **not** copy `settings.json` / `settings.local.json` unless the user
   explicitly asks (`--include-settings`) — formats often differ by tool.
5. Do not invent a manual file-by-file walk when the script can plan it.

## Workflow

Copy this checklist and track it:

```text
Sync progress:
- [ ] 1. Dry-run plan (script, no writes)
- [ ] 2. Summarize findings for the user
- [ ] 3. Get explicit confirmation (and conflict policy)
- [ ] 4. Apply only what was approved
- [ ] 5. Re-run plan to verify
```

### 1. Dry-run plan

From the **repo root**. Prefer `uv run python` in this monorepo (plain `python`
may be missing on Windows):

```bash
uv run python .cursor/skills/sync-claude-to-cursor/scripts/sync_claude_to_cursor.py
```

For machine-readable detail:

```bash
uv run python .cursor/skills/sync-claude-to-cursor/scripts/sync_claude_to_cursor.py --json
```

The script walks the repo (skips `.git`, `node_modules`, venvs, `.cursor`,
`__pycache__` / `.pyc`, etc.), finds every `.claude` directory, and classifies
each candidate file:

| Action | Meaning |
|--------|---------|
| `copy_new` | Missing under sibling `.cursor` — safe once approved |
| `conflict` | Same relative path exists in both; content differs |
| `identical` | Same sha256 — no copy needed |
| `skip_settings` | Settings file seen but omitted (default) |

### 2. Summarize for the user

Show:

1. Which `.claude` dirs were found and their target `.cursor` paths
2. Counts: new / conflict / identical / skipped settings
3. **Conflicts first**, each with:
   - relative path (e.g. `skills/teach-me/SKILL.md`)
   - Claude mtime + size
   - Cursor mtime + size
   - `fresher=claude|cursor|same` and a one-line verdict
4. New files as a shorter list (group by `.claude` root)

Do not bury conflicts. If Cursor looks fresher, say clearly that overwriting
may discard Cursor-only edits.

### 3. Ask before copying

Ask the user to choose, at minimum:

- Proceed with **new files only**?
- For each conflict (or as a batch): **keep Cursor** / **overwrite from Claude** / **skip**?
- Include **settings** files? (default no)

Do not apply until they answer. If they say “sync everything” without
addressing conflicts, list conflicts again and get a yes/no on overwrites.

### 4. Apply (only after confirmation)

**New files only** (skips conflicts):

```bash
uv run python .cursor/skills/sync-claude-to-cursor/scripts/sync_claude_to_cursor.py --apply
```

**New files + overwrite conflicts** (only if user approved overwrites):

```bash
uv run python .cursor/skills/sync-claude-to-cursor/scripts/sync_claude_to_cursor.py --apply --overwrite-conflicts
```

**Also settings** (only if user asked):

```bash
uv run python .cursor/skills/sync-claude-to-cursor/scripts/sync_claude_to_cursor.py --apply --include-settings
# add --overwrite-conflicts if they also approved conflict overwrites
```

Optional: limit mirrored top-level dirs:

```bash
uv run python .cursor/skills/sync-claude-to-cursor/scripts/sync_claude_to_cursor.py --dirs skills rules
```

### 5. Verify

Re-run the dry-run plan. Expect approved paths to be `identical` (or gone
from `copy_new`). Report any remaining conflicts.

## Agent presentation template

Use this shape when reporting the plan:

```markdown
## Claude → Cursor sync plan

**Found:** N `.claude` trees

| Source | Target |
|--------|--------|
| `.claude` | `.cursor` |
| `databricks/.claude` | `databricks/.cursor` |

### Conflicts (need your call)
- `skills/foo/SKILL.md` — Claude mtime … / Cursor mtime … — **fresher: …**
  - Recommendation: …

### New (safe after you say go)
- …

### Identical
- count only, unless they ask for the list

### Settings (skipped by default)
- …

**How should I proceed?**
1. Copy new only
2. Copy new + overwrite conflicts from Claude
3. Cancel / copy a subset (list paths)
```

## Notes

- Script path: [scripts/sync_claude_to_cursor.py](scripts/sync_claude_to_cursor.py)
- Default root is inferred as the monorepo root (four levels above the script).
  Override with `--root` if needed.
- This skill lives only under `.cursor` on purpose; it is the Cursor-side
  operator for harness mirroring.
