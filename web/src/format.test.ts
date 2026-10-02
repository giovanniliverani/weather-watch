import { describe, expect, it } from 'vitest'
import { formatDay, formatWhen, hazardName, measure, plural, precisionName, sourceName } from './format'
import { severityMark } from './symbols'

describe('format', () => {
  it('shows times in UTC and passes unreadable values through', () => {
    expect(formatWhen('2026-09-23T08:20:00Z')).toBe('23 Sept 2026, 08:20 UTC')
    expect(formatWhen(null)).toBe('not stated')
    expect(formatWhen('soon')).toBe('soon')
    expect(formatDay('2026-10-01')).toBe('Thu 1 Oct')
  })

  it('names hazards, sources and precision for people', () => {
    expect(hazardName('tropical_cyclone')).toBe('Tropical cyclone')
    expect(sourceName('eonet')).toBe('NASA EONET')
    expect(sourceName('newfeed')).toBe('newfeed')
    expect(precisionName('admin1')).toBe('region')
  })

  it('counts and measures', () => {
    expect(plural(1, 'event')).toBe('1 event')
    expect(plural(1048, 'event')).toBe('1,048 events')
    expect(measure(null, 'mm')).toBe('not reported')
    expect(measure(3.5, 'mm')).toBe('3.5 mm')
  })
})

describe('severityMark', () => {
  it('rings only the Orange and Red bands', () => {
    expect(['Red', 'Orange', 'Green', null].map(severityMark)).toEqual(['red', 'orange', 'none', 'none'])
  })
})
