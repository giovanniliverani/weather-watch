// The view lives in the URL query, so a reload or a shared link restores it:
// ?days=14&hazard=flood&hazard=wildfire&sev=0.66&fp=1&event=<id>&map=<zoom>/<lat>/<lon>&page=about
import { DEFAULT_DAYS, MAX_DAYS, MIN_DAYS } from './config'

export interface Filters {
  days: number
  hazards: string[]
  minSeverity: number
  footprints: boolean
}

export interface MapView {
  zoom: number
  lat: number
  lon: number
}

export interface ViewState {
  filters: Filters
  eventId: string | null
  map: MapView | null
  page: 'map' | 'about'
}

function parseDays(raw: string | null): number {
  const days = Number(raw)
  return Number.isInteger(days) && days >= MIN_DAYS && days <= MAX_DAYS ? days : DEFAULT_DAYS
}

function parseSeverity(raw: string | null): number {
  const value = Number(raw)
  // Any score 0..1 is a valid filter (the API checks the range too); the steps offered come from /severity-steps.
  return Number.isFinite(value) && value >= 0 && value <= 1 ? value : 0
}

function parseMap(raw: string | null): MapView | null {
  const parts = raw?.split('/').map(Number)
  if (!parts || parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return null
  const [zoom, lat, lon] = parts
  if (zoom < 0 || zoom > 22 || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null
  return { zoom, lat, lon }
}

export function readViewState(search: string): ViewState {
  const params = new URLSearchParams(search)
  return {
    filters: {
      days: parseDays(params.get('days')),
      hazards: [...new Set(params.getAll('hazard'))].sort(),
      minSeverity: parseSeverity(params.get('sev')),
      footprints: params.get('fp') === '1',
    },
    eventId: params.get('event') || null,
    map: parseMap(params.get('map')),
    page: params.get('page') === 'about' ? 'about' : 'map',
  }
}

export function writeViewState(state: ViewState): string {
  const params = new URLSearchParams()
  const { filters } = state
  if (filters.days !== DEFAULT_DAYS) params.set('days', String(filters.days))
  for (const hazard of filters.hazards) params.append('hazard', hazard)
  if (filters.minSeverity) params.set('sev', String(filters.minSeverity))
  if (filters.footprints) params.set('fp', '1')
  if (state.eventId) params.set('event', state.eventId)
  if (state.map) params.set('map', `${state.map.zoom.toFixed(2)}/${state.map.lat.toFixed(4)}/${state.map.lon.toFixed(4)}`)
  if (state.page === 'about') params.set('page', 'about')
  const query = params.toString()
  return query ? `?${query}` : ''
}
