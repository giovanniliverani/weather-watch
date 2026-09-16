"""Settings for Extreme Weather Watch.

Everything tunable lives here: paths, HTTP identity, feed URLs, hazard mappings, severity
normalisation and the seed rows for the `source` table. Nothing else reads the environment.
Optional overrides come from a `.env` file at the repository root (see `.env.example`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------- identity
VERSION = "0.1"
CONTACT = os.getenv("EWW_CONTACT", "https://github.com/giovanniliverani/weather-watch")
USER_AGENT = f"extreme-weather-watch/{VERSION} (+{CONTACT})"

# --------------------------------------------------------------------------- paths
DATA_DIR = Path(os.getenv("EWW_DATA_DIR", str(PROJECT_ROOT / "data")))
DB_PATH = Path(os.getenv("EWW_DB_PATH", str(DATA_DIR / "eww.sqlite")))
SNAPSHOT_DIR = DATA_DIR / "snapshots"  # data/snapshots/<source>/<YYYY-MM-DDTHH-MM>Z.json
RUNS_DIR = DATA_DIR / "runs"  # data/runs/<YYYY-MM-DD>.jsonl, one line per (run, source)
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
DOCS_DIR = PROJECT_ROOT / "docs"
COUNTRIES_CSV = Path(__file__).resolve().parent / "data" / "countries.csv"

# --------------------------------------------------------------------------- HTTP and scheduling
HTTP_TIMEOUT_S = 60.0
HTTP_RETRIES = 3  # attempts per request; retried on 5xx, 429 and transport errors
HTTP_RETRY_BACKOFF_S = 2.0
COLLECT_SLOT_HOURS = 3  # collector_run.scheduled_for is the start of the 3-hour slot a run served
DEFAULT_COLLECT_DAYS = 30
DEFAULT_EXPORT_SINCE = "30d"
DEFAULT_VIEWER_DAYS = 14
SNAPSHOT_FORMAT = "eww.snapshot/1"  # the envelope written by collectors and replayed by ingest

# --------------------------------------------------------------------------- spine, data branch, heartbeat
SPINE_SOURCES = ["gdacs", "eonet"]  # `eww collect --all-spine`; copernicus joins in M2
DATA_BRANCH = "data"  # orphan branch holding snapshots/ and runs/, written by .github/workflows/collect.yml
GIT_REMOTE = "origin"
HEARTBEAT_GRACE_MINUTES = 45  # a run serves its 3-hour slot only if it started within this many minutes
STATUS_RED_MISSED_RUNS = 2  # the viewer's status strip turns red above this many missed runs in 7 days
STATUS_RED_STALE_HOURS = 6.0  # ...or when the last successful collector run is older than this
VOLUME_PACK_LIMIT_MB = 15  # M1 exit criterion 5: size-pack of a fresh data-branch clone after 3 days
VOLUME_DAILY_LIMIT_MB = 5  # docs/architecture.md §1: above this per day, the sink moves to Cloudflare R2

# --------------------------------------------------------------------------- hazards
# Must match the CHECK constraint on event.hazard_type in sql/schema.sql.
HAZARD_TYPES = [
    "flood",
    "tropical_cyclone",
    "severe_storm",
    "wildfire",
    "heatwave",
    "coldwave",
    "drought",
    "landslide",
    "volcano",
    "earthquake",
    "tsunami",
    "other",
]

# Precision recorded on the primary geometry taken from a feed: GDACS flood and drought points
# are area centroids, everything else is the source's own coordinate.
GEOMETRY_PRECISION_BY_HAZARD = {"flood": "admin1", "drought": "admin1"}
GEOMETRY_PRECISION_DEFAULT = "exact"

# --------------------------------------------------------------------------- GDACS
# Parameter names confirmed against https://www.gdacs.org/gdacsapi/swagger/v1/swagger.json on
# 2026-09-16 (GET /api/Events/geteventlist/search: eventlist, alertlevel, fromDate, toDate,
# country, severity, pageSize, pageNumber, caller). Observed behaviour the same day:
#   * `alertlevel` is required; without it the API answers 204 with an empty body.
#   * A page past the end answers 204, so paging stops on 204 or on an empty FeatureCollection.
#   * A combined `eventlist=EQ;TC;FL;VO;WF;DR;TS` collapsed to 4 rows whenever one type (TS)
#     had no rows in the window, so the collector queries one event type per request.
GDACS_SEARCH_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GDACS_RSS_URL = "https://www.gdacs.org/xml/rss.xml"
GDACS_REPORT_URL = "https://www.gdacs.org/report.aspx?eventtype={eventtype}&eventid={eventid}"
GDACS_EVENT_TYPES = ["EQ", "TC", "FL", "VO", "WF", "DR", "TS"]
GDACS_ALERT_LEVELS = ["Green", "Orange", "Red"]
GDACS_PAGE_SIZE = 100  # the API maximum
GDACS_HAZARD = {
    "EQ": "earthquake",
    "TC": "tropical_cyclone",
    "FL": "flood",
    "VO": "volcano",
    "WF": "wildfire",
    "DR": "drought",
    "TS": "tsunami",
}
GDACS_SEVERITY = {"Green": 0.33, "Orange": 0.66, "Red": 1.0}

# --------------------------------------------------------------------------- EONET
# https://eonet.gsfc.nasa.gov/docs/v3 (read 2026-09-16): `status`, `days`, `start`/`end`
# (YYYY-MM-DD, inclusive), `category` (comma-separated). `start`/`end` and `days=30` returned the
# same 1,051 events for the same window on that date; the collector uses `start`/`end` so that
# fetch(since, until) means exactly what it says.
EONET_EVENTS_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
EONET_CATEGORIES = [
    "wildfires",
    "severeStorms",
    "floods",
    "volcanoes",
    "drought",
    "landslides",
    "tempExtremes",
    "snow",
    "dustHaze",
    "earthquakes",
]
EONET_HAZARD = {
    "wildfires": "wildfire",
    "severeStorms": "severe_storm",  # becomes tropical_cyclone when the title matches EONET_STORM_TITLE_RE
    "floods": "flood",
    "volcanoes": "volcano",
    "drought": "drought",
    "landslides": "landslide",
    "tempExtremes": "heatwave",  # becomes coldwave when the title matches EONET_COLD_TITLE_RE
    "snow": "coldwave",
    "dustHaze": "other",
    "earthquakes": "earthquake",
}
EONET_STORM_TITLE_RE = re.compile(r"hurricane|typhoon|cyclone|tropical", re.IGNORECASE)
EONET_COLD_TITLE_RE = re.compile(r"cold|freez", re.IGNORECASE)
EONET_SEVERITY = 0.4
# EONET Polygon rings arrive as [lat, lon] pairs, the reverse of GeoJSON order. Verified
# 2026-09-16: 39 of 40 GDACS-sourced flood polygons land on GDACS's own point only after
# swapping (the 40th sits on the lat=lon diagonal). Point geometries are in [lon, lat] order.
EONET_POLYGON_AXES_SWAPPED = True
# EONET events carry no country. GDACS-mirrored titles read "Flood in Croatia 1104153"; these
# US fire systems only ever report US incidents.
EONET_TITLE_COUNTRY_RE = re.compile(r"^\s*[A-Za-z ]+?\s+in\s+(.+?)\s+\d+\s*$")
EONET_SOURCE_COUNTRY = {"IRWIN": "USA", "InciWeb": "USA", "CALFIRE": "USA"}

# GDACS country spellings that differ from the GeoNames names in eww/data/countries.csv.
COUNTRY_NAME_ALIASES = {
    "Czech Republic": "CZE",
    "Islamic Republic of Iran": "IRN",
    "Russian Federation": "RUS",
    "Türkiye": "TUR",
    "Turkiye": "TUR",
    "The Democratic Republic of Congo": "COD",
    "Democratic Republic of Congo": "COD",
    "Congo": "COG",
    "Republic of Korea": "KOR",
    "Democratic People's Republic of Korea": "PRK",
    "Lao People's Democratic Republic": "LAO",
    "Viet Nam": "VNM",
    "Syrian Arab Republic": "SYR",
    "United Republic of Tanzania": "TZA",
    "Bolivia (Plurinational State of)": "BOL",
    "Venezuela (Bolivarian Republic of)": "VEN",
    "Republic of Moldova": "MDA",
    "United States of America": "USA",
    "United States": "USA",
    "Côte d'Ivoire": "CIV",
    "Cabo Verde": "CPV",
    "Swaziland": "SWZ",
    "Macedonia": "MKD",
    "Burma": "MMR",
    "Palestine": "PSE",
    "Micronesia (Federated States of)": "FSM",
    "Brunei Darussalam": "BRN",
    "Timor-Leste": "TLS",
    "East Timor": "TLS",
}

# --------------------------------------------------------------------------- seed rows for `source`
SOURCES = {
    "gdacs": {
        "kind": "authority",
        "display_name": "Global Disaster Alert and Coordination System (GDACS)",
        "terms_url": "https://www.gdacs.org/About/termofuse.aspx",
        "attribution": "Global Disaster Alert and Coordination System (GDACS), European Union, CC BY 4.0",
    },
    "eonet": {
        "kind": "authority",
        "display_name": "NASA Earth Observatory Natural Event Tracker (EONET)",
        "terms_url": "https://eonet.gsfc.nasa.gov/what-is-eonet",
        "attribution": "NASA Earth Observatory Natural Event Tracker (EONET), public domain",
    },
}

# --------------------------------------------------------------------------- M0 density bar (docs/architecture.md §4)
DENSITY_BAR = {
    "min_events": 40,  # after excluding GDACS wildfires below Orange
    "min_hazard_types": 4,  # hazard types with at least `min_per_group` events
    "min_continents": 4,  # continents with at least `min_per_group` events
    "min_per_group": 3,
    "min_europe_non_wildfire": 3,
}
