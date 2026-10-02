import { describe, expect, it } from 'vitest'
import { DEFAULT_DAYS } from './config'
import { readViewState, writeViewState } from './url'

describe('readViewState', () => {
  it('falls back to the defaults for an empty or broken query', () => {
    const state = readViewState('?days=0&sev=2&map=1/2&page=nope')
    expect(state.filters).toEqual({ days: DEFAULT_DAYS, hazards: [], minSeverity: 0, footprints: false })
    expect(state.map).toBeNull()
    expect(state.page).toBe('map')
  })

  it('reads every key, with hazards de-duplicated and sorted', () => {
    const state = readViewState('?days=30&hazard=wildfire&hazard=flood&hazard=flood&sev=0.66&fp=1&event=E1&map=4/-15/28&page=about')
    expect(state).toEqual({
      filters: { days: 30, hazards: ['flood', 'wildfire'], minSeverity: 0.66, footprints: true },
      eventId: 'E1',
      map: { zoom: 4, lat: -15, lon: 28 },
      page: 'about',
    })
  })

  it('refuses a map position off the globe', () => {
    expect(readViewState('?map=3/95/0').map).toBeNull()
    expect(readViewState('?map=3/0/181').map).toBeNull()
  })
})

describe('writeViewState', () => {
  it('leaves defaults out of the URL', () => {
    expect(writeViewState(readViewState(''))).toBe('')
  })

  it('round-trips through readViewState', () => {
    const query = '?days=7&hazard=flood&sev=0.66&fp=1&event=E1&map=4.00%2F-15.0000%2F28.0000'
    expect(writeViewState(readViewState(query))).toBe(query)
  })
})
