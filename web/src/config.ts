// Viewer settings. Every value a later decision may change lives here once; hazard colours live in symbols.ts.

/** Where `uv run eww serve` listens. Override with VITE_API_BASE_URL in web/.env.local. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

/** A basemap the map can draw. `ground` picks the symbol colours that stay readable on it. */
export type Basemap =
  | { id: string; name: string; kind: 'style'; url: string; attribution: string; ground: 'dark' | 'light' }
  /** A raster tile service (for example satellite imagery): its tiles and credit become a one-layer style. */
  | { id: string; name: string; kind: 'raster'; tiles: string[]; tileSize: number; maxzoom: number; attribution: string; ground: 'dark' | 'light' }

const OPENFREEMAP_CREDIT = 'OpenFreeMap © OpenMapTiles Data from OpenStreetMap'

/** NASA GIBS shows the latest complete UTC day, not today's partial one: yesterday in UTC, as YYYY-MM-DD. Before
 *  03:00 UTC yesterday's last passes may still be processing, so it shows the day before. */
const GIBS_DAY = new Date(Date.now() - (new Date().getUTCHours() < 3 ? 48 : 24) * 3600 * 1000).toISOString().slice(0, 10)
const GIBS_DAY_LABEL = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${GIBS_DAY}T00:00:00Z`))
/** NASA GIBS daily true colour (NOAA-20 VIIRS), EPSG:3857 WMTS; its tile matrix set stops at zoom 9. No key. */
const GIBS_TODAY: Basemap = {
  id: 'today',
  name: 'Today from space',
  kind: 'raster',
  tiles: [`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_NOAA20_CorrectedReflectance_TrueColor/default/${GIBS_DAY}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`],
  tileSize: 256,
  maxzoom: 9,
  attribution: `NASA GIBS, VIIRS NOAA-20 true colour, ${GIBS_DAY_LABEL} UTC. We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Science Data and Information System (ESDIS).`,
  ground: 'dark',
}

/** Esri World Imagery through ArcGIS Location Platform; offered only when web/.env.local sets VITE_ESRI_API_KEY.
 *  The key ends up in the built JavaScript, so restrict it to this site's address in the Esri dashboard. */
const ESRI_API_KEY: string | undefined = import.meta.env.VITE_ESRI_API_KEY || undefined
const ESRI_SATELLITE: Basemap | null = ESRI_API_KEY
  ? {
      id: 'satellite',
      name: 'Satellite',
      kind: 'raster',
      tiles: [`https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token=${encodeURIComponent(ESRI_API_KEY)}`],
      tileSize: 256,
      maxzoom: 19,
      attribution: 'Powered by Esri. Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community',
      ground: 'dark',
    }
  : null

/** The basemaps on offer, in menu order. Adding one is one entry here. */
export const BASEMAPS: Basemap[] = [
  { id: 'dark', name: 'Dark', kind: 'style', url: 'https://tiles.openfreemap.org/styles/dark', attribution: OPENFREEMAP_CREDIT, ground: 'dark' },
  { id: 'positron', name: 'Positron', kind: 'style', url: 'https://tiles.openfreemap.org/styles/positron', attribution: OPENFREEMAP_CREDIT, ground: 'light' },
  { id: 'liberty', name: 'Liberty', kind: 'style', url: 'https://tiles.openfreemap.org/styles/liberty', attribution: OPENFREEMAP_CREDIT, ground: 'light' },
  { id: 'bright', name: 'Bright', kind: 'style', url: 'https://tiles.openfreemap.org/styles/bright', attribution: OPENFREEMAP_CREDIT, ground: 'light' },
  { id: 'fiord', name: 'Fiord', kind: 'style', url: 'https://tiles.openfreemap.org/styles/fiord', attribution: OPENFREEMAP_CREDIT, ground: 'dark' },
  ...(ESRI_SATELLITE ? [ESRI_SATELLITE] : []),
  GIBS_TODAY,
]

/** "Auto" follows the page theme with these two. */
export const AUTO_BASEMAP = { dark: 'dark', light: 'positron' } as const

export const DEFAULT_DAYS = 14
export const MIN_DAYS = 1
export const MAX_DAYS = 90
export const WINDOW_PRESETS = [1, 3, 7, 14, 30, 90] as const
