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

/** A forecast row's date ("2026-10-01") as "Thu 1 Oct". */
export function formatDay(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  return Number.isNaN(date.getTime()) ? isoDate : dayOnly.format(date)
}

export const hazardName = (hazard: string) => {
  const words = hazard.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function measure(value: number | null, unit: string): string {
  return value === null ? 'not reported' : `${value} ${unit}`
}

export const plural = (n: number, word: string) => `${n.toLocaleString('en-GB')} ${word}${n === 1 ? '' : 's'}`
