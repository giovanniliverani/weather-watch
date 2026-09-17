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
SPINE_SOURCES = ["gdacs", "eonet", "copernicus"]  # `eww collect --all-spine`, what GitHub Actions runs
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
# Tie-break inside a band. In the SEARCH API `alertscore` is quantised to 1/2/3 (one value per
# band, observed 2026-09-17 over 2,405 records), so the continuous `episodealertscore` (0 to 2.5
# observed) breaks ties, and `alertscore` only when the episode score is missing. The increment
# never reaches the next band: Green stays in [0.33, 0.36), Orange in [0.66, 0.69), Red is 1.0.
GDACS_ALERTSCORE_MAX = 3.0
GDACS_TIEBREAK_SPAN = 0.03

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
EONET_SEVERITY = 0.4  # a geometry entry without a magnitude
# magnitudeValue scaled per unit: piecewise-linear through these (value, score) points, clamped to
# [EONET_SEVERITY_FLOOR, 1.0]. Knots follow the Saffir-Simpson steps (34 kt tropical storm, 64 kt
# hurricane, 96 kt major hurricane). Hectares put 5,000 ha, the smallest wildfire GDACS mirrors
# into EONET (observed minimum 5,001 ha on 2026-09-17), at Green. Acres convert to hectares first.
EONET_SEVERITY_FLOOR = 0.1
EONET_MAGNITUDE_SCALE = {
    "kts": [(0.0, 0.1), (34.0, 0.33), (64.0, 0.66), (96.0, 1.0)],
    "hectare": [(0.0, 0.1), (5000.0, 0.33), (30000.0, 0.66), (100000.0, 1.0)],
}
EONET_UNIT_TO_HECTARE = {"acres": 0.404686, "acre": 0.404686, "ha": 1.0, "hectares": 1.0}
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

# --------------------------------------------------------------------------- Copernicus EMS
# Rapid Mapping activations, read 2026-09-17: GET public-activations-info/ pages with `limit` and
# `offset` (count, next, previous, results); 265 activations back to 2023-03 in 3 pages of 100.
# Item fields: code, name, category, countries (names), centroid (WKT "POINT (lon lat)"),
# eventTime, activationTime, lastUpdate (naive UTC), closed (bool), gdacsId ("FL1104124": GDACS
# type code + eventid, set on 76 of 265), n_aois, n_products. No date filter, so fetch() pages
# through the whole list and keeps what falls in the window or is still open.
COPERNICUS_ACTIVATIONS_URL = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/"
COPERNICUS_PAGE_SIZE = 100
COPERNICUS_MAX_PAGES = 50  # safety stop; 3 pages cover the whole public list today
COPERNICUS_ACTIVATION_URL = "https://rapidmapping.emergency.copernicus.eu/{code}"
COPERNICUS_ATTRIBUTION = "Copernicus Emergency Management Service (© {year} European Union), {code}"
COPERNICUS_HAZARD = {  # categories observed 2026-09-17, plus the plausible ones marked "not seen"
    "Wildfire": "wildfire",
    "Flood": "flood",
    "Storm": "severe_storm",  # tropical_cyclone when the name matches STORM_TITLE_RE
    "Earthquake": "earthquake",
    "Mass movement": "landslide",
    "Volcanic activity": "volcano",
    "Drought": "drought",  # not seen
    "Tsunami": "tsunami",  # not seen
}
# Activations that are not natural hazards yield no source_record; the raw item stays in the snapshot.
COPERNICUS_SKIP_CATEGORIES = {"Other", "Transport accident", "Industrial accident"}
STORM_TITLE_RE = EONET_STORM_TITLE_RE

# --------------------------------------------------------------------------- identity (docs/architecture.md §3)
# Unresolved records resolve in this source order, so the feed other feeds point at (GDACS) exists first.
RESOLVE_SOURCE_ORDER = ["gdacs", "eonet", "copernicus"]
# Cross-source blocking happens inside a hazard class; the two storm types share one, 'other' never blocks.
HAZARD_CLASS = {h: h for h in HAZARD_TYPES if h != "other"} | {"tropical_cyclone": "storm", "severe_storm": "storm"}
# Blocking radius R (km) and window T (days) per hazard type of the incoming record.
BLOCKING = {
    "tropical_cyclone": (500.0, 10.0),
    "flood": (250.0, 5.0),
    "wildfire": (100.0, 7.0),
    "severe_storm": (200.0, 3.0),
    "drought": (500.0, 30.0),
    "heatwave": (500.0, 10.0),
    "coldwave": (500.0, 10.0),
    "landslide": (50.0, 3.0),
    "volcano": (50.0, 30.0),
    "earthquake": (150.0, 2.0),
    "tsunami": (500.0, 2.0),
}
# A named storm blocks by name inside its class and window ("names decide", §3): a track's first
# point can be thousands of km from the other feed's current position, so its radius is basin-scale.
# Unnamed storms and every other hazard block with the radius above.
STORM_NAME_BLOCK_KM = 5000.0
AUTO_MERGE_THRESHOLD = 0.90  # at or above: merge automatically (logged in event_lineage, reversible)
PROPOSAL_THRESHOLD = 0.60  # at or above: create the event and write a merge_proposal for review
SCORE_WEIGHTS = {"spatial": 0.5, "temporal": 0.3, "text": 0.2}
SCORE_GLIDE_EQUAL = 1.0
SCORE_STORM_NAME_EQUAL = 0.95
TITLE_SIMILARITY = "jaccard"  # M3 adds "embedding"; eww.matching.title_similarity is the hook
TITLE_STOPWORDS = {"in", "of", "the", "and", "a", "an", "at", "on", "near", "region", "province", "area", "island"}
# Removed before comparing storm names, so "Tropical Cyclone NORBERT-26" equals "Hurricane Norbert".
STORM_WORDS = {"tropical", "cyclone", "hurricane", "typhoon", "storm", "depression", "severe", "super", "post", "subtropical", "remnants", "of", "in", "the"}
STORM_NAME_YEAR_SUFFIX_RE = re.compile(r"-\d{2}$")
# Whose latest record supplies an event's title, hazard type, centroid and severity label (first source present).
PRIMARY_SOURCE_ORDER = ["gdacs", "copernicus", "eonet"]
EMS_SEVERITY_FLOOR = 0.66  # an event with a Copernicus activation scores at least this
REVIEW_RECENT_MERGES = 50  # rows in the Review tab's merge list

# --------------------------------------------------------------------------- labels (M2 exit criteria 1 and 2)
LABELS_DIR = DATA_DIR / "labels"
LABEL_CANDIDATES_CSV = LABELS_DIR / "merge_candidates.csv"  # written by `eww labels candidates`
LABEL_PAIRS_CSV = LABELS_DIR / "merge_pairs.csv"  # the same rows with `same_event` filled by hand

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
    "copernicus": {
        "kind": "authority",
        "display_name": "Copernicus Emergency Management Service (CEMS), Rapid Mapping activations",
        "terms_url": "https://www.copernicus.eu/en/access-data/copyright-and-licences",
        "attribution": "Copernicus Emergency Management Service (© European Union)",  # per activation: COPERNICUS_ATTRIBUTION
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
