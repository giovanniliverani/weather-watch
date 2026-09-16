"""Extreme Weather Watch viewer: a Streamlit page with a folium map of events_geojson().

Imports only eww.api. No SQL, no HTML, no JavaScript, no CSS: the sidebar is built from each
feature's `properties`, exactly as a future frontend would build it from the same GeoJSON.
Run with `uv run streamlit run app.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import folium
import streamlit as st
from streamlit_folium import st_folium

from eww import api

DEFAULT_DAYS = 14
PALETTE = {
    "flood": "#1f77b4",
    "tropical_cyclone": "#9467bd",
    "severe_storm": "#17becf",
    "wildfire": "#d62728",
    "heatwave": "#ff7f0e",
    "coldwave": "#7fdbff",
    "drought": "#bcbd22",
    "landslide": "#8c564b",
    "volcano": "#e377c2",
    "earthquake": "#7f7f7f",
    "tsunami": "#2ca02c",
    "other": "#000000",
}

st.set_page_config(page_title="Extreme Weather Watch", layout="wide")
st.title("Extreme Weather Watch")

# ---------------------------------------------------------------- filters (sidebar)
today = datetime.now(timezone.utc).date()
st.sidebar.header("Filters")
picked = st.sidebar.date_input(
    "Observed between",
    value=(today - timedelta(days=DEFAULT_DAYS), today),
    max_value=today,
)
if isinstance(picked, tuple) and len(picked) == 2:
    start_date, end_date = picked
elif isinstance(picked, tuple) and len(picked) == 1:
    start_date, end_date = picked[0], today
else:
    start_date, end_date = picked, today
hazards = st.sidebar.multiselect("Hazard types", options=api.HAZARD_TYPES, default=api.HAZARD_TYPES)

since = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
until = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
collection = api.events_geojson(since, until, hazard=hazards or None, limit=0)
features = collection["features"]
by_id = {f["properties"]["event_id"]: f for f in features}
meta = collection["meta"]

# ---------------------------------------------------------------- status strip (from `meta` only)
last_run = meta["last_collector_run_at"]
hours_since_run = None
if last_run:
    hours_since_run = (datetime.now(timezone.utc) - datetime.fromisoformat(last_run.replace("Z", "+00:00"))).total_seconds() / 3600
ago = "never" if hours_since_run is None else f"{hours_since_run:.1f} h ago"
strip = (
    f"Data as of {meta['data_as_of'] or 'never'} · last collector run {ago} · "
    f"{meta['missed_runs_7d']} of {meta['expected_runs_7d']} runs missed in 7 days · {len(features)} events shown"
)
stale = hours_since_run is None or hours_since_run > api.STATUS_RED_STALE_HOURS
if meta["missed_runs_7d"] > api.STATUS_RED_MISSED_RUNS or stale:
    st.error(strip)
else:
    st.success(strip)

# ---------------------------------------------------------------- map
m = folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap", control_scale=True)
for feature in features:
    lon, lat = feature["geometry"]["coordinates"]
    props = feature["properties"]
    folium.CircleMarker(
        location=[lat, lon],
        radius=6,
        color=PALETTE.get(props["hazard_type"], PALETTE["other"]),
        fill=True,
        fill_opacity=0.8,
        weight=1,
        tooltip=props["event_id"],
    ).add_to(m)

clicked = st_folium(m, returned_objects=["last_object_clicked_tooltip"], use_container_width=True, height=620)

# ---------------------------------------------------------------- details (sidebar)
st.sidebar.header("Selected event")
selected_id = (clicked or {}).get("last_object_clicked_tooltip")
selected = by_id.get(selected_id) if selected_id else None
if selected is None:
    st.sidebar.write("Click a pin to see its details here.")
else:
    p = selected["properties"]
    st.sidebar.subheader(p["title"])
    st.sidebar.write(f"Hazard: {p['hazard_type'].replace('_', ' ')}")
    st.sidebar.write(f"Status: {p['status']}")
    st.sidebar.write(f"Started: {p['started_at']}")
    st.sidebar.write(f"Ended: {p['ended_at'] or 'ongoing'}")
    st.sidebar.write(f"Last observed: {p['last_observed_at']}")
    st.sidebar.write(f"Severity: {p['severity_label'] or 'not stated'} (score {p['severity_score']})")
    st.sidebar.write(f"Country: {p['country_iso3'] or 'not stated'}")
    st.sidebar.write(f"Sources: {', '.join(p['source_ids'])}")
    if p["glide_number"]:
        st.sidebar.write(f"GLIDE: {p['glide_number']}")
    if p["detail_url"]:
        st.sidebar.link_button("Open the source's page", p["detail_url"])
    st.sidebar.caption(f"event_id {p['event_id']}")

st.sidebar.header("Legend")
for hazard in api.HAZARD_TYPES:
    st.sidebar.write(f"{hazard.replace('_', ' ')}: {PALETTE[hazard]}")
