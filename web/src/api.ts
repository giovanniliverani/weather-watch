// The only module that talks to `eww serve`. Each function is one GET; errors become ApiError.
import { API_BASE_URL, API_PAGE_ORIGINS } from './config'
import type { Attribution, DocumentItem, EventCollection, Forecast, SeverityStep } from './types'
import type { Filters } from './url'

export class ApiError extends Error {
  readonly status: number | null

  constructor(message: string, status: number | null) {
    super(message)
    this.status = status
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { signal })
  } catch (error) {
    if (signal?.aborted) throw error
    // The API's refusals of a foreign page address carry no CORS headers, so they also land here, not below.
    throw new ApiError(
      `The API is not answering, or it refused this page's address (it answers only ${API_PAGE_ORIGINS.join(' and ')}). Is \`uv run eww serve\` running?`,
      null,
    )
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : response.statusText
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

/** Query string for /events.geojson; limit=0 asks for every event so counts match `eww export --count`. */
export function eventsQuery(filters: Filters): string {
  const params = new URLSearchParams({ since: `${filters.days}d`, min_severity: String(filters.minSeverity), limit: '0' })
  for (const hazard of filters.hazards) params.append('hazard', hazard)
  if (filters.footprints) params.set('include_footprints', 'true')
  return params.toString()
}

export const fetchEvents = (filters: Filters, signal?: AbortSignal) =>
  get<EventCollection>(`/events.geojson?${eventsQuery(filters)}`, signal)

export const fetchHazards = (signal?: AbortSignal) => get<string[]>('/hazards', signal)

export const fetchSeveritySteps = (signal?: AbortSignal) => get<SeverityStep[]>('/severity-steps', signal)

export const fetchAttributions = (signal?: AbortSignal) => get<Attribution[]>('/attributions', signal)

export const fetchDocuments = (eventId: string, signal?: AbortSignal) =>
  get<DocumentItem[]>(`/events/${encodeURIComponent(eventId)}/documents`, signal)

export const fetchForecast = (lat: number, lon: number, signal?: AbortSignal) =>
  get<Forecast>(`/forecast?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`, signal)
