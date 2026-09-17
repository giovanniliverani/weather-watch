---
name: playbook
description: Save and reuse standalone helper scripts, SQL, and templates that would otherwise live only in chat. Use when creating a one-off .py/.sql/.ps1/checklist that will be needed again, when the user mentions the playbook or toolbox, or at the end of a task if a helper was written that is not product code and is not already in a job skill.
---

# Playbook

Repo toolbox for **helpers that are not a full skill and not product code**.
Canonical files live in this skill's `files/` folder (same path under
`.claude/skills/playbook/files/` when mirrored).

## When to save

Save if **all** of these are true:

- You wrote a script, SQL snippet, checklist, or template in this session
- It is likely to be run again (same task next week, or a parameterized variant)
- It is **not** already owned by a job skill (`references/` / `scripts/` there)
- It is **not** application/model code under `databricks/delta_live_tables/`, a
  service package, Terraform, etc.

## When not to save

- One-off query *results*, Excel workbooks, Desktop run folders
- Secrets, tokens, passwords, connection strings, personal IPs
- Drafts the user has not approved
- Throwaway debug that only makes sense with this conversation's data
- Anything that already belongs in a job skill (e.g. ECC mapping registry)

If unsure, ask one line: "Save this to the playbook as `[name]`?"

## Where and how

1. Write `.cursor/skills/playbook/files/[descriptive-name].[ext]`
   - kebab-case, no dates, no version numbers (`validate-column-list.py`, not
     `validate_v2_2026-09-17.py`)
   - If `.claude/skills/playbook/files/` exists, write the **same** file there
     so Claude ↔ Cursor stay aligned
2. Scripts: take inputs via argparse / env. Do not hardcode catalog, table,
   model, or employee names.
3. Prose / SQL / email templates: `[PLACEHOLDERS]` in square brackets, then
   **one filled example** underneath (comment or fenced block).
4. Append **one line** to `files/INDEX.md`:

   ```text
   - `[filename]` — [what it is for]. Added YYYY-MM-DD.
   ```

5. Point at it from a job skill only if that job's workflow should invoke it.
   Do not copy the file into the job skill.

Do **not** re-run the user's original task after filing. Saving is the finish.

## First check before writing

Read `files/INDEX.md`. Reuse or extend an existing file instead of adding a
near-duplicate.

## Example

Bad (lost in chat, hardcoded):

```python
cols = ["ke30_id", "revenue"]
# compare to main.gold.fct_gross_profit somehow
```

Good: `files/validate-column-list.py` with `--table` and `--json` / `--model`.
See that file's docstring for the filled ECC example.
