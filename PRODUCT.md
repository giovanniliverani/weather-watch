# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One user, the owner, on a Windows laptop. They open the map to see at a glance what extreme weather is happening around the world in the last few days, then click one event to read about it. This is personal situational awareness, not emergency operations: nobody dispatches anything from it.

## Product Purpose

Extreme Weather Watch puts recent hazard events (floods, wildfires, storms, earthquakes, volcanoes, droughts and the rest) on one world map. Each event is one pin, with its details, news, posts and the local weather a click away. Success: the owner trusts the map's count and freshness, and reaches any event's story in one click.

## Positioning

One pin per real event, deduplicated across three authoritative feeds (GDACS, NASA EONET, Copernicus EMS) by a reversible identity pipeline, with news, posts and a forecast attached to that pin. Free, private and local: it stores references (links), never article bodies or media.

## Operating Context

- A Python pipeline (`eww sync`) keeps a local SQLite database current; a GitHub Actions collector keeps collecting while the laptop is off.
- The React map in `web/` reads only the local read-only HTTP API (`uv run eww serve`, http://127.0.0.1:8000). Its contract is docs/architecture.md section 2.
- Accepting or rejecting merge proposals stays in the Streamlit Review tab; the React map has no write actions.
- Typical data: a few hundred to about a thousand events in a 14-day window, most of them wildfires and most Green; a handful Orange or Red. Few events have news or posts attached yet.

## Capabilities and Constraints

- Filters: hazard types (the list comes from the API), a window of 1 to 90 days, a minimum severity step. Filters, the selected event and the map position live in the URL.
- A status strip says how fresh the data is and how many collector runs were missed.
- Side panel tabs: Details, News (articles and reports), Posts (posts, photos and videos together), Weather (current conditions and a 5-day forecast from Open-Meteo).
- An About page lists every data source and its credit, from the API.
- The frontend holds no data logic: no filtering, no severity mapping, no merge-pointer following. Severity labels, staleness and the hazard list come from the API.
- Free tiers only (about €25 a month ceiling, currently €0). Map tiles need their credit line shown.
- Open decision: the basemap (OpenFreeMap vector style or OpenStreetMap raster tiles).
- A phone layout must work at 375 px; a hosted phone copy is a separate, optional later milestone.

## Brand Commitments

Name: Extreme Weather Watch. No logo. Voice: plain, factual, short sentences, plain words.

## Evidence on Hand

Real data only, from the local database through the API. No invented events, counts, headlines or quotes; empty states say plainly that nothing is attached.

## Product Principles

1. The map renders; the pipeline decides. Anything that needs judgement belongs in Python behind the API.
2. Trust before decoration: freshness, counts and sources are always visible.
3. One click to the story: from pin to details, news, posts and weather without leaving the map.
4. References, never bytes: media shows from its original URL, and the link is always the fallback.

## Accessibility & Inclusion

WCAG 2.2 AA. Every event reachable without the map (a keyboard and screen-reader list), visible focus, sufficient contrast for hazard colours, and reduced motion respected.
