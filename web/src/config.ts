// Viewer settings. Every value a later decision may change lives here once; hazard colours live in symbols.ts.

/** Where `uv run eww serve` listens. Override with VITE_API_BASE_URL in web/.env.local. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

/** A basemap the map can draw. `ground` picks the symbol colours that stay readable on it. */
export type Basemap =
  | { id: string; name: string; kind: 'style'; url: string; attribution: string; ground: 'dark' | 'light' }
  /** A raster tile service (for example satellite imagery): its tiles and credit become a one-layer style. */
  | { id: string; name: string; kind: 'raster'; tiles: string[]; tileSize: number; maxzoom: number; attribution: string; ground: 'dark' | 'light' }

const OPENFREEMAP_CREDIT = 'OpenFreeMap © OpenMapTiles Data from OpenStreetMap'

/** The basemaps on offer, in menu order. Adding one is one entry here. */
export const BASEMAPS: Basemap[] = [
  { id: 'dark', name: 'Dark', kind: 'style', url: 'https://tiles.openfreemap.org/styles/dark', attribution: OPENFREEMAP_CREDIT, ground: 'dark' },
  { id: 'positron', name: 'Positron', kind: 'style', url: 'https://tiles.openfreemap.org/styles/positron', attribution: OPENFREEMAP_CREDIT, ground: 'light' },
  { id: 'liberty', name: 'Liberty', kind: 'style', url: 'https://tiles.openfreemap.org/styles/liberty', attribution: OPENFREEMAP_CREDIT, ground: 'light' },
  { id: 'bright', name: 'Bright', kind: 'style', url: 'https://tiles.openfreemap.org/styles/bright', attribution: OPENFREEMAP_CREDIT, ground: 'light' },
  { id: 'fiord', name: 'Fiord', kind: 'style', url: 'https://tiles.openfreemap.org/styles/fiord', attribution: OPENFREEMAP_CREDIT, ground: 'dark' },
]

/** "Auto" follows the page theme with these two. */
export const AUTO_BASEMAP = { dark: 'dark', light: 'positron' } as const

export const DEFAULT_DAYS = 14
export const MIN_DAYS = 1
export const MAX_DAYS = 90
export const WINDOW_PRESETS = [1, 3, 7, 14, 30, 90] as const
