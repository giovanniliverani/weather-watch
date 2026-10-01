// Viewer settings. Every value a later decision may change lives here once; hazard colours live in symbols.ts.

/** Where `uv run eww serve` listens. Override with VITE_API_BASE_URL in web/.env.local. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

/** The basemap: OpenFreeMap's dark vector style (no key; its credit line shows on the map). */
export const BASEMAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/dark'

export const DEFAULT_DAYS = 14
export const MIN_DAYS = 1
export const MAX_DAYS = 90
export const WINDOW_PRESETS = [1, 3, 7, 14, 30, 90] as const

/** Minimum-severity steps offered by the filter, as in app.py. Events show the API's severity_label. */
export const SEVERITY_STEPS = [
  { value: 0, label: 'Any', hint: 'every severity' },
  { value: 0.33, label: 'Green', hint: 'Green and above' },
  { value: 0.66, label: 'Orange', hint: 'Orange and above, or a Copernicus EMS activation' },
  { value: 1, label: 'Red', hint: 'Red only' },
] as const
