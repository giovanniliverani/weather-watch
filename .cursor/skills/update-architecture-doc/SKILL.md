---
name: update-architecture-doc
description: Updates docs/architecture.md so the plan matches reality after code changes, finished milestones, or decisions made in conversation. Use this whenever something has moved on from what the document says — a milestone's exit criteria were met or failed, a provider or library was swapped, an API's terms, limits or prices changed, the schema or the GeoJSON contract changed, a source was added or dropped, a cost estimate was replaced by a measured number, or a decision was made in chat that the document does not record yet. Also use when the user says the architecture doc is stale, asks to sync the plan with the code, wants the doc updated after a diff or a commit, or runs /update-architecture-doc.
---

# Update the architecture document

`docs/architecture.md` is a decision record the user builds from, not a wiki page. Its value comes from three properties: every choice is decided rather than surveyed, every milestone has exit criteria the user can check alone, and every external fact is either verified against an official page or explicitly marked **verify**. Your job is to keep those properties true as reality moves, which means targeted edits plus a consistency sweep — never a regeneration.

Two facts about the document shape everything below.

**It is long and provenance-carrying.** Roughly 18,000 words across nine sections written under a fixed contract (`docs/planning-prompt.md` holds the original rules). Facts in it were checked against official pages on a stated date. If you rewrite prose wholesale, you silently destroy the distinction between "checked" and "assumed", and nobody can tell afterwards which is which.

**It is densely cross-referenced.** One real-world change usually lands in three to six sections. Dropping a data source, for example, touches the decisions table, the cost table, the architecture diagram, a milestone's scope, the Not-in-v1 list and that milestone's implementation prompt. A half-applied change leaves the document contradicting itself, which is worse than leaving it out of date, because the user builds from it.

## Step 1 — Establish what actually changed

Gather evidence before opening the document. Run these and read what comes back:

```bash
git status --short
git log --oneline -10
git diff HEAD~1 --stat
git diff            # unstaged work, often where the newest reality lives
```

Then scan the conversation for anything the repository cannot tell you: decisions the user made out loud, numbers they read off a run, terms they checked on a vendor's page, frustrations that amount to a changed constraint.

Sort what you find into four classes, because they carry different authority:

| Class | Example | Authority |
|---|---|---|
| Code reality | `eww/collectors/gdacs.py` now pages through the JSON API | Wins over the document. If they disagree, the document is stale — unless the code is a mistake, in which case say so rather than documenting the bug |
| Measured result | The M0 density report counted 62 events across 5 hazard types | Replaces an estimate. Always record the number, not just the verdict |
| External fact | An API's free tier changed, a licence now bars automated use | Only as good as its source. Verified on an official page today → state it. Heard, recalled or inferred → state it and mark **verify** |
| Decision in chat | "Use DuckDB instead of SQLite" | Authoritative, but needs a reason and a falsifier before it enters the decisions table |

Write the change list down in a few lines before editing. If nothing on it contradicts the document, say so and stop. A no-op is a good outcome and costs the user nothing.

## Step 2 — Find every place the change lives

Never edit from memory of the document's structure. Grep for the entity first, so you see all of its homes at once:

```bash
grep -n "GDELT" docs/architecture.md        # or the provider, table, milestone, number
grep -n "^## \|^### " docs/architecture.md  # current section and milestone map
```

Then use this map to decide where an edit is owed. It is a checklist, not a suggestion: the failure mode this skill exists to prevent is updating one section and leaving five stale.

| What happened | Sections that must change |
|---|---|
| A decision changed (tool, library, provider, approach) | §1 row: choice, the ≤3-sentence why, and what would change my mind. Every downstream mention found by grep. §5 if the old choice is now deferred rather than dead |
| A data source was added or dropped | §1 source rows, §1b cost table, §2 diagram and component lines, §3 `source` seed values, §4 the owning milestone's scope and exit criteria, §5 with a one-line reason if dropped, §6 that milestone's prompt, §7 if it needs an account or a check |
| A price, free tier or volume changed | §1b row and the total, §1 rows that depend on the budget, §4 M4's cap criterion if the cap moved, §0 if the headline cost claim changed |
| The schema changed | §3 DDL, the "where event identity is enforced" list, the FUTURE markers, §2 if a component's responsibility moved, §6 the prompt of the milestone that creates those tables |
| The GeoJSON contract changed | §2 contract block and the "what you must not do" list, §6 prompts that consume it, and say plainly in your report that this is the one interface a future frontend depends on |
| A milestone finished | §4 mark it done with the measured numbers against each exit criterion, §7 close any question it answered, §0 only if it tested the central bet |
| An exit criterion failed | §4 apply the fallback that milestone already pre-declared, §0 if the central bet is the thing that failed, §1 rows that rested on it |
| An external fact was verified or invalidated | The claim itself, its **verify** marker, and the matching item in §7 |
| A new risk or unknown appeared | §7, and §4 if it changes which milestone retires it |
| A new feature idea appeared | §5 with a one-line reason, unless the user explicitly promoted it into v1 |

