// Text formatting for display only: dates, hazard names, measurements.

const dateTime = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
})
const dayOnly = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return 'not stated'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : `${dateTime.format(date)} UTC`
}

const shortDateTime = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })

/** The phone's top bar: "22 Sept, 11:28 UTC". */
export function formatShortWhen(iso: string | null | undefined): string {
  if (!iso) return 'not stated'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : `${shortDateTime.format(date)} UTC`
}

/** A forecast row's date ("2026-10-01") as "Thu 1 Oct". */
export function formatDay(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  return Number.isNaN(date.getTime()) ? isoDate : dayOnly.format(date)
}

export const hazardName = (hazard: string) => {
  const words = hazard.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

// Display names for the API's enum values; an unknown value shows as it comes.
const SOURCE_NAMES: Record<string, string> = { gdacs: 'GDACS', eonet: 'NASA EONET', copernicus: 'Copernicus EMS' }
const PRECISION_NAMES: Record<string, string> = {
  exact: 'exact point',
  city: 'city',
  admin1: 'region',
  country: 'country',
  unresolved: 'not located',
}

export const sourceName = (id: string) => SOURCE_NAMES[id] ?? id
export const precisionName = (precision: string) => PRECISION_NAMES[precision] ?? precision
export const statusName = (status: string) => status.charAt(0).toUpperCase() + status.slice(1)

export function measure(value: number | null, unit: string): string {
  return value === null ? 'not reported' : `${value} ${unit}`
}

export const plural = (n: number, word: string) => `${n.toLocaleString('en-GB')} ${word}${n === 1 ? '' : 's'}`
