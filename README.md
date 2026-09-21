# Extreme Weather Watch

## Introduction

The value proposition of this website is to collect extreme weather events from news/social media channels/other weather apps/etc. in a single space. Users can see, at a glance, what is happening around the world, weather-wise. The website would cluster these extreme weather events, catalogue them by type, strength, etc. and provide users quick access to:

- written summary information related to the event (start date, ongoing, size, damages, n injured, missing, dead, etc.)
- pictures and videos of the event pulled from social media, prominent people (politicians, meteorologists), newspapers, etc.
- social media posts related to the event (mainly x, bluesky, instagram, reddit)
- current weather conditions + 3/5 days weather forecast

Ideally, the website should present itself like [https://zoom.earth/](https://zoom.earth/) or Google Maps/OpenMaps. It should be a world map, easy to navigate, zoom in, etc. It should be easy to pin your location using geolocation data, with the map centred on where you are.

The map would display extreme weather events over the last X days using different icons. There would be filters you can choose to show only events of a specific type / scale / timeframe. (e.g. only fires bigger than X in the last Y days).

The map would be interactive; by clicking on the dot of the natural event (e.g. red for fire, blue for flood, etc.) we would be able to see the photos/videos of the event, the headlines, maybe a feed (as explained above)

Eventually, if the website gets enough traction, we would introduce the possibility for a user to make their own account and submit information for an event. An event could be created by said user or they could join an already existing event. The user would be able to add pictures/videos/links but also enrich the description of the event. Practically making this a Wikipedia for natural disasters. Ultimately, the end-goal of the website would be to have first-hand user input to document extreme weather events, without changing the platform.

We realise this is too far ahead, so the first step would be to have a platform that finds (1) catalogues (2) and displays events without users' input. However, it is useful to consider this when designing a proper database structure to accommodate this future feature. No frontend is needed for this right now, as I would be interested mainly in the MVP right now: data collection, data presentation, website usability, front-end design, etc.

Slides: [https://docs.google.com/presentation/d/1ue2GXbwYTAzx-kkgJ25d_5NGQZNjg_IyWjjkkUGcYhA/edit#slide=id.g2759e7532a8_0_374](https://docs.google.com/presentation/d/1ue2GXbwYTAzx-kkgJ25d_5NGQZNjg_IyWjjkkUGcYhA/edit#slide=id.g2759e7532a8_0_374)

## Running it (M0: real events on a local map)

The first milestone is built: GDACS and NASA EONET events from the last 30 days, in SQLite, on a folium map inside Streamlit. Python and SQL only. The plan, schema and GeoJSON contract are in [docs/architecture.md](docs/architecture.md); the M0 density verdict is in [docs/m0.md](docs/m0.md).

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it). Optional: copy `.env.example` to `.env` and set `EWW_CONTACT` (it goes into the HTTP User-Agent).

```bash
uv sync --all-groups                                       # create .venv, install everything
uv run eww init-db                                         # apply sql/schema.sql, seed the source table
uv run eww collect --source gdacs --source eonet --days 30 # fetch, write data/snapshots/, ingest source_record
uv run eww resolve                                         # source_record -> event (+ event_geometry)
uv run eww doctor                                          # invariants: duplicates, unresolved, counts, heartbeat
uv run eww export --since 30d > events.geojson             # the GeoJSON contract (eww/api.py)
uv run eww report density --days 30                        # writes docs/m0.md (M0's record)
uv run streamlit run app.py                                # the map, at http://localhost:8501
uv run pytest                                              # tests against a temporary SQLite file
```

Every command is idempotent: run `collect` twice and the second run reports 0 new rows; run `resolve` twice and the second changes nothing.

### Collection while the laptop is off (M1)

[.github/workflows/collect.yml](.github/workflows/collect.yml) runs the same `eww collect` every three hours in GitHub Actions (cron `7 */3 * * *`, UTC) and commits the raw snapshots and the run log to the orphan `data` branch. Nothing needs a secret. The laptop replays what it has not seen:

```bash
uv run eww sync                                   # git fetch origin data:data, ingest new snapshot and run files, resolve
uv run eww ingest --from branch                   # only the replay step; --from local replays data/snapshots
uv run eww collect --all-spine --out some-dir     # exactly what Actions runs: snapshots/ and runs/ under some-dir, no database
uv run eww report volume                          # fresh clone of the data branch, pack size, yearly extrapolation -> docs/m1.md
uv run eww task-scheduler                         # prints the schtasks commands that run sync at logon and every 2 hours
```

Data branch layout: `snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json` (the raw items exactly as fetched, inside a small envelope) and `runs/<YYYY-MM-DD>.jsonl` (one line per run and source with run_id, source_id, scheduled_for, started_at, finished_at, status, http_status, items_seen, snapshot_path, error). The `heartbeat` SQL view lists every 3-hour slot since the first run and whether an ok run started within 45 minutes of it; the viewer's status strip, the GeoJSON `meta` and `eww doctor` read it, and the strip turns red above 2 missed runs in 7 days or when the last successful run is more than 6 hours old.

### One event, one pin (M2)

The same flood or cyclone arrives from several feeds and must become one pin, reversibly. Copernicus EMS Rapid Mapping activations join the spine (`--all-spine` now collects GDACS, EONET and Copernicus). `eww resolve` applies the identity rules of [docs/architecture.md §3](docs/architecture.md) to each new record, in this order: the GLIDE number; the feed's own id (a new GDACS episode, a new EONET track point); the deterministic cross-source keys (Copernicus `gdacsId`, the GDACS report URL EONET cites), in both directions; then blocking and scoring inside a hazard class, with a radius and a time window per hazard from `eww/config.py`. A score of 0.90 or more merges automatically, 0.60 to 0.90 writes a `merge_proposal` for the Review tab, and less creates a separate event. Named storms merge on their name (a track's first point can be thousands of km from the other feed's current position, so named storms block basin-wide); two differently named storms are never merged automatically; a pair the sources themselves keep apart (an EONET item that cites a *different* GDACS id) is never scored. Every merge moves records under a pointer and writes an `event_lineage` row with the exact ids moved; a revert undoes precisely that row and writes a second one. No event row is ever deleted.

**The aggregation radius.** Two feeds' records share one pin automatically only when their positions lie within the aggregation radius in [identity.yaml](identity.yaml) (25 km by default, wider for storm classes whose tracks each feed samples hours apart, and for regional hazards), measured between the closest pair of their positions. That gate applies to every automatic join, a shared id or GLIDE number included: beyond the radius the pipeline keeps two pins and writes a proposal saying "cites GDACS 1104124, 85 km apart" for the Review tab. A feed's own successive records (a storm track, a new GDACS episode) are one event by that feed's identity and are not gated, and a person's accept is never gated. On 2026-09-17 the gate did more than keep three Nepal flood positions 85 to 102 km apart on two pins: it caught EONET items citing GDACS wildfire ids that GDACS had since reused for fires on other continents (Indonesia → United States, Australia → Algeria, Brazil → Russia), which the id alone had merged silently. GDACS wildfire ids are not stable over time.

```bash
uv run eww collect --source copernicus --days 30                          # the new spine source (part of --all-spine)
uv run eww resolve                                                        # now reports keys, auto-merges, proposals and pairs kept apart by the radius
uv run eww identity                                                       # the identity parameters in force (identity.yaml)
uv run eww report identity --days 30                                      # docs/m2.md: joins by rule, how far apart the feeds place one event, proposals, labels
uv run eww export --since 7d --hazard flood --min-severity 0.66 --count   # the pin count the viewer must match
uv run eww labels candidates --days 30                                    # data/labels/merge_candidates.csv: every blocked pair, its evidence, an empty same_event
uv run eww eval merges                                                    # precision and recall of the auto-merge rule against data/labels/merge_pairs.csv
uv run eww doctor                                                         # ... plus merges, proposals and EMS activations
```

Tuning: every number behind identity (aggregation radius per hazard, blocking radii and windows, thresholds, score weights) lives in `identity.yaml`; a typo or an unknown hazard name fails at start-up instead of being ignored. Edit a value, rebuild the database from the snapshots (`uv run python .cursor/skills/playbook/files/rebuild-database.py --swap`, 8 seconds on 2026-09-17), then re-run `eww eval merges` and `eww report identity` to see what changed.

Labelling: copy rows from `merge_candidates.csv` into `merge_pairs.csv` and fill `same_event` with `yes` or `no`; the `linked` column says a feed cites the other feed's id, `key_conflict` says it cites a different one, and `pipeline`/`merged_by` are the verdict at the time of writing. `eww eval merges` re-reads the database, so labels stay valid across rebuilds and merges. The file shipped on 2026-09-17 holds a starter set of 24 true pairs and 24 non-pairs labelled from facts in the feeds themselves (same storm name in both feeds, an explicit GDACS id, or a conflicting one), marked in `labelled_by`, plus the open proposals with an empty label: review them, overwrite freely.

Severity is one function (`eww/severity.py`): GDACS Green/Orange/Red are 0.33/0.66/1.0 with the continuous `episodealertscore` as a tie-break inside the band (the SEARCH API's `alertscore` is only 1/2/3); EONET magnitudes are scaled per unit (knots on the Saffir-Simpson steps, hectares with 5,000 ha at Green, acres converted); a Copernicus activation sets the `ems_activation` flag in the GeoJSON and raises the score to at least 0.66. The viewer shows an "EMS activation" badge for those events.

Identity rules apply when a record is first resolved. To re-apply changed rules or thresholds to data already in the database, rebuild it from the snapshots, which is what the architecture designed the data branch for: `uv run eww --db data/new.sqlite init-db`, then `ingest --from all`, then `resolve` (8 seconds for 3,855 records on 2026-09-17), and swap the file.

| Path | What it is |
|---|---|
| `eww/cli.py` | the `eww` command (typer) |
| `eww/config.py` | every setting: paths, User-Agent, feed URLs, hazard mappings, severity scores, the density bar; loads and validates `identity.yaml` |
| `identity.yaml` | the tunable identity numbers: aggregation radius per hazard, blocking radii and windows, thresholds, score weights |
| `docs/m2.md` | the M2 identity report written by `eww report identity`: parameters in force, joins by rule, feed disagreement, proposals, labels |
| `eww/db.py`, `sql/schema.sql` | SQLite connection (WAL, foreign keys) and the DDL, copied verbatim from the architecture document |
| `eww/collectors/gdacs.py`, `eww/collectors/eonet.py`, `eww/collectors/copernicus.py` | `fetch(since, until)`, `normalise(item)`, `linked_ids(payload)` and `storm_name(payload)` per source; raw items go to `data/snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json` |
| `eww/ingest.py` | snapshot files -> `source_record` and `collector_run`, idempotent on the natural key plus payload hash |
| `eww/resolve.py` | `source_record` -> `event`; `create_event()` is the only insert into `event`; GLIDE, sibling, cross-source key, then blocking and scoring |
| `eww/matching.py`, `eww/merge.py`, `eww/events.py` | blocking radii, storm names and the score; `merge()` / `revert()` / `propose()` with `event_lineage`; the derived columns and geometries of an event |
| `eww/severity.py`, `eww/geo.py` | the one severity function; haversine, WKT points, bounding boxes |
| `eww/review.py`, `eww/labels.py` | the Review tab's lists and its three write actions; candidate pairs and the precision/recall evaluation |
| `eww/api.py` | `events_geojson(...)`, the interface the viewer and any future frontend read; since M3 also `event_documents()` (News tab) and `attributions()` (About) |
| `eww/enrich/` | the laptop enrichment collectors: `gdelt.py`, `reliefweb.py`, `queries.py` (what an event is called in the news), the per-event run loop |
| `eww/documents.py`, `eww/lexicon.py`, `eww/data/lexicon.yaml` | canonical URLs, idempotent document rows, the 60-day purge; the hazard lexicon in English and Italian with negative patterns |
| `eww/extract.py`, `eww/geocode.py`, `eww/embed.py`, `eww/attach.py` | lexicon + NER extraction; the three-tier cached geocoder and `eww geonames load`; the local embedding model; `attach_document()` and `--rebuild` |
| `eww/ratelimit.py`, `eww/http.py` | limiters (minimum interval, sliding window with budgets), the rate-limited `Provider` per service, the structured log `eww doctor` reads |
| `docs/m3.md` | the M3 record written by `eww eval attachments`: coverage, documents, geocoding, rate limits, hand-checked precision |
| `eww/heartbeat.py`, `eww/gitdata.py`, `eww/report.py` | collector_run heartbeat and the `heartbeat` view; reading the data branch with git plumbing; the density and volume reports |
| `app.py` | the Streamlit + folium viewer (Map, Review and About tabs; Details and News in the sidebar); imports only `eww.api` and `eww.review` |
| `eww/data/countries.csv` | ISO3 -> continent, from GeoNames countryInfo.txt |
| `data/` (gitignored except `data/labels/`) | `eww.sqlite`, `snapshots/`, `runs/`, `models/` (the embedding model cache), the regenerated `labels/merge_candidates.csv`; the hand-labelled `labels/merge_pairs.csv` and `labels/attachment_sample.csv` are versioned |
| `logs/providers.jsonl` (gitignored) | one line per external call and geocode lookup; the evidence behind `eww doctor`'s rate-limit and cache-hit numbers |
| `.cursor/skills/playbook/files/` (mirrored in `.claude/`) | helpers that are not product code: `rebuild-database.py` (rebuild from snapshots and swap in with a backup), `seed-merge-labels.py` (seed the pairs file from feed facts) |
| `.github/workflows/collect.yml`, the `data` branch | the scheduled collector and its raw archive |

Data and attribution: Global Disaster Alert and Coordination System (GDACS), European Union, CC BY 4.0; NASA Earth Observatory Natural Event Tracker (EONET), public domain; Copernicus Emergency Management Service (© European Union), each activation credited as "Copernicus Emergency Management Service (© \<year\> European Union), \<code\>"; headlines and thumbnail references via the GDELT Project (gdeltproject.org); reports from ReliefWeb (UN OCHA), personal and non-commercial use; place names, coordinates and country data from GeoNames, CC BY 4.0; geocoding by Nominatim and map tiles © OpenStreetMap contributors, ODbL.

### Headlines on pins (M3)

News is *attached* to events the feeds already know, never used to create them (docs/architecture.md §3, step 2). Two enrichment collectors run on the laptop inside `eww sync`: **GDELT DOC 2.0** queries one event at a time, best severity first and at most 40 events per run, with a query built from the event's country names in English and Italian, the admin1 names its titles carry, the storm name and the hazard keywords of the lexicon (`("Huelva" OR "Spain" OR "Spagna") (wildfire OR bushfire OR "incendio boschivo" ...)`), from the last successful run for that event (`enrichment_run`) or the event's start minus two days the first time, at least 5 seconds between calls, and it stops for the run on a 429. **ReliefWeb** reports are filtered by GLIDE number or by country and disaster type; without an approved `RELIEFWEB_APPNAME` in `.env` the collector logs one warning and skips. Both write `document` rows (URL, canonical URL, title, publisher, time, language, a thumbnail *reference*; a ReliefWeb body is cut to 2,000 characters, in the stored payload too) and a `document_retrieval` row naming the event whose query found them. Re-runs are no-ops: `UNIQUE (source_id, url_canonical)`.

Extraction is deterministic and stops at the first stage that classifies and locates: the hazard lexicon in English and Italian ([eww/data/lexicon.yaml](eww/data/lexicon.yaml), with negative patterns so "a flood of complaints" and "a storm of criticism" are nothing), spaCy NER (`xx_ent_wiki_sm` on every text, `en_core_web_sm` on English), then the geocoder ([eww/geocode.py](eww/geocode.py)): tier 1 the local GeoNames gazetteer (`eww geonames load`, cities500 plus admin1 and country rows, FTS5 over alternate names, country hint from the text's country names and demonyms or from the querying event, ties broken by population), tier 2 the GeoNames web service (`GEONAMES_USERNAME`, budgeted under 1,000 an hour and 10,000 a day), tier 3 the public Nominatim (4 requests a minute, one at a time). Every answer, misses included, is cached in `geocode_cache`; remote tiers stop once a document has one located place. Every document is embedded with `paraphrase-multilingual-MiniLM-L12-v2` on the CPU (`document_embedding`, float32 BLOBs; the model is cached under `data/models/`).

`attach_document()` scores a document against the live events of its hazard class whose window overlaps its own and that a resolved place puts within 2R, or that share its country, or whose query fetched it: `s = 0.45 spatial + 0.25 temporal + 0.30 text` (no place: `0.20 temporal + 0.80 text`, text ≥ 0.60), `+0.10` when the event's own query fetched the document; ≥ 0.75 attached, 0.55–0.75 a candidate for the Review tab, below that nothing (and `eww purge` deletes unattached documents after 60 days). Every number lives in the `attachment:` section of [identity.yaml](identity.yaml). Resolved places of attached documents become `event_geometry` rows with `role = 'mention'` (stored, not drawn). Syndicated copies (cosine ≥ 0.95) are shown as one headline with a source count. The sidebar of a clicked pin gets a **News** tab (headline link, publisher, time, thumbnail by URL with the link as fallback), the Review tab gets **Candidate headlines** with Accept / Reject, and an **About** tab carries the credits (GDELT, ReliefWeb, GeoNames CC BY 4.0, OpenStreetMap ODbL).

```bash
uv sync --all-groups                               # also installs the 'enrich' group: sentence-transformers, spaCy and its two models (GitHub Actions never installs it)
uv run eww geonames load                           # once: GeoNames cities500 + admin1 + countryInfo -> gazetteer_place, FTS rebuilt (CC BY 4.0)
uv run eww sync                                    # ingest + resolve, then enrich (GDELT, ReliefWeb) + extract + embed + attach; --no-enrich / --no-attach to skip
uv run eww enrich --source gdelt --max-events 40   # the collectors alone; ReliefWeb needs RELIEFWEB_APPNAME
uv run eww extract                                 # lexicon + NER + geocoder for every document without an extraction (--no-remote: gazetteer only)
uv run eww embed                                   # vectors for every document without one
uv run eww attach                                  # decisions for undecided documents; on a copy: `eww --db data/copy.sqlite attach --rebuild`
uv run eww eval attachments --sample 100           # data/labels/attachment_sample.csv: fill `correct` yes/no and `cause`
uv run eww eval attachments                        # precision from the filled file -> docs/m3.md (also coverage, geocoding, rate limits)
uv run eww purge --dry-run                         # what the 60-day purge of unattached documents would delete
uv run eww doctor                                  # ... plus documents, coverage, geocode cache hit rate and the rate-limit maxima read from logs/providers.jsonl
```

Every external call goes through one rate-limited `Provider` per service ([eww/http.py](eww/http.py), [eww/ratelimit.py](eww/ratelimit.py)) and is appended to `logs/providers.jsonl` with its User-Agent; `eww doctor` proves from that file that Nominatim never exceeded 4 requests in a minute, GeoNames 1,000 in an hour, GDELT spacing never fell under 5 s and every call carried `EWW_CONTACT`. Set `EWW_CONTACT` in `.env` to the address you want published (the default is the repository URL). No language model is used anywhere in M3.

Tuning: edit `attachment:` in `identity.yaml` or the lexicon, copy the database, run `eww --db data/copy.sqlite attach --rebuild` (every pipeline decision is re-made from scratch in document order; human accepts and rejects stay), then `eww eval attachments`. `EWW_TITLE_SIMILARITY=embedding` switches the cross-source identity score's text term from token Jaccard to the embedding cosine; it is off by default until `eww eval merges` has been re-run under it.

### Where the documents are, and the `docs/m<N>.md` rule

**Every milestone leaves exactly one record in `docs/`, named after the milestone and nothing else:** `docs/m0.md`, `docs/m1.md`, `docs/m2.md`, `docs/m3.md`, and so on through M6. Each is written by the command that measures that milestone, so re-running the command refreshes the record in place and its numbers are measured rather than remembered. `eww/config.py` holds the naming rule once, as `milestone_doc(n)`; a new report writer inherits it by calling that instead of naming a file.

| Document | Written by | What it holds |
|---|---|---|
| [docs/architecture.md](docs/architecture.md) | by hand (the `update-architecture-doc` skill) | the plan of record: decisions, cost, the schema, the GeoJSON contract, the milestones and their exit criteria |
| [docs/m0.md](docs/m0.md) | `uv run eww report density --days 30` | M0: events by hazard type and by continent, the density bar and its verdict |
| [docs/m1.md](docs/m1.md) | `uv run eww report volume` | M1: the data branch's pack size, growth per day, the extrapolated year |
| [docs/m2.md](docs/m2.md) | `uv run eww report identity --days 30` | M2: the identity parameters in force, joins by rule, how far apart the feeds place one event, proposals, labels |
| [docs/m3.md](docs/m3.md) | `uv run eww eval attachments` | M3: attachment coverage, documents, geocoding, the rate-limit maxima, hand-checked precision and its errors |
| [docs/planning-prompt.md](docs/planning-prompt.md) | by hand, once | the brief the architecture document was written against |

**Finishing a milestone means two writes:** its `docs/m<N>.md` (run the command), then `docs/architecture.md` marked done with the measured numbers against each exit criterion. Same rule for M4, M5 and M6.

## Proof of Concept

Steps/points:

1. Version control via GitHub.
2. Aggregate data either via a “news” API or via web scraping. (Interesting to research if an LLM swarm + bots + search with a browser such as Browserbase would be sufficient since parsing websites is too complex)
3. Save the data in a database (cloud-based?/postgreSQL? not sure what is best, to investigate) (critical to have a method to properly aggregate events, e.g. if 5/10 posts/news articles are about the same event we should be able to understand this and group these)
4. Normalise, clean and enrich the data. Add approximate (or precise depending on the information available) location pins (even multiple pins for the same article - e.g. if it mentions streets or multiple cities).
5. Display data via a website:
6. Add a “donate” button (for later) :)

## Links

Several websites collect and visualise weather events on maps. Here are some of the best options.

Great maps:

- [https://earth.nullschool.net/](https://earth.nullschool.net/)
- [https://zoom.earth/](https://zoom.earth/)
- [https://worldview.earthdata.nasa.gov/](https://worldview.earthdata.nasa.gov/)
- [https://www.wxcharts.com/](https://www.wxcharts.com/)
- [https://www.weather.gov/](https://www.weather.gov/)
- [https://www.msn.com/en-gb/weather/maps/?type=radar](https://www.msn.com/en-gb/weather/maps/?type=radar)

Other maps:

- [https://www.openstreetmap.org/](https://www.openstreetmap.org/#map=8/52.154/5.295)
- [https://what3words.com/](https://what3words.com/)

Good weather data:

- [https://weatherspark.com/](https://weatherspark.com/)
- [https://www.weatherpro.com/en/nl](https://www.weatherpro.com/en/nl)
- [https://charts.ecmwf.int/](https://charts.ecmwf.int/)

Collection of catastrophic events data:

- [https://www.emdat.be/](https://www.emdat.be/) (good website we could take data from?)
- [https://gdacs.org/](https://gdacs.org/)
- [https://disasters-nasa.hub.arcgis.com/](https://disasters-nasa.hub.arcgis.com/)
- [https://reliefweb.int/disasters](https://reliefweb.int/disasters)

Data on climate change:

- [https://climatechangetracker.org/](https://climatechangetracker.org/)
- [https://atlas.climate.copernicus.eu/atlas](https://atlas.climate.copernicus.eu/atlas)
- [https://climatereanalyzer.org/](https://climatereanalyzer.org/)
- [https://cds.climate.copernicus.eu/](https://cds.climate.copernicus.eu/) (API?)

Repos/Documentation:

- [github.com/sunshineplan/weather](https://github.com/sunshineplan/weather)
- [https://gitlab.com/KNMI-OSS/KNMI-App](https://gitlab.com/KNMI-OSS/KNMI-App)
