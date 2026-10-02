---
name: Extreme Weather Watch
description: A night-desk hazard chart; recent extreme weather as weather-chart symbols on one world map.
colors:
  ground: "#0c0c0c"
  surface: "#171717"
  raise: "#212121"
  rule: "#2f2f2f"
  text: "#e8e8e8"
  muted: "#a8a8a8"
  accent: "#8ab4ff"
  accent-ink: "#0c1424"
  alarm: "#ff7a7a"
  light-ground: "#f2f3f0"
  light-surface: "#ffffff"
  light-raise: "#f0f0f0"
  light-rule: "#d6d6d6"
  light-text: "#1b1b1b"
  light-muted: "#5c5c5c"
  light-accent: "#2357c6"
  light-accent-ink: "#ffffff"
  light-alarm: "#c62828"
  symbol-ink: "#101317"
  hollow-fill: "#101317"
  light-hollow-fill: "#ffffff"
  hazard-flood: "#38c6f4"
  hazard-tropical-cyclone: "#ff6fb5"
  hazard-severe-storm: "#c39bff"
  hazard-wildfire: "#ff8a3d"
  hazard-heatwave: "#ffe14a"
  hazard-coldwave: "#a5f3fc"
  hazard-drought: "#a9b84e"
  hazard-landslide: "#c4925f"
  hazard-volcano: "#ff5c57"
  hazard-earthquake: "#3ddc97"
  hazard-tsunami: "#1fb5a5"
  hazard-other: "#8e959d"
  light-hazard-flood: "#0090bc"
  light-hazard-tropical-cyclone: "#de5098"
  light-hazard-severe-storm: "#9974d4"
  light-hazard-wildfire: "#d6681a"
  light-hazard-heatwave: "#998600"
  light-hazard-coldwave: "#43929b"
  light-hazard-drought: "#7d8d23"
  light-hazard-landslide: "#ad7d4b"
  light-hazard-volcano: "#ef4d4b"
  light-hazard-earthquake: "#009b5c"
  light-hazard-tsunami: "#009788"
  light-hazard-other: "#818890"
typography:
  headline:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.3rem"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  title:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.07rem"
    fontWeight: 650
    lineHeight: 1.45
    letterSpacing: "-0.005em"
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.87rem"
    fontWeight: 650
    lineHeight: 1.45
  hint:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.87rem"
    fontWeight: 400
    lineHeight: 1.45
  meta:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
rounded:
  sm: "3px"
  md: "6px"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "0.75rem"
  lg: "1rem"
components:
  column:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    width: "260px"
    padding: "1rem"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    width: "380px"
    padding: "1rem"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    typography: "{typography.hint}"
    rounded: "{rounded.md}"
    padding: "0.3rem 0"
  chip-hover:
    backgroundColor: "{colors.raise}"
  chip-selected:
    backgroundColor: "color-mix(in srgb, #8ab4ff 22%, #171717)"
    textColor: "{colors.text}"
  select:
    backgroundColor: "{colors.raise}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0.4rem 2rem 0.4rem 0.5rem"
  list-toggle:
    backgroundColor: "{colors.raise}"
    textColor: "{colors.accent}"
    rounded: "{rounded.md}"
    padding: "0.45rem 0.6rem"
  icon-button:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    size: "2rem"
  icon-button-hover:
    backgroundColor: "{colors.raise}"
    textColor: "{colors.text}"
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    padding: "0.4rem 0.5rem"
  tab-selected:
    textColor: "{colors.text}"
  event-row:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0.4rem 0.3rem"
  event-row-current:
    backgroundColor: "color-mix(in srgb, #8ab4ff 18%, #171717)"
  stamp:
    backgroundColor: "transparent"
    textColor: "{colors.alarm}"
    typography: "{typography.meta}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.45rem"
  badge:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    typography: "{typography.meta}"
    rounded: "{rounded.sm}"
    padding: "0.1rem 0.5rem"
  cluster-disc:
    backgroundColor: "{colors.raise}"
    textColor: "{colors.text}"
