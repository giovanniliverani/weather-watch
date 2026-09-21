"""Settings for Extreme Weather Watch.

Everything tunable lives here: paths, HTTP identity, feed URLs, hazard mappings, severity
normalisation and the seed rows for the `source` table. Nothing else reads the environment.
Optional overrides come from a `.env` file at the repository root (see `.env.example`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
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
# The tunable numbers (aggregation radius, blocking radii and windows, thresholds, score weights) live in
# identity.yaml at the repository root (EWW_IDENTITY_FILE overrides the path) and are loaded and
# validated here, so the rest of the package keeps reading config.*. Edit the file, then rebuild the
# database from the snapshots to apply the change to existing events (README, "One event, one pin").
IDENTITY_FILE = Path(os.getenv("EWW_IDENTITY_FILE", str(PROJECT_ROOT / "identity.yaml")))


class IdentityConfigError(ValueError):
    """identity.yaml is missing, malformed or names something the pipeline does not know."""


def load_identity(path: Path | None = None) -> dict:
    """Read and validate identity.yaml. Returns plain floats and tuples; fails loudly on a typo."""
    path = Path(path) if path is not None else IDENTITY_FILE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise IdentityConfigError(f"{path} not found; it holds the aggregation radius, blocking radii and thresholds") from exc
    except yaml.YAMLError as exc:
        raise IdentityConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise IdentityConfigError(f"{path}: the file must be a mapping of sections")

    def fail(message: str) -> None:
        raise IdentityConfigError(f"{path}: {message}")

    def number(value, where: str, *, low: float | None = None, high: float | None = None) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(f"{where} must be a number, got {value!r}")
        if low is not None and value < low:
            fail(f"{where} must be at least {low}, got {value!r}")
        if high is not None and value > high:
            fail(f"{where} must be at most {high}, got {value!r}")
        return float(value)

    hazards = set(HAZARD_TYPES) - {"other"}
    radius_raw = (raw.get("aggregation") or {}).get("radius_km")
    if not isinstance(radius_raw, dict) or "default" not in radius_raw:
        fail("aggregation.radius_km must be a mapping with a 'default' entry")
    radius = {}
    for key, value in radius_raw.items():
        if key != "default" and key not in hazards:
            fail(f"aggregation.radius_km: unknown hazard {key!r} (known: {', '.join(sorted(hazards))})")
        radius[key] = number(value, f"aggregation.radius_km.{key}", low=0)
    blocking_section = raw.get("blocking") or {}
    blocking_raw = blocking_section.get("hazards") or {}
    if not isinstance(blocking_raw, dict):
        fail("blocking.hazards must be a mapping of hazard -> {km, days}")
    blocking = {}
    for key, value in blocking_raw.items():
        if key not in hazards:
            fail(f"blocking.hazards: unknown hazard {key!r}")
        if not isinstance(value, dict) or "km" not in value or "days" not in value:
            fail(f"blocking.hazards.{key} needs 'km' and 'days'")
        blocking[key] = (number(value["km"], f"blocking.hazards.{key}.km", low=0), number(value["days"], f"blocking.hazards.{key}.days", low=0))
    named_storm_km = number(blocking_section.get("named_storm_km", 5000), "blocking.named_storm_km", low=0)
    thresholds = raw.get("thresholds") or {}
    auto_merge = number(thresholds.get("auto_merge", 0.90), "thresholds.auto_merge", low=0, high=1)
    proposal = number(thresholds.get("proposal", 0.60), "thresholds.proposal", low=0, high=1)
    if proposal >= auto_merge:
        fail("thresholds.proposal must be below thresholds.auto_merge")
    score = raw.get("score") or {}
    weights_raw = score.get("weights") or {}
    weights = {name: number(weights_raw.get(name), f"score.weights.{name}", low=0, high=1) for name in ("spatial", "temporal", "text")}
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        fail(f"score.weights must sum to 1, got {sum(weights.values()):g}")
    # step 2 of §3, documents -> events (M3); every value has the default the architecture states
    att = raw.get("attachment") or {}
    if not isinstance(att, dict):
        fail("attachment must be a mapping")
    window = att.get("window") or {}
    spatial_raw = att.get("spatial") or {}
    att_weights_raw = att.get("weights") or {"spatial": 0.45, "temporal": 0.25, "text": 0.30}
    att_weights = {name: number(att_weights_raw.get(name), f"attachment.weights.{name}", low=0, high=1) for name in ("spatial", "temporal", "text")}
    if abs(sum(att_weights.values()) - 1.0) > 1e-6:
        fail(f"attachment.weights must sum to 1, got {sum(att_weights.values()):g}")
    no_place = att.get("no_place") or {}
    no_place_weights_raw = no_place.get("weights") or {"temporal": 0.20, "text": 0.80}
    no_place_weights = {name: number(no_place_weights_raw.get(name), f"attachment.no_place.weights.{name}", low=0, high=1) for name in ("temporal", "text")}
    if abs(sum(no_place_weights.values()) - 1.0) > 1e-6:
        fail(f"attachment.no_place.weights must sum to 1, got {sum(no_place_weights.values()):g}")
    att_thresholds = att.get("thresholds") or {}
    attach_threshold = number(att_thresholds.get("attach", 0.75), "attachment.thresholds.attach", low=0, high=1)
    candidate_threshold = number(att_thresholds.get("candidate", 0.55), "attachment.thresholds.candidate", low=0, high=1)
    if candidate_threshold >= attach_threshold:
        fail("attachment.thresholds.candidate must be below attachment.thresholds.attach")
    attachment = {
        "doc_before_days": number(window.get("doc_before_days", 7), "attachment.window.doc_before_days", low=0),
        "doc_after_days": number(window.get("doc_after_days", 1), "attachment.window.doc_after_days", low=0),
        "event_before_days": number(window.get("event_before_days", 2), "attachment.window.event_before_days", low=0),
        "event_after_days": number(window.get("event_after_days", 7), "attachment.window.event_after_days", low=0),
        "decay_days": number(window.get("decay_days", 7), "attachment.window.decay_days", low=0.001),
        "spatial_within_radius": number(spatial_raw.get("within_radius", 1.0), "attachment.spatial.within_radius", low=0, high=1),
        "spatial_within_double_or_country": number(spatial_raw.get("within_double_or_country", 0.5), "attachment.spatial.within_double_or_country", low=0, high=1),
        "weights": att_weights,
        "no_place_weights": no_place_weights,
        "no_place_min_text": number(no_place.get("min_text", 0.60), "attachment.no_place.min_text", low=0, high=1),
        "query_prior": number(att.get("query_prior", 0.10), "attachment.query_prior", low=0, high=1),
        "attach_threshold": attach_threshold,
        "candidate_threshold": candidate_threshold,
        "syndication_cosine": number(att.get("syndication_cosine", 0.95), "attachment.syndication_cosine", low=0, high=1),
    }
    return {
        "path": str(path),
        "aggregation_radius_km": radius,
        "blocking": blocking,
        "named_storm_km": named_storm_km,
        "auto_merge": auto_merge,
        "proposal": proposal,
        "weights": weights,
        "key_equal": number(score.get("key_equal", 1.0), "score.key_equal", low=0, high=1),
        "glide_equal": number(score.get("glide_equal", 1.0), "score.glide_equal", low=0, high=1),
        "storm_name_equal": number(score.get("storm_name_equal", 0.95), "score.storm_name_equal", low=0, high=1),        "attachment": attachment,
    }


IDENTITY = load_identity()
# Two feeds' records share a pin automatically only within this distance (closest pair of positions).
AGGREGATION_RADIUS_KM: dict[str, float] = IDENTITY["aggregation_radius_km"]


def aggregation_radius_km(hazard_type: str | None) -> float:
    return AGGREGATION_RADIUS_KM.get(hazard_type or "", AGGREGATION_RADIUS_KM["default"])


# Blocking radius R (km) and window T (days) per hazard type of the incoming record; 'other' never blocks.
BLOCKING: dict[str, tuple[float, float]] = IDENTITY["blocking"]
# A named storm blocks by name inside its class and window ("names decide", §3): a track's first
# point can be thousands of km from the other feed's current position, so its radius is basin-scale.
STORM_NAME_BLOCK_KM = IDENTITY["named_storm_km"]
AUTO_MERGE_THRESHOLD = IDENTITY["auto_merge"]  # at or above, inside the aggregation radius: merged automatically (reversible)
PROPOSAL_THRESHOLD = IDENTITY["proposal"]  # at or above: create the event and write a merge_proposal for review
SCORE_WEIGHTS = IDENTITY["weights"]
SCORE_KEY_EQUAL = IDENTITY["key_equal"]
SCORE_GLIDE_EQUAL = IDENTITY["glide_equal"]
SCORE_STORM_NAME_EQUAL = IDENTITY["storm_name_equal"]
TITLE_SIMILARITY = os.getenv("EWW_TITLE_SIMILARITY", "jaccard")  # "embedding": cosine of eww.embed vectors (M3); eww.matching.title_similarity is the hook
TITLE_STOPWORDS = {"in", "of", "the", "and", "a", "an", "at", "on", "near", "region", "province", "area", "island"}
# Removed before comparing storm names, so "Tropical Cyclone NORBERT-26" equals "Hurricane Norbert".
STORM_WORDS = {"tropical", "cyclone", "hurricane", "typhoon", "storm", "depression", "severe", "super", "post", "subtropical", "remnants", "of", "in", "the"}
STORM_NAME_YEAR_SUFFIX_RE = re.compile(r"-\d{2}$")
# Whose latest record supplies an event's title, hazard type, centroid and severity label (first source present).
PRIMARY_SOURCE_ORDER = ["gdacs", "copernicus", "eonet"]
EMS_SEVERITY_FLOOR = 0.66  # an event with a Copernicus activation scores at least this
REVIEW_RECENT_MERGES = 50  # rows in the Review tab's merge list

# --------------------------------------------------------------------------- milestone records (docs/m<N>.md)
# One document per milestone, named after the milestone and nothing else: docs/m0.md, docs/m1.md, ...
# Each is written (and rewritten) by the command that measures that milestone, so the numbers in it are
# always measured rather than remembered: `eww report density` -> m0, `eww report volume` -> m1,
# `eww report identity` -> m2, `eww eval attachments` -> m3. A new report writer inherits the rule by
# calling milestone_doc() instead of naming a file.


def milestone_doc(number: int) -> Path:
    """The record of milestone `number`: docs/m<number>.md."""
    return DOCS_DIR / f"m{number}.md"


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
    # enrichment providers (M3): they supply documents, never events
    "gdelt": {
        "kind": "news",
        "display_name": "The GDELT Project, DOC 2.0 API (news headlines and thumbnails)",
        "terms_url": "https://www.gdeltproject.org/about.html#termsofuse",
        "attribution": "Headlines and thumbnail references from the GDELT Project (gdeltproject.org)",
    },
    "reliefweb": {
        "kind": "authority",
        "display_name": "ReliefWeb (UN OCHA), reports",
        "terms_url": "https://reliefweb.int/terms-conditions",
        "attribution": "Reports from ReliefWeb, a service of UN OCHA; personal, non-commercial use",
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

# --------------------------------------------------------------------------- structured provider log (M3)
# One JSON line per external call and per geocode lookup (eww.ratelimit); `eww doctor` reads it back to
# prove the rate limits held. Outside data/ on purpose: data/ holds the SQLite file and model caches.
LOG_DIR = Path(os.getenv("EWW_LOG_DIR", str(PROJECT_ROOT / "logs")))
PROVIDER_LOG = LOG_DIR / "providers.jsonl"
SECRET_PARAMS = {"username", "appname", "key", "api_key", "token"}  # never written to the log in clear

# --------------------------------------------------------------------------- enrichment collectors (M3, laptop only)
ENRICH_SOURCES = ["gdelt", "reliefweb"]  # what `eww sync` and `eww enrich` run, in this order
ENRICH_ACTIVE_DAYS = 14  # events observed (or ended) in the last N days are queried, by severity
ENRICH_MAX_EVENTS_PER_RUN = 40  # per provider per run: 40 GDELT queries at 5 s spacing is about 3.5 minutes
ENRICH_BACKFILL_DAYS = 2  # the first query for an event starts this many days before its start
ENRICH_MIN_SEVERITY = 0.0  # events below this score are never queried (0 = every active event, capped above)

# GDELT DOC 2.0 (https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/, read 2026-09-16). No key; the
# informal limit is one request every 5 seconds, and the API answers 429 (or a plain-text notice with
# status 200) when it is exceeded: on either the run stops calling GDELT. Articles carry url, url_mobile,
# title, seendate (YYYYMMDDTHHMMSSZ), socialimage, domain, language, sourcecountry. The search lookback is
# finite (about three months, verify), so a first query never starts earlier than GDELT_LOOKBACK_DAYS ago.
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MIN_INTERVAL_S = 6.0  # the rule is 5 s; the extra second absorbs clock jitter, because the penalty for a breach is a lockout of hours
GDELT_MAX_RECORDS = 250
GDELT_LOOKBACK_DAYS = 90
GDELT_MAX_PLACE_TERMS = 8
GDELT_MAX_HAZARD_TERMS = 10
GDELT_TIMEOUT_S = 90.0  # the API can take 15 s on a cold query

# ReliefWeb API v2 (https://apidoc.reliefweb.int/, read 2026-09-21): a pre-approved appname is mandatory
# since 2025-11-01 and the API answers 403 without one; 1,000 calls a day; terms: personal, non-commercial.
# Reports are filtered by disaster.glide when the event has a GLIDE number, else country.iso3 plus the
# disaster type names below, and date.original since the last successful run.
RELIEFWEB_APPNAME = os.getenv("RELIEFWEB_APPNAME") or None
RELIEFWEB_REPORTS_URL = "https://api.reliefweb.int/v2/reports"
RELIEFWEB_DAILY_LIMIT = 1000
RELIEFWEB_DAILY_BUDGET = 900  # the run stops before the service's own limit
RELIEFWEB_MIN_INTERVAL_S = 1.0
RELIEFWEB_PAGE_LIMIT = 100
RELIEFWEB_FIELDS = ["title", "url", "date.original", "source.shortname", "disaster.glide", "country.iso3", "body", "language.code"]
RELIEFWEB_DISASTER_TYPES = {
    "flood": ["Flood", "Flash Flood"],
    "tropical_cyclone": ["Tropical Cyclone"],
    "severe_storm": ["Severe Local Storm", "Storm Surge"],
    "wildfire": ["Wild Fire"],
    "heatwave": ["Heat Wave"],
    "coldwave": ["Cold Wave", "Snow Avalanche"],
    "drought": ["Drought"],
    "landslide": ["Land Slide", "Mud Slide"],
    "volcano": ["Volcano"],
    "earthquake": ["Earthquake"],
    "tsunami": ["Tsunami"],
}

# --------------------------------------------------------------------------- documents (M3)
EXCERPT_MAX_CHARS = 2000  # document.text_excerpt and the body kept inside the payload: never an article body
# Query parameters dropped from url_canonical (tracking and share tokens); everything else is kept.
TRACKING_PARAM_PREFIXES = ("utm_", "pk_", "mtm_", "piwik_", "hsa_", "vero_", "oly_", "wt_", "mc_", "ga_")
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "igshid", "yclid", "twclid", "ttclid", "_ga", "_gl",
    "ref", "ref_src", "ref_url", "cmpid", "ocid", "smid", "s_cid", "spm", "ncid", "sr_share", "mkt_tok", "soc_src",
    "soc_trk", "share", "shared", "source", "src", "via", "feature", "trk", "cid", "ito", "ns_mchannel", "ns_campaign",
    "ns_source", "at_medium", "at_campaign", "outputType", "output", "amp", "__twitter_impression", "guccounter",
})
PURGE_UNATTACHED_DAYS = 60  # `eww purge`: unattached documents older than this go, with their extraction and embedding rows
ATTACH_RETRY_DAYS = 14  # a document without any decision is re-scored on every run while younger than this

# --------------------------------------------------------------------------- extraction (M3): lexicon + NER
LEXICON_FILE = Path(os.getenv("EWW_LEXICON_FILE", str(Path(__file__).resolve().parent / "data" / "lexicon.yaml")))
EXTRACT_METHOD = "lexicon+ner"
SPACY_MODELS = {"xx": "xx_ent_wiki_sm", "en": "en_core_web_sm"}  # multilingual for every text, English adds GPE/LOC/FAC
NER_LABELS = {"xx_ent_wiki_sm": ("LOC",), "en_core_web_sm": ("GPE", "LOC", "FAC")}
EXTRACT_MAX_PLACES = 6  # place names geocoded per document, in order of appearance
LEXICON_CONFIDENCE = {"strong": 0.9, "strong_repeated": 1.0, "weak": 0.5, "ambiguous_penalty": 0.2}

# --------------------------------------------------------------------------- geocoding (M3): three tiers behind one interface
# Tier 1: GeoNames dump (CC BY 4.0) loaded once by `eww geonames load`. Tier 2: GeoNames web service, one
# credit per search, 1,000 an hour and 10,000 a day with a free username (read 2026-09-16); the run stays
# under both with the budgets below. Tier 3: public Nominatim, whose usage policy (read 2026-09-21) allows
# scripts run at regular intervals 4 requests a minute, single-threaded, with an identifying User-Agent
# and cached results (ODbL attribution). Every answer, including "not found", is cached in geocode_cache.
GEONAMES_DUMP_URL = "https://download.geonames.org/export/dump/"
GEONAMES_DUMP_FILES = ("cities500.zip", "admin1CodesASCII.txt", "countryInfo.txt")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME") or None
GEONAMES_SEARCH_URL = "http://api.geonames.org/searchJSON"
GEONAMES_MAX_ROWS = 5
GEONAMES_HOURLY_LIMIT = 1000
GEONAMES_HOURLY_BUDGET = 900
GEONAMES_DAILY_LIMIT = 10000
GEONAMES_DAILY_BUDGET = 9000
GEONAMES_MIN_INTERVAL_S = 0.5
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_PER_MINUTE = 4
NOMINATIM_MIN_INTERVAL_S = 1.0
GEOCODE_TIERS = ["gazetteer", "geonames", "nominatim"]  # tried in this order; a tier without credentials is skipped
GAZETTEER_UNHINTED_MIN_POPULATION = 50000  # a name that misses inside the hinted country may match a big place elsewhere
GEOCODE_MIN_NAME_CHARS = 3
GEOCODE_REMOTE_CALLS_PER_RUN = 60  # after this many web-service calls in one run, remaining names stay unresolved (not cached) until the next run
GEOCODER_ATTRIBUTIONS = {
    "gazetteer": "Place names and coordinates from GeoNames (geonames.org), CC BY 4.0",
    "geonames": "GeoNames web service (geonames.org), CC BY 4.0",
    "nominatim": "Geocoding by Nominatim, data © OpenStreetMap contributors, ODbL",
}

# --------------------------------------------------------------------------- embeddings (M3)
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"  # sentence-transformers, 384 dimensions, CPU
EMBED_DIM = 384
EMBED_BATCH_SIZE = 64
EMBED_EXCERPT_CHARS = 400  # of the excerpt, after the title, goes into the vector (the model reads 128 tokens)
MODEL_DIR = Path(os.getenv("EWW_MODEL_DIR", str(DATA_DIR / "models")))  # the model cache lives under data/

# --------------------------------------------------------------------------- attachment (docs/architecture.md §3, step 2)
# The numbers live in identity.yaml (attachment:) next to the identity numbers they belong with.
ATTACHMENT: dict = IDENTITY["attachment"]
ATTACHMENT_SAMPLE_CSV = LABELS_DIR / "attachment_sample.csv"  # `eww eval attachments --sample 100` writes it; fill `correct`
ATTACHMENT_SAMPLE_SIZE = 100
ATTACHMENT_PRECISION_TARGET = 0.90  # M3 exit criterion 2: at least 90 of 100 hand-checked rows correct
COVERAGE_MIN_SEVERITY = 0.66  # M3 exit criterion 1 ...
COVERAGE_MIN_DOCUMENTS = 3  # ... at least this many attached documents ...
COVERAGE_TARGET = 0.50  # ... on at least this share of severe events active in the last ENRICH_ACTIVE_DAYS days
CACHE_HIT_RATE_TARGET = 0.70  # M3 exit criterion 4
DOCTOR_LOG_DAYS = 7  # `eww doctor` reads the provider log this far back by default
