import { describe, expect, it } from 'vitest'
import { clusterLabel, hazardMix } from './clusters'

// A cluster's properties as MapLibre gives them: point_count plus one hz_<hazard> count per named hazard.
const cluster = (counts: Record<string, number>, total: number) => ({
  point_count: total,
  ...Object.fromEntries(Object.entries(counts).map(([hazard, n]) => [`hz_${hazard}`, n])),
})

describe('hazardMix', () => {
  it('counts what no named hazard claims as other', () => {
    expect(hazardMix(cluster({ wildfire: 3, flood: 1 }, 6))).toEqual([
      ['wildfire', 3],
      ['other', 2],
      ['flood', 1],
    ])
  })

  it('leaves out hazards with no events', () => {
    expect(hazardMix(cluster({ wildfire: 2 }, 2))).toEqual([['wildfire', 2]])
  })

  it('orders ties as the legend does, with other last', () => {
    expect(hazardMix(cluster({ earthquake: 2, flood: 2 }, 6)).map(([hazard]) => hazard)).toEqual(['flood', 'earthquake', 'other'])
  })
})

describe('clusterLabel', () => {
  it('names the count and up to three hazards', () => {
    expect(clusterLabel(24, [['wildfire', 18], ['flood', 4], ['other', 2]])).toBe('24 events: 18 wildfire, 4 flood, 2 other')
  })

  it('folds everything past the third hazard into "n more"', () => {
    const mix = hazardMix(cluster({ wildfire: 5, flood: 4, earthquake: 3, drought: 2, volcano: 1 }, 15))
    expect(clusterLabel(15, mix)).toBe('15 events: 5 wildfire, 4 flood, 3 earthquake, 3 more')
  })

  it('spells multi-word hazards in lower case', () => {
    expect(clusterLabel(2, [['tropical_cyclone', 2]])).toBe('2 events: 2 tropical cyclone')
  })
})
