import { expect, it } from 'vitest'
import { eventsQuery } from './api'

it('asks /events.geojson for every event in the window, as tests/test_serve.py expects', () => {
  const query = eventsQuery({ days: 14, hazards: ['flood', 'wildfire'], minSeverity: 0, footprints: true })
  expect(query).toBe('since=14d&min_severity=0&limit=0&hazard=flood&hazard=wildfire&include_footprints=true')
})

it('leaves footprints out unless asked', () => {
  expect(eventsQuery({ days: 7, hazards: [], minSeverity: 0.66, footprints: false })).toBe('since=7d&min_severity=0.66&limit=0')
})