## Step 3 — Edit inside the document's rules

Read the sections you are about to touch before touching them. Use targeted edits on specific blocks; if you find yourself rewriting a whole section, stop and ask whether the change is really that big (see "When the change is bigger than an edit").

The document was written under rules that are load-bearing, not stylistic. Keep them:

- **Decide, don't survey.** A decisions row gives one choice, at most three sentences of justification, and what would change my mind. Never "you could use X or Y" — if a genuinely undecidable choice appears, it belongs in §7, not in the body.
- **Exit criteria stay falsifiable.** Each one must be checkable by the user alone: a command with an expected output, a SQL query with an expected result, a file that must exist with stated contents. "Ingestion works" is not a criterion; "the second run reports 0 new rows" is.
- **No time estimates.** Relative size only — small, medium, large — plus which milestone is most likely to overrun and why.
- **At most seven milestones**, M0 through M6. If new work will not fit, it goes to §5, or it displaces something that moves to §5. Never add M7.
- **No code** beyond the SQL DDL in §3 and the clustering pseudocode in §3. Implementation detail belongs in the §6 prompts, which are prose instructions and may name endpoints, parameters and fields.
- **Uncertainty is marked, not smoothed.** Anything you did not verify today gets **verify** and an entry in §7.
- **The user's constraints are fixed**, unless they say otherwise: one part-time developer, Python and SQL only with no JavaScript or CSS, a hard €25/month ceiling, localhost first, low ops appetite, no terms-of-service violations. A change that breaches one of these is not an edit to make quietly — flag it.

When a milestone is finished, mark it rather than deleting it. The record of what was tested and what it cost is the most valuable thing the document accumulates. Check that the milestone's own document exists first — one per milestone, `docs/m<N>.md`, written by the command that measures it (`eww report density`, `eww report volume`, `eww report identity`, `eww eval attachments`) — because §4's numbers should be the ones that file reports:

```markdown
### M0 — Real events on a local map (small) — DONE 2026-09-24

**Result.** 62 events in 30 days, 5 hazard types, 5 continents, 4 non-wildfire European
events. Density bar passed, so the anchored-feeds bet holds and news-driven discovery
stays in §5. Exit criteria 1–5 all met; see docs/m0.md.
```

## Step 4 — Sweep for consistency

Run the bundled checker, which catches the structural breakages that are tedious to spot by eye:

```bash
python .claude/skills/update-architecture-doc/scripts/check_doc.py
# .cursor/skills/update-architecture-doc/scripts/check_doc.py is the identical mirror
```

It hard-fails on missing or reordered sections, a summary over 200 words, more than seven milestones, a milestone with no exit criteria, milestones and prompts that do not correspond, unbalanced code fences, and time estimates in milestone bodies. It then prints numbers that need a human judgement rather than an assertion: the cost total row, every **verify** marker, the decision and open-question counts.

The checker cannot judge meaning. Verify these yourself:

- The §1b total still fits under €25, and anything newly paid is flagged as scaling with volume if it does.
- Everything dropped anywhere appears in §5 with a one-line reason.
- Every **verify** marker has a matching item in §7, and every §7 verification item still needs doing.
- A changed decision's "what would change my mind" is still the right falsifier for the new choice, not the old one.
- The §0 summary still names the right central bet, and §4's earliest milestone is still the cheapest test of it.

## Step 5 — Record the revision

Two small edits so the user can tell a current document from a stale one.

Update the dateline under the title, keeping the original authorship date:

```markdown
*Written 2026-09-16, last updated 2026-09-24 against the README and constraints in
`docs/planning-prompt.md`. Facts about third-party services were checked against their
official pages on those dates; anything marked **verify** is either unconfirmed or
likely to change.*
```

