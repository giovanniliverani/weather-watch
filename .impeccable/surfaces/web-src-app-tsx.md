---
version: 1
slug: "web-src-app-tsx"
primary_target: "web/src/App.tsx"
related_targets: ["web/src"]
---

# Map surface

Mode: Operate. Code-first (no image generation on this machine).

## Job and audience
The owner, on a laptop, sees what extreme weather happened worldwide in the last days, then reads one event's story in one click.

## Outcome
- Pin count matches `eww export --count` for the same filters.
- One click from a pin to Details, News, Posts and Weather.
- Freshness (data as of, missed collector runs) always visible.

## Layout and interaction
- Full-screen map. Filters top-left: hazards (from GET /hazards), window presets 1/3/7/14/30/90 days (default 14; any 1-90 from the URL), minimum severity any / 0.33 Green / 0.66 Orange-EMS / 1.0 Red, footprints toggle.
- Status strip from `meta`: data as of, last run, missed runs n of m; red only from `meta.pipeline_stale`.
- Event panel on the right with tabs Details, News (article, report), Posts (post, video; photos inline from their URL, direct video files play, otherwise a thumbnail that links out). Weather: /forecast current conditions and 5 days, with the Open-Meteo credit.
- About page from /attributions.
- Pins coloured by hazard, clustered when zoomed out, selected pin marked; footprints optional; geolocation only on press.
- Accessible events list (title, hazard, severity): selecting a row opens the panel and moves the map there.
- URL holds filters, selected event, centre and zoom; a reload restores them.
- At 375 px the panel is a bottom sheet and the filters fold into a top bar.

## States
Loading (skeleton), API down (say to run `uv run eww serve`), no events match, nothing attached yet, forecast failed (502), unknown event id in the URL (404).

## Ranges
About 650 events in 14 days and 1,000+ in 30 days; mostly Green wildfires. Few events have documents.

## Constraints
No data logic in the frontend. Dependencies: react, react-dom, maplibre-gl only. First load under 600 KB gzipped. WCAG 2.2 AA.

## Open decisions
- Basemap: OpenFreeMap vector style or OpenStreetMap raster (one constant).
- Visual world: survey sheet (assigned, seed d8f0d40b), forecast chart (pick), or the standard dark map. No visual styling until chosen; the direction contract is added here then.
- How the build is served: by `eww serve` or by its own process (API base URL in one constant, asset paths relative).
