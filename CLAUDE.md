# Working in this repository

Extreme Weather Watch: a private, single-user map of recent hazard events, running on one Windows
laptop. Python 3.12 with uv, SQLite (WAL, STRICT), Streamlit + folium. The owner writes Python and SQL
and no JavaScript or CSS.

Read [docs/architecture.md](docs/architecture.md) before changing anything structural: it is the plan of
record, and §2 (the GeoJSON contract), §3 (the schema and how identity is decided) and §4 (the
milestones and their exit criteria) are the parts most code touches. [README.md](README.md) is how to
run it.

## One document per milestone: `docs/m<N>.md`

Every milestone leaves exactly one record in `docs/`, named after the milestone and nothing else:
`docs/m0.md`, `docs/m1.md`, `docs/m2.md`, `docs/m3.md`, and so on through M6. Never `m3-eval.md`,
never `m3-attachments-report.md`.

Each record is *written by the command that measures that milestone*, so it is refreshed by re-running
the command and its numbers are measured rather than remembered:

| Milestone | Command that writes its record |
|---|---|
| M0 | `uv run eww report density --days 30` |
| M1 | `uv run eww report volume` |
| M2 | `uv run eww report identity --days 30` |
| M3 | `uv run eww eval attachments` |

`eww/config.py` holds the naming rule once, as `milestone_doc(n)`. A new report writer calls that
instead of naming a file.

**Finishing a milestone means two writes**: its `docs/m<N>.md`, then `docs/architecture.md` marked done
in §4 with the measured numbers against each exit criterion (use the `update-architecture-doc` skill,
which will not let a half-applied change through). A milestone whose numbers live only in a chat
transcript is not finished.

## Conventions that are load-bearing

- **Tunable numbers are configuration, not literals.** Identity and attachment numbers (radii, windows,
  weights, thresholds) live in [identity.yaml](identity.yaml); hazard terms live in
  [eww/data/lexicon.yaml](eww/data/lexicon.yaml); everything else is in `eww/config.py`. A typo in
  either YAML file fails at start-up rather than being ignored.
- **The viewer imports `eww.api` and `eww.review`, and nothing else.** No SQL, no HTML, no JavaScript,
  no CSS in `app.py`. If the viewer needs a field, add it to the contract in `eww/api.py`.
- **`eww.resolve.create_event()` is the only insert into `event`.** Documents never create events.
- **Every command is idempotent.** Run it twice; the second run reports zero new rows.
- **Every external call goes through a rate-limited `Provider`** (`eww/http.py`, `eww/ratelimit.py`) and
  is logged to `logs/providers.jsonl`, which is how `eww doctor` proves the limits held. Honour every
  documented limit: GDELT answers 429 for hours after two calls inside five seconds.
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
