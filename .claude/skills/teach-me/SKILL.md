---
name: teach-me
description: >
  Teaches the learner about this data-and-analytics monorepo: its code, models,
  pipelines, services, and domain concepts. Builds a clear lesson (purpose in
  the platform, meaning of the code, how it connects to the rest of the repo,
  resources)—never a quiz. Use when the user says "teach me", "explain this",
  "walk me through", "help me understand", or asks for a lesson on a file,
  function, DLT model, import service, ADF pipeline, SQL, config, or a declared
  topic in this repository.
---

# Teach Me

Teach **this repository** — not a generic programming course. The lesson should
make the learner understand what the target does *here*: business purpose,
layer/service, upstream/downstream, and the meaning of the actual code.

Not a quiz. Not a code review unless they ask for one.

## When this skill applies

- `@path` + "teach me" / "explain" / "walk me through" / "lesson"
- A pasted snippet from this repo (or clearly about it)
- A declared topic ("how does gold dim_active_parts work?", "what does
  sharepoint-import do?", "teach me bronze → silver")
- A folder, pipeline, or service ("walk me through this DLT project")

If they only want bugs fixed or a PR review, use the review/debug skill
instead. If they want both, teach first; optional short "things to watch for"
at the end.

## Repo context (use it)

This is a **data-and-analytics monorepo**. Lessons should place the target on
the map before diving into syntax.

Typical areas (orient, don't lecture the whole monorepo):

| Area | Role |
|------|------|
| `databricks/delta_live_tables/` | Transformation layer (DLT / Lakeflow): bronze → silver → (integration) → gold → platinum (+ interfaces) |
| `*-import/`, other Python services | Ingestion / batch / tooling that feeds the lake or ops |
| `azure-data-factory/` | Orchestration of jobs and imports |
| `powerplatform/` | Power Platform solutions |
| `demand-forecast/`, research folders | Analytics / ML / research workloads |

When teaching a DLT model, name the **layer**, **project/source**, and
**consumers** when knowable. When teaching an import service, say what it
loads, where it lands, and which DLT models (if any) pick it up.

Prefer **repo truth**: open the file, follow imports, read nearby README /
`CLAUDE.md` / schemas / tests. Do not invent platform conventions.

## Input modes (resolve scope first)

1. **File / path** — read it; pull only the dependencies needed for the lesson.
   Prefer a cited symbol or line range when given.
2. **Snippet** — teach what was pasted; add repo context only when it clarifies
   (e.g. which layer this lives in).
3. **Declared topic** — find the relevant path(s), confirm briefly if ambiguous,
   then teach from real code.
4. **Multi-file / pipeline / service** — platform-level flow first, then
   chapters (e.g. download → land → bronze → silver).

If scope is ambiguous, ask once:

- Whole file / module?
- One function / block / concept?
- Pipeline or service overview with chapters?

Default: match the size of what they attached or named.

## Hard rules

- **No quiz.** No "test yourself", multiple choice, or graded questions.
  Soft "if this broke, look here" only if useful — never as a quiz.
- **Teach the repo artifact**, not a textbook topic that happens to share a
  keyword. External concepts only as scaffolding for *this* code.
- **Teach, don't dump.** Guided narrative over line-by-line restating.
- **Scale the lesson** (see sizing). Wrong size is a failure mode.
- **Be precise** about real identifiers, schemas, decorators, regexes, SQL.
- **Language follows the target** — Python, SQL, Spark, YAML, Terraform,
  JSON (ADF), TypeScript, shell, etc.

## Lesson sizing

| Target | Lesson length | Depth |
|--------|---------------|--------|
| One expression / regex / decorator / `field(...)` | Short (≈½–1 screen) | Every token that matters |
| One function / small class | Medium | Signature, body, edge cases, callers if relevant |
| One module / DLT model / script | Structured chapters | Purpose in platform → stages → key blocks |
| Pipeline / several files / one service | Overview + chapters | Data/control flow; deep-dive hot paths only |
| Whole package / large area | Map + selective deep dives | Entrypoints and contracts; do not narrate every file |

Anti-patterns: 10 pages on a helper; one vague paragraph on a multi-stage
model; explaining every import.

Collapse boilerplate (headers, trivial re-exports, generated noise) unless
that *is* the ask.

## Lesson structure (required sections)

Rename chapter titles to fit the artifact (e.g. "Ingestion", "Silver clean",
"Gold merge").

### 1. Purpose (in this platform)

- What this exists to do for the business / data platform
- Where it sits: service, DLT layer, ADF pipeline, consumer dashboards, etc.
- Inputs and outputs in plain language (tables, files, APIs, side effects)
- One sentence: "if you remember nothing else…"

### 2. Place on the map

Brief orientation — enough that a teammate new to this area knows neighbors:

- Upstream sources and downstream dependents (files, tables, jobs)
- Same-layer siblings or parallel patterns when helpful
- Skip for a tiny isolated snippet unless context is the point

A short list or mermaid flow is fine for pipelines; skip diagrams for micro
targets.

### 3. Meaning of the code

Explain what the code **actually means**, not a paraphrase of identifiers.

Organize by **chapters** when larger than one function:

```markdown
### Chapter: <stage or concern>
**Role:** …
**Key pieces:**
- `symbol` / lines — what it does and why it is written this way *here*
```

Within chapters (or for a single function):

- Walk important constructs in reading or dependency order
- Dense bits (regex, windows, merges, `dp_*` / `field`, SCD logic): go deep
- Non-obvious intent: why this pattern in *this* codebase, what invariant it
  protects
- Tie names to domain meaning (`is_current`, commodity codes, dealer keys, …)

Granularity:

- **Micro** — one function / regex / SQL fragment: full dissection
- **Meso** — module/model: chapters; deep-dive only the hard parts
- **Macro** — multi-file: flow + contracts; deep-dive 1–2 cores

### 4. Vocabulary & concepts

Short glossary of terms the learner will see again **in this repo** (e.g.
SCD2, `@dlt.table`, `Schema.GOLD`, `field()`, dlt/Data Load Tool, landing
zone). Only what the target requires — not a general Spark course.

### 5. Gotchas & design notes

- Edge cases, null/ordering semantics, refresh/idempotency implications
- Intentional tradeoffs visible in the code
- "If this breaks, look here first" — 2–4 bullets max

Do not turn into a full standards review (`review-model` / PR review) unless
asked.

### 6. Resources

Prefer **nearby repo materials**, then official docs:

- Related paths: callers, schemas, enums, tests, silver/gold neighbors, ADF
  JSON, service README
- Local docs: `README.md`, `databricks/CLAUDE.md`, layer rules under
  `.claude/rules` when relevant
- Official docs for the stack pieces actually used (Databricks, Spark, dlt,
  pydantic, ADF, …)
- One solid external explainer only when it unlocks a concept in *this* file

```markdown
## Resources
- `path/in/repo` — how it relates to what you just learned
- [Title](url) — why it helps for *this* artifact
```

Do not invent URLs; if unsure, give a search term or repo path.

### 7. Suggested next lesson (optional)

One or two natural follow-ups in **this repo** ("next: the silver model that
feeds this", "the sharepoint-import path for this mapping table").

## Style

- Direct, teachy, concrete. Short paragraphs and bullets.
- Quote real identifiers; cite line ranges when helpful.
- Assume a competent engineer who does not yet know *this* area of the repo.
- Match stated level (new to DLT vs "I know Spark, explain the business keys").
- No performative enthusiasm.

## Optional extras (only when they help)

- Tiny worked example (input row → output row) for transforms
- Annotated regex / merge condition with labeled parts
- Before/after mental picture for migrations
- Pointer to a **reference model** or sibling service that shows the same
  pattern cleanly

## Anti-patterns for the agent

- Quiz / flashcards / challenge questions
- Generic language tutorials disconnected from the repo file
- Restating code in English without meaning or platform context
- Same depth for a decorator and a 500-line gold model
- Teaching "Python" when they asked to understand a warehouse dim

## Quick checklist before sending

- [ ] Purpose places the artifact in *this* monorepo
- [ ] Upstream/downstream mentioned when knowable
- [ ] Size matches micro / meso / macro
- [ ] Hard bits explained; boilerplate collapsed
- [ ] Resources include real repo paths (and honest external links)
- [ ] No quiz