---

# Design System: Extreme Weather Watch

## Overview

**Creative North Star: "The Night-Desk Hazard Chart"**

A dark, quiet world map with every event drawn as a weather-chart symbol in its hazard colour. Shape and colour each tell the hazards apart on their own, so the map reads at a glance and still reads for someone who cannot tell two colours apart. Everything around the map is a plain, solid frame: a filter column on the left, an event panel on the right, hairline rules between them.

Dark is the default; a light theme swaps the same roles onto white surfaces over a warm grey map. The basemap can be chosen separately, and the map's symbols, clusters and selection ring follow the basemap's own ground (dark or light), not the page theme. Density is that of a tool, not a page: small type, tight rows, figures in fixed-width digits.

The frontend draws and never decides. Severity bands (hence the rings), staleness (the STALE stamp), country names and the hazard list all come from the API; the design only gives them a shape.

**Key Characteristics:**
- Solid surfaces only; no shadows, glow, gradients or see-through panels.
- Twelve hazard symbols: a disc in the hazard colour with a dark chart glyph; hollow when the event has ended.
- One accent, for what can be pressed or is selected.
- One red, for the STALE stamp and errors.
- System sans throughout, fixed-width digits for counts, dates and temperatures.

## Colors

Neutral charcoal (or white) surfaces that step one shade from the map's ground, one blue accent, one alarm red, and twelve saturated hazard colours that carry all the meaning on the map.

### Primary
- **Periwinkle Signal** (accent; light theme: Chart Blue): links, the selected chip's border and tint, the selected tab's underline, the events-list toggle text, the focus ring, text selection. Accent Ink is the text colour on a solid accent fill.

### Neutral
- **Basemap Black / Warm Map Grey** (ground, light-ground): the page and the map's own background.
- **Charcoal Desk / White Sheet** (surface, light-surface): the filter column, the event panel, the map controls and the tile credit.
- **Raised Charcoal / Soft Grey** (raise, light-raise): chips and rows on hover, the select box, the list toggle, the centre of cluster donuts on a dark map.
- **Hairline** (rule, light-rule): 1 px borders between column, map and panel, chip and select outlines, table rows, tab baseline.
- **Chart Ink** (text, light-text): body text; also the ink of severity rings, cluster outlines, counts, footprints and the selection ring on that ground.
- **Pencil Grey** (muted, light-muted): hints, counts, metadata, table headers, inactive tabs and icon buttons.
- **Alarm Red** (alarm, light-alarm): the STALE stamp and error messages, nothing else.

### Hazard colours
One colour per hazard per ground: flood, tropical cyclone, severe storm, wildfire, heatwave, coldwave, drought, landslide, volcano, earthquake, tsunami, other. The dark set is each at least 6:1 on #0c0c0c, and every pair, the accent and the selection ring included, is at least 13.3 apart in CIEDE2000. The light set is each at least 3.2:1 on #f2f3f0 and on white, and every pair is at least 10.5 apart. Glyphs on a filled disc use Symbol Ink; a hollow disc's centre is the ground it sits on (Hollow Fill).

### Named Rules
**The One Accent Rule.** The accent marks only what can be pressed or what is selected. It never decorates, never fills a panel, never colours a heading.

**The Ground Decides Rule.** A symbol takes the colour set of what it sits on: on the map, the basemap's ground; in the column and panel, the page theme. A new basemap declares its ground, and that is all it needs.

