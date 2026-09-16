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

The first milestone is built: GDACS and NASA EONET events from the last 30 days, in SQLite, on a folium map inside Streamlit. Python and SQL only. The plan, schema and GeoJSON contract are in [docs/architecture.md](docs/architecture.md); the M0 density verdict is in [docs/m0-density.md](docs/m0-density.md).

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it). Optional: copy `.env.example` to `.env` and set `EWW_CONTACT` (it goes into the HTTP User-Agent).

```bash
uv sync --all-groups                                       # create .venv, install everything
uv run eww init-db                                         # apply sql/schema.sql, seed the source table
uv run eww collect --source gdacs --source eonet --days 30 # fetch, write data/snapshots/, ingest source_record
uv run eww resolve                                         # source_record -> event (+ event_geometry)
uv run eww doctor                                          # invariants: duplicates, unresolved, counts, heartbeat
uv run eww export --since 30d > events.geojson             # the GeoJSON contract (eww/api.py)
uv run eww report density --days 30                        # writes docs/m0-density.md
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
uv run eww report volume                          # fresh clone of the data branch, pack size, yearly extrapolation -> docs/m1-volume.md
uv run eww task-scheduler                         # prints the schtasks commands that run sync at logon and every 2 hours
```

Data branch layout: `snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json` (the raw items exactly as fetched, inside a small envelope) and `runs/<YYYY-MM-DD>.jsonl` (one line per run and source with run_id, source_id, scheduled_for, started_at, finished_at, status, http_status, items_seen, snapshot_path, error). The `heartbeat` SQL view lists every 3-hour slot since the first run and whether an ok run started within 45 minutes of it; the viewer's status strip, the GeoJSON `meta` and `eww doctor` read it, and the strip turns red above 2 missed runs in 7 days or when the last successful run is more than 6 hours old.

| Path | What it is |
|---|---|
| `eww/cli.py` | the `eww` command (typer) |
| `eww/config.py` | every setting: paths, User-Agent, feed URLs, hazard mappings, severity scores, the density bar |
| `eww/db.py`, `sql/schema.sql` | SQLite connection (WAL, foreign keys) and the DDL, copied verbatim from the architecture document |
| `eww/collectors/gdacs.py`, `eww/collectors/eonet.py` | `fetch(since, until)` and `normalise(item)` per source; raw items go to `data/snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json` |
| `eww/ingest.py` | snapshot files -> `source_record` and `collector_run`, idempotent on the natural key plus payload hash |
| `eww/resolve.py` | `source_record` -> `event`; `create_event()` is the only insert into `event` |
| `eww/api.py` | `events_geojson(...)`, the interface the viewer and any future frontend read |
| `eww/heartbeat.py`, `eww/gitdata.py`, `eww/report.py` | collector_run heartbeat and the `heartbeat` view; reading the data branch with git plumbing; the density and volume reports |
| `app.py` | the Streamlit + folium viewer; imports only `eww.api` |
| `eww/data/countries.csv` | ISO3 -> continent, from GeoNames countryInfo.txt |
| `data/` (gitignored) | `eww.sqlite`, `snapshots/`, `runs/` |
| `.github/workflows/collect.yml`, the `data` branch | the scheduled collector and its raw archive |

Data and attribution: Global Disaster Alert and Coordination System (GDACS), European Union, CC BY 4.0; NASA Earth Observatory Natural Event Tracker (EONET), public domain; country data from GeoNames, CC BY 4.0; map tiles from OpenStreetMap contributors, ODbL.

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
