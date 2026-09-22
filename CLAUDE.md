# Working in this repository

Extreme Weather Watch: a private, single-user map of recent hazard events, running on one Windows
laptop. The pipeline is Python 3.12 with uv, SQLite (WAL, STRICT); the current viewer is Streamlit +
folium. The owner writes Python and SQL. On 2026-09-22 they chose **React for the frontend** (M7, in
`web/` only, built with the `impeccable` and `react-best-practices` skills in `.claude/skills`); it is
their first JavaScript project, so explain the toolchain as you introduce it and keep the frontend
free of data logic.

Read [docs/architecture.md](docs/architecture.md) before changing anything structural: it is the plan of
record, and §2 (the GeoJSON contract), §3 (the schema and how identity is decided) and §4 (the
milestones and their exit criteria) are the parts most code touches. [README.md](README.md) is how to
run it.

## Where the project stands (2026-09-22)

- M0, M1, M2 done. **M3 built and parked**: the whole news-attachment pipeline exists and is tested, but
  no real document was ever collected, because GDELT's DOC API refuses at far below its documented
  rate and ReliefWeb waits on an appname the owner deferred. `docs/m3.md` says so.
- **Data collection is not the priority now.** M1 to M3 went into collecting; the owner wants to move
  to M4 through M7 with the spine (GDACS, EONET, Copernicus) as it is. Do not propose more collectors
  unless asked.
- Two ideas are recorded for later, not to be built now: GDELT's **Web NGrams table of contents** as
  the news firehose (478 KB per 15 minutes, url/title/date/lang/image; the 11.9 MB quadgram file is
  ruled out), and **typesafe.ai's Jev** to classify those titles into the hazard schema. Both are in
  docs/architecture.md §1 and §7.

## One document per milestone: `docs/m<N>.md`

Every milestone leaves exactly one record in `docs/`, named after the milestone and nothing else:
`docs/m0.md`, `docs/m1.md`, ... `docs/m7.md`. Never `m3-eval.md`, never `m3-attachments-report.md`.

Each record is *written by the command that measures that milestone*, so it is refreshed by re-running
the command and its numbers are measured rather than remembered:

| Milestone | Command that writes its record |
|---|---|
| M0 | `uv run eww report density --days 30` |
| M1 | `uv run eww report volume` |
| M2 | `uv run eww report identity --days 30` |
| M3 | `uv run eww eval attachments` |
| M4 to M7 | the command each prompt in §6 names (`eww report frontend` for M7) |

`eww/config.py` holds the naming rule once, as `milestone_doc(n)`. A new report writer calls that
instead of naming a file.

**Finishing a milestone means two writes**: its `docs/m<N>.md`, then `docs/architecture.md` marked done
in §4 with the measured numbers against each exit criterion (use the `update-architecture-doc` skill,
which will not let a half-applied change through). A milestone whose numbers live only in a chat
transcript is not finished. There is no ceiling on the number of milestones since 2026-09-22, but every
new one needs the owner's say-so, exit criteria, a prompt and a record.

## Conventions that are load-bearing

- **Tunable numbers are configuration, not literals.** Identity and attachment numbers (radii, windows,
  weights, thresholds) live in [identity.yaml](identity.yaml); hazard terms live in
  [eww/data/lexicon.yaml](eww/data/lexicon.yaml); everything else is in `eww/config.py`. A typo in
  either YAML file fails at start-up rather than being ignored.
- **The Streamlit viewer imports `eww.api` and `eww.review`, and nothing else.** No SQL, no HTML, no
  JavaScript, no CSS in `app.py`. If the viewer needs a field, add it to the contract in `eww/api.py`.
  The React frontend (M7) talks only to `eww serve`'s HTTP endpoints, which wrap `eww.api` and add no
  logic; it never filters, maps severity or follows merge pointers itself.
- **`eww.resolve.create_event()` is the only insert into `event`.** Documents never create events.
- **Every command is idempotent.** Run it twice; the second run reports zero new rows.
- **Every external call goes through a rate-limited `Provider`** (`eww/http.py`, `eww/ratelimit.py`) and
  is logged to `logs/providers.jsonl`, which is how `eww doctor` proves the limits held. GDELT's DOC API
  is best-effort only: 3 events a run, 15 s apart, and 24 hours of silence after a 429, because
  retrying is what keeps a caller blocked (measured 2026-09-21/22).
- **References, never bytes.** Media is a URL; article bodies are never stored and excerpts are capped
  at 2,000 characters.
- UTC ISO 8601 timestamps, ULID keys, structured logging to stderr, pytest against a temporary SQLite
  file.
- Changed identity or attachment rules reach existing rows only through a rebuild: from the snapshots
  (`.claude/skills/playbook/files/rebuild-database.py`) or, for attachments,
  `eww --db data/copy.sqlite attach --rebuild` on a copy.

## Behind a TLS-inspecting proxy

On a corporate laptop a proxy such as Zscaler re-signs every HTTPS connection with its own root.
Windows trusts that root, Python's bundled certifi roots do not, so every call fails with
`CERTIFICATE_VERIFY_FAILED` while the browser on the same machine works. Trust the proxy's root as
well; never turn verification off and never route around the proxy, which on a work machine is a
policy matter, not a technical one.

```bash
# export the corporate root(s) from the Windows store, then combine with certifi's
powershell -Command "Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root | Where-Object { $_.Subject -match 'Zscaler' } | Sort-Object Thumbprint -Unique | ForEach-Object { '-----BEGIN CERTIFICATE-----'; [Convert]::ToBase64String($_.RawData, 'InsertLineBreaks'); '-----END CERTIFICATE-----' }" > corporate-roots.pem
uv run python -c "import certifi,pathlib; pathlib.Path('ca-bundle.pem').write_text(pathlib.Path(certifi.where()).read_text()+open('corporate-roots.pem').read())"
```

`ca-bundle.pem` at the repository root is picked up automatically (`config.CA_BUNDLE`, or set
`EWW_CA_BUNDLE`); both files are gitignored because they are machine-specific. `eww doctor` prints
which bundle is in force. Symptom to recognise: `curl` works and Python does not.

## Cost

Free tiers and local components only, under a hard ceiling of about €25 a month. Flag the cost before
proposing anything paid.
