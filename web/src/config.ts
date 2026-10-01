// Viewer settings. Every value a later decision may change lives here once.

/** Where `uv run eww serve` listens. Override with VITE_API_BASE_URL in web/.env.local. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

/** The basemap: an OpenFreeMap vector style (no key; its credit line shows on the map). */
export const BASEMAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/positron'

export const DEFAULT_DAYS = 14
export const MIN_DAYS = 1
export const MAX_DAYS = 90
export const WINDOW_PRESETS = [1, 3, 7, 14, 30, 90] as const

/** Minimum-severity steps offered by the filter, as in app.py. Pins show the API's severity_label. */
export const SEVERITY_STEPS = [
  { value: 0, label: 'Any' },
  { value: 0.33, label: 'Green and up' },
  { value: 0.66, label: 'Orange and up (EMS)' },
  { value: 1, label: 'Red' },
] as const

/** Pin colour per hazard_type; presentation only. Unknown types use OTHER_COLOUR. */
export const HAZARD_COLOURS: Record<string, string> = {
  flood: '#1f77b4',
  tropical_cyclone: '#9467bd',
  severe_storm: '#17becf',
  wildfire: '#d62728',
  heatwave: '#ff7f0e',
  coldwave: '#7fdbff',
  drought: '#bcbd22',
  landslide: '#8c564b',
  volcano: '#e377c2',
  earthquake: '#7f7f7f',
  tsunami: '#2ca02c',
}
export const OTHER_COLOUR = '#000000'