**The Measured Palette Rule.** A new hazard colour joins only if it meets the same floors as the rest (dark: 6:1 and 13.3 CIEDE2000 from every other colour, accent and selection ring included; light: 3.2:1 on #f2f3f0 and white, 10.5 from every other). If it cannot, the hazard uses Other.

**The Alarm Is Data Rule.** Red appears only when the API says something is wrong: the STALE stamp from the pipeline's stale flag, and error text. Severity is never shown in red; it is shown as rings.

## Typography

**Body Font:** system-ui (with -apple-system, Segoe UI, Roboto, sans-serif)

**Character:** The operating system's own sans, at a 15 px base, so the frame feels like a native tool and gives way to the map. There is no display role: the largest text is the event title in the panel.

### Hierarchy
- **Headline** (650, 1.3rem, 1.25): the selected event's title in the panel.
- **Title** (650, 1.07rem): the app name in the column header (0.95rem on a phone). The About page heading is 1.4rem.
- **Body** (400, 15 px, 1.45): facts, summaries (at most 65 characters wide), document titles. About page text is at most 70 characters wide.
- **Label** (650, 0.87rem): filter group names (Window, Hazards, Severity, Map style), selected chips, selected tab.
- **Hint** (400, 0.87rem, muted): chip text, legend counts, hints, data age.
- **Meta** (400, 0.8rem, muted): event-list second lines, tab counts, forecast headers, credits. The STALE stamp uses this size at 650, upper case, 0.04em tracking.
- The current temperature on the Weather tab is the one large figure (650, 1.6rem).

### Named Rules
**The Tabular Figures Rule.** Every number a reader compares (counts, dates, data age, chips, facts, forecast, temperature) uses fixed-width digits.

**The Two Weights Rule.** Text is regular (400) or emphasised (650). There is no light weight and no third emphasis.

## Layout

A full-height three-part grid: a 260 px filter column, the map filling the rest, and a 380 px event panel that appears only when an event is selected. The column scrolls on its own; the map never scrolls. Spacing is in rem on a 15 px base: 0.25rem between chips, 0.5rem between a symbol and its text, 1rem padding in the column and the panel, about 1.1rem between filter groups.

The map's zoom and locate controls sit top-right. The bottom-right corner is kept free above the tile credit for a later add-event control.

At 720 px and below the grid stacks: the column becomes a top bar (name, short data age, stamp, theme switch, Filters button) whose filters open as a drawer over the map (at most 72% of the screen height); the panel becomes a bottom sheet under the map (at most 55%) that folds to its header. Touch targets grow to at least 40 px.

## Elevation & Depth

Flat. Depth comes from tone (ground, then surface, then raise) and 1 px hairlines, never from shadows; MapLibre's own control shadows are removed. The only see-through things are on the map itself: footprint areas (8% fill, dashed outline) and hidden legend rows (45% opacity).

### Named Rules
**The Solid Surfaces Rule.** Every panel, drawer, sheet, control and credit line is opaque. No shadows, no blur, no glow, no gradients.

## Shapes

Gently rounded rectangles (6 px) for everything that can be pressed or hovered: chips, select, list toggle, rows, icon buttons, media. Small status marks (the STALE stamp, the EMS badge) and the focus ring use a tighter 3 px corner. The only circles are on the map: hazard discs, severity rings, cluster donuts and the selection ring. Interface icons (chevron, sun, moon, close) are inline SVG line drawings at 1.5 to 1.75 stroke, taking the text colour of their button.

## Components

### Hazard symbol (signature)
The system's core. A 24-unit disc (radius 10.5) in the hazard colour with a dark chart glyph drawn at 1.9 stroke: waves for flood, an eye with two spiral arms for tropical cyclone, a lightning bolt for severe storm, a flame for wildfire, a sun for heatwave, a six-armed star for coldwave, cracked ground for drought, a slope with falling blocks for landslide, a cone with ash for volcano, a seismograph trace for earthquake, a breaking wave for tsunami, an exclamation mark for other. The same drawing serves the map (a canvas image per hazard, state, severity and ground) and the page (inline SVG).
- **Active:** filled disc, 1 px Symbol Ink outline, Symbol Ink glyph.
- **Ended:** hollow disc, 2 px outline and glyph in the hazard colour, centre in Hollow Fill.
- **Severity:** rings outside the disc in that ground's text colour, 1.4 stroke: one ring (radius 13.4) for the Orange band, two (13.4 and 15.4) for the Red band, none otherwise. The mark follows the API's severity_band exactly; the panel still shows the source's own severity label.
- **On the map:** the whole 32-unit box at about 29 px; active events above ended ones, more severe above less severe.

### Map chrome
- **Clusters:** a donut of the hazard mix around the count: one segment per hazard in that ground's hazard colour, sized by its share, 1.5 px gaps in the centre disc's colour; the centre disc in Raised Charcoal (dark ground) or white (light ground) with a 1 px outline in that ground's text colour at 55%, the count in 12 px semibold tabular figures in that text colour. Outer radius steps with the count (15, 18, 22, 27), ring 4.5 px under 10 events and 5.5 px above. Each cluster is a button named with its count and largest hazards ("24 events: 18 wildfire, 4 flood, 2 other"); focus shows that ground's accent. Events cluster within 28 px up to zoom 4 and stand alone beyond it. Segments keep at least 5.3:1 (dark) and 3.5:1 (light) against the centre disc.
- **Selection:** a 2.5 px ring in that ground's text colour at radius 18, outside the widest severity ring.
- **Footprints:** that ground's text colour at 8% fill with a dashed 1 px outline at 55%.
- **Controls and credit:** on Charcoal Desk with a hairline border, no shadow; the tile credit is always spelled out.

### Chips
- **Style:** an equal-width grid of 1 px hairline-outlined cells, 6 px corners, hint-size tabular text.
- **State:** hover fills Raised Charcoal; selected takes the accent border, a 22% accent tint and label weight. They are real radio inputs, so focus shows the accent ring. Background and border change over 150 ms.

### Legend rows (the hazard filter)
Checkbox, symbol, hazard name, count. Hover fills Raised Charcoal; a hazard filtered out fades to 45% and its count reads "off". Below the list, a small key shows how ended, Orange and Red read.

### Buttons
- **List toggle:** Raised Charcoal, hairline border, accent text, chevron that turns 180 degrees when open; hover adds a 12% accent tint.
- **Icon button:** 2rem square (40 px on a phone), no fill, muted icon; hover fills Raised Charcoal and the icon takes text colour.
- **Text link:** accent, underlined, used for "Show all" and "About and credits".

### Inputs / Fields
- **Select:** Raised Charcoal fill, hairline border, 6 px corners, a muted chevron in place of the native arrow, the basemap credit beneath it in meta size.

### Navigation (panel tabs)
Muted text on a hairline baseline; hover takes text colour; selected takes text colour, label weight and a 2 px accent underline. Tab counts sit beside the name in meta size.

### Event list
Rows of symbol plus title, with hazard, severity label and "ended" on a muted second line. Hover fills Raised Charcoal; the selected event gets an 18% accent tint.

### Status marks
- **STALE stamp:** a 1.5 px Alarm Red outline, Alarm Red upper-case text, 3 px corners; shown only when the API flags the pipeline stale.
- **Badge:** a 1 px muted outline, 3 px corners, meta size at 650, for "Copernicus EMS activation".

## Do's and Don'ts

### Do:
- **Do** draw every event with its hazard symbol, filled when active and hollow when ended, with severity rings only from the API's Orange or Red label.
- **Do** pick symbol, cluster and selection colours from the ground the symbol sits on: the basemap's ground on the map, the page theme elsewhere.
- **Do** keep every text colour at WCAG AA or better in both themes; the built system's lowest is 5.08:1 (light) and 5.32:1 (dark), at desktop width and at 375 px.
- **Do** keep the tile credit spelled out on a solid surface, and keep the map's bottom-right corner free.
- **Do** use fixed-width digits for every count, date and measurement.
- **Do** keep the frontend free of data logic: labels, staleness, country names and the hazard list arrive from the API and are shown as given.

### Don't:
- **Don't** use shadows, blur, glow, gradients or see-through panels.
- **Don't** use the accent for anything that cannot be pressed or is not selected.
- **Don't** show severity in red, or derive it from a score in the frontend; red belongs to the STALE stamp and errors.
- **Don't** add a hazard colour that misses the measured floors, or a hazard symbol without a glyph of its own.
- **Don't** use emoji or font glyphs as icons; draw them as inline SVG in the text colour.
