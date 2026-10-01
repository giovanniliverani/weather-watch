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
- Full-screen map; the basemap is chosen from a list (config.ts BASEMAPS; "Auto" follows the theme: Dark or Positron), and a dark/light theme switch sits in the column header. Both are kept per browser, not in the URL. Filter column on the left: hazards (from GET /hazards), window presets 1/3/7/14/30/90 days (default 14; any 1-90 from the URL), minimum severity any / 0.33 Green / 0.66 Orange-EMS / 1.0 Red, footprints toggle.
- Data age from `meta` in the filter column, with a STALE stamp only from `meta.pipeline_stale`.
- Event panel on the right with tabs Details, News (article, report), Posts (post, video; photos inline from their URL, direct video files play, otherwise a thumbnail that links out). Weather: /forecast current conditions and 5 days, with the Open-Meteo credit.
- About page from /attributions.
- Events as weather-chart symbols in hazard colours (hollow when ended), clustered as circles with counts, selected event ringed; footprints optional; geolocation only on press.
- Accessible events list (title, hazard, severity): selecting a row opens the panel and moves the map there.
- URL holds filters, selected event, centre and zoom; a reload restores them.
- At 375 px the panel is a bottom sheet and the filters fold into a top bar.

## States
Loading (skeleton), API down (say to run `uv run eww serve`), no events match, nothing attached yet, forecast failed (502), unknown event id in the URL (404).

## Ranges
About 650 events in 14 days and 1,000+ in 30 days; mostly Green wildfires. Few events have documents.

## Constraints
No data logic in the frontend. Dependencies: react, react-dom, maplibre-gl only. First load under 600 KB gzipped. WCAG 2.2 AA.

## Direction contract
THESIS: a night-desk hazard chart. The world sits dark and quiet; every event is a weather-chart symbol in its hazard colour, so shape and colour each identify the hazard alone. It refuses the glass-dashboard default: no translucent panels, no glow, no gradients.
OWN-WORLD: dark by default (OpenFreeMap Dark; neutral charcoal surfaces one step above the map's ground, hairline rules between), with a light theme (Positron; white surfaces). Solid surfaces in both. Twelve hazard colours, one set per ground (dark: at least 6:1 and 13 CIEDE2000 apart; light: at least 3.2:1 and 10 apart), each drawn as a disc with a chart glyph (filled = active, hollow ring = ended); the map picks the set from the basemap's ground. One accent (periwinkle on dark, blue on light), used only for interactive and selected states; a red STALE stamp only from meta.pipeline_stale. A ring in the map's ink colour on the selected event. System UI sans, tabular figures for counts and dates.
STORY: the owner sees the world's recent hazards at a glance, knows how fresh the data is, narrows by window, hazard and severity, and opens one event's details, news, posts and weather without leaving the map.
FIRST VIEWPORT: left, a 260 px filter column: app name and theme switch, data age and STALE stamp, window chips 1-90 d, the legend as hazard filter with a symbol and count per type, severity chips, footprints, map style with its credit, "N events · list". Centre, the map framed on the pins, zoom and locate controls top-right, the bottom-right kept free above the credit line for a future add-event control. Right, the 380 px event panel when an event is selected. At 375 px: a top bar (name, short data age, stamp, theme switch, Filters button) and a bottom sheet under the map that folds to its header.
FORM: Giovanni's combination D of the three dealt looks (A survey sheet = assigned, B forecast chart = pick, C dark map = standard), seed d8f0d40b.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Open decisions
- How the build is served: by `eww serve` or by its own process (API base URL in one constant, asset paths relative).
- User-contributed events: a later milestone; the map's bottom-right slot stays free for it, with no write code now.