Append an entry to `## Revision log`, the last section, after §7. Create the section if it does not exist yet. Keep sections 0 through 7 numbered as they are — the revision log sits outside that contract deliberately, so the document's shape never drifts:

```markdown
## Revision log

- **2026-09-24 — M0 done, density bar passed.** 62 events across 5 hazard types and 5
  continents, so the anchored-feeds bet holds. Marked M0 done with its measured numbers,
  closed open question 1, left news-driven discovery in §5. Sections touched: 0, 4, 7.
```

One entry per update session, newest last, each naming what triggered it, what changed and which sections moved.

## Step 6 — Report

Close with a short summary the user can act on: what changed and why, which sections moved, what the checker said, what you deliberately left alone, and anything that now needs their verification or a decision only they can make. If you marked something **verify**, say so explicitly rather than burying it.

## When the change is bigger than an edit

Some news does not fit in a patch. The document already anticipates the important cases and pre-declares what to do, so look there before inventing a new plan:

- **The central bet failed** (free authoritative feeds do not put enough real events on the map). §0 names the bet and §4's M0 names the fallback: bring orphan clustering forward and promote GDELT to a discovery source. Apply the declared fallback, update §0 so the bet reads as tested-and-wrong, and say plainly in your report that the plan's centre moved.
- **A constraint moved** (budget, skills, ops appetite, the laptop). This invalidates whole rows of §1 at once. Re-derive the affected decisions rather than patching one row, and flag which milestones change size.
- **The user wants something outside v1.** Default to §5 with a reason. Promote it only if they said to, and then say what it displaces, because seven milestones is a hard ceiling.

In all three cases, describe the reshaping in your report before applying it, so the user sees the shape of the change rather than discovering it in a diff.

## What goes wrong

These are the specific failures this skill exists to prevent. They are worth re-reading before you finish an update:

- **Half-applied changes.** The commonest and most damaging: §1 says one thing, §1b and §6 still say the old thing. The grep in step 2 is the cure; do it even when you are sure you know where the mentions are.
- **Verification by assertion.** Writing a price, rate limit or licence term as fact because it sounded right. If you did not read it on the vendor's page in this session, mark it **verify** and add it to §7. The document's credibility rests on this distinction.
- **Scope creep by paraphrase.** A feature mentioned in passing in chat becomes a milestone. New ideas go to §5 unless the user explicitly promoted them.
- **Losing the measured number.** Writing "M0 passed" instead of the counts. The numbers are what let the user re-judge a decision months later.
- **Turning a decision back into an option.** Edits that soften a choice into a comparison undo the document's whole purpose.
- **Regenerating a section to fix a sentence.** Costs provenance and specificity, and usually introduces drift somewhere else. Edit the block.
- **Silently changing the GeoJSON contract.** It is the boundary that lets the disposable Streamlit viewer be replaced later without touching the pipeline. Changing it is a real decision and must be reported as one.

## Worked example

A session ends with the user saying: "GDELT started 429-ing at 1 request per 5 seconds, so I moved to 10, and ReliefWeb approved my appname."

The change list: an external fact measured in practice (GDELT's tolerated rate, now first-hand rather than inferred) and an account state change (ReliefWeb approval, which retires a conditional).

Grep finds GDELT in §1 decisions, §1b cost table, §2 diagram, §4 M3 scope and exit criteria, §6 prompt M3, §7 verification list; ReliefWeb in §1, §1b, §4 M3, §6 prompt M3, §7 open questions.

The edits: §4 M3's rate-limit exit criterion changes from 5 s to 10 s; §6 prompt M3's GDELT instruction and its definition of done change to match; §7's GDELT rate-limit item is rewritten from "verify the informal limit" to the measured value with the date it was measured; §7's ReliefWeb approval question is closed; §4 M3 and §6 prompt M3 drop the "skipped with a logged warning if not approved" conditional. §1b is unchanged because both remain free. Then the checker, the dateline, a revision log entry, and a report that names the one thing the user still owes: nothing, in this case.

## Keeping the two copies in sync

This skill lives in `.cursor/skills/update-architecture-doc/` and is mirrored to
`.claude/skills/update-architecture-doc/` so Cursor and Claude Code both see it. If you edit one copy,
mirror it before you finish, or the two assistants will follow different instructions:

```bash
cp -r .cursor/skills/. .claude/skills/
```
