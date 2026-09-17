"""Extreme Weather Watch viewer: a Streamlit page with a folium map of events_geojson() and a Review tab.

Imports only eww.api (reads) and eww.review (the write actions behind Accept, Reject and Revert).
No SQL, no HTML, no JavaScript, no CSS: every filter is a Streamlit widget whose value is passed
to events_geojson(), and the sidebar is built from each feature's `properties`, exactly as a
future frontend would build it from the same GeoJSON. Run with `uv run streamlit run app.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import folium
import streamlit as st
from folium import plugins
from streamlit_folium import st_folium

from eww import api, review

DEFAULT_DAYS = 14
SEVERITY_STEPS = [0.0, 0.33, 0.66, 1.0]
SEVERITY_NAMES = {0.0: "any", 0.33: "0.33 (Green)", 0.66: "0.66 (Orange, EMS)", 1.0: "1.0 (Red)"}
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

# ---------------------------------------------------------------- filters (sidebar): widgets only, the API filters
st.sidebar.header("Filters")
days = st.sidebar.slider("Window: days back from now", min_value=1, max_value=90, value=DEFAULT_DAYS)
hazards = st.sidebar.multiselect("Hazard types", options=api.HAZARD_TYPES, default=api.HAZARD_TYPES)
min_severity = st.sidebar.select_slider("Minimum severity", options=SEVERITY_STEPS, value=0.0, format_func=lambda v: SEVERITY_NAMES[v])
footprints = st.sidebar.checkbox("Footprints", value=False, help="Draw the footprint polygons the feeds supply (EONET floods and fires).")
if not hazards:
    st.sidebar.caption("No hazard type selected: every type is shown.")

collection = api.events_geojson(f"{days}d", hazard=hazards or None, min_severity=min_severity, include_footprints=footprints, limit=0)
points = [f for f in collection["features"] if f["geometry"]["type"] == "Point"]
polygons = [f for f in collection["features"] if f["geometry"]["type"] != "Point"]
by_id = {f["properties"]["event_id"]: f for f in points}
meta = collection["meta"]

# ---------------------------------------------------------------- status strip (from `meta` only)
last_run = meta["last_collector_run_at"]
hours_since_run = None
if last_run:
    hours_since_run = (datetime.now(timezone.utc) - datetime.fromisoformat(last_run.replace("Z", "+00:00"))).total_seconds() / 3600
ago = "never" if hours_since_run is None else f"{hours_since_run:.1f} h ago"
strip = (
    f"Data as of {meta['data_as_of'] or 'never'} · last collector run {ago} · "
    f"{meta['missed_runs_7d']} of {meta['expected_runs_7d']} runs missed in 7 days · {len(points)} events shown"
)
stale = hours_since_run is None or hours_since_run > api.STATUS_RED_STALE_HOURS
if meta["missed_runs_7d"] > api.STATUS_RED_MISSED_RUNS or stale:
    st.error(strip)
else:
    st.success(strip)

tab_map, tab_review = st.tabs(["Map", "Review"])

# ---------------------------------------------------------------- map
with tab_map:
    m = folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap", control_scale=True)
    if polygons:
        hazard_of = {f["properties"]["event_id"]: f["properties"]["hazard_type"] for f in points}
        folium.GeoJson(
            {"type": "FeatureCollection", "features": polygons},
            name="footprints",
            style_function=lambda feature: {
                "color": PALETTE.get(hazard_of.get(feature["properties"]["event_id"]), PALETTE["other"]),
                "weight": 1,
                "fillOpacity": 0.15,
            },
            tooltip=folium.GeoJsonTooltip(fields=["event_id", "role", "source_id"], aliases=["event", "role", "source"]),
        ).add_to(m)
    cluster = plugins.MarkerCluster(name="events", options={"disableClusteringAtZoom": 9, "spiderfyOnMaxZoom": True}).add_to(m)
    for feature in points:
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
        ).add_to(cluster)
    plugins.LocateControl(auto_start=False, position="topleft").add_to(m)
    clicked = st_folium(m, returned_objects=["last_object_clicked_tooltip"], use_container_width=True, height=620)

# ---------------------------------------------------------------- review: proposals and merges (eww.review does the writes)
with tab_review:
    totals = review.counts()
    st.caption(
        f"{totals['open_proposals']} open proposals · {totals['accepted_proposals']} accepted · {totals['rejected_proposals']} rejected · "
        f"{totals['merges']} merges, {totals['merges_reverted']} reverted"
    )
    st.subheader("Open merge proposals")
    proposals = review.open_proposals()
    if not proposals:
        st.write("Nothing to review: no proposal scored between the proposal and the auto-merge thresholds.")
    for proposal in proposals:
        with st.container(border=True):
            a, b = proposal["event_a"], proposal["event_b"]
            distance = "?" if proposal["distance_km"] is None else f"{proposal['distance_km']:.0f} km apart"
            apart = "?" if proposal["days_apart"] is None else f"{proposal['days_apart']:.1f} days apart"
            st.markdown(f"**Score {proposal['score']:.2f}** · {distance} · {apart} · title similarity {proposal['text_sim'] if proposal['text_sim'] is not None else '?'} · rule {proposal['rule'] or '?'}")
            if proposal["keys"]:
                cited = ", ".join(f"{source} {external_id}" for source, external_id in proposal["keys"])
                st.caption(f"One feed cites the other's id ({cited}); kept apart because the positions lie beyond the {proposal['aggregation_radius_km']:.0f} km aggregation radius.")
            elif proposal["within_aggregation_radius"] is False:
                st.caption(f"Scored high enough to merge but the positions lie beyond the {proposal['aggregation_radius_km']:.0f} km aggregation radius (identity.yaml).")
            left, right, actions = st.columns([5, 5, 2])
            for column, event, label in ((left, a, "A (newer)"), (right, b, "B (existing)")):
                with column:
                    st.markdown(f"**{label}: {event['title']}**")
                    st.write(f"{event['hazard_type'].replace('_', ' ')} · {event['status']} · started {event['started_at']} · last observed {event['last_observed_at']}")
                    st.write(f"Sources: {', '.join(event['source_ids']) or 'none'} · severity {event['severity_label'] or 'not stated'} · country {event['country_iso3'] or 'not stated'}")
                    st.caption(f"event_id {event['event_id']}")
            with actions:
                if st.button("Accept: one event", key=f"accept-{proposal['proposal_id']}", type="primary", use_container_width=True):
                    review.accept(proposal["proposal_id"])
                    st.rerun()
                if st.button("Reject: keep apart", key=f"reject-{proposal['proposal_id']}", use_container_width=True):
                    review.reject(proposal["proposal_id"])
                    st.rerun()

    st.subheader(f"Last {review.RECENT_LIMIT} merges")
    merges = review.recent_merges()
    if not merges:
        st.write("No merge has happened yet.")
    for entry in merges:
        with st.container(border=True):
            text, action = st.columns([10, 2])
            with text:
                state = "reverted" if entry["reverted"] else "in effect"
                score = "" if entry["score"] is None else f" · score {entry['score']:.2f}"
                st.markdown(f"**{entry['from_title']}** → **{entry['to_title']}** · by {entry['performed_by']} at {entry['performed_at']}{score} · {entry['records_moved']} records moved · {state}")
                st.caption(f"lineage {entry['lineage_id']} · {entry['from_event_id']} → {entry['to_event_id']}" + (f" · rule {entry['rule']}" if entry["rule"] else ""))
            with action:
                if st.button("Revert", key=f"revert-{entry['lineage_id']}", disabled=not entry["revertable"], use_container_width=True):
                    review.revert(entry["lineage_id"])
                    st.rerun()

# ---------------------------------------------------------------- details (sidebar)
st.sidebar.header("Selected event")
selected_id = (clicked or {}).get("last_object_clicked_tooltip")
selected = by_id.get(selected_id) if selected_id else None
if selected is None:
    st.sidebar.write("Click a pin to see its details here.")
else:
    p = selected["properties"]
    st.sidebar.subheader(p["title"])
    if p["ems_activation"]:
        st.sidebar.badge("EMS activation", color="orange")
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
