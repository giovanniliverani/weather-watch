// Cluster markers: a donut of the hazard mix around the event count, one HTML button per cluster in view.
// Presentation only: MapLibre counts the hazard_type values the API sent; nothing here filters or ranks events.
import type { GeoJSONSourceSpecification } from 'maplibre-gl'
import { hazardName } from './format'
import { HAZARDS, type Ground } from './symbols'

/** Hazards with their own colour; any other hazard_type counts as 'other', as it draws with the 'other' symbol. */
const NAMED = Object.keys(HAZARDS).filter((hazard) => hazard !== 'other')

/** MapLibre clusterProperties: one running count per named hazard, kept on every cluster as hz_<hazard>. */
export const CLUSTER_PROPERTIES: GeoJSONSourceSpecification['clusterProperties'] = Object.fromEntries(
  NAMED.map((hazard) => [`hz_${hazard}`, ['+', ['case', ['==', ['get', 'hazard_type'], hazard], 1, 0]]]),
)

/** How many events of each hazard a cluster holds, most first; ties keep the legend's order. */
export function hazardMix(properties: Record<string, unknown>): [string, number][] {
  const total = Number(properties.point_count) || 0
  const mix: [string, number][] = NAMED.map((hazard) => [hazard, Number(properties[`hz_${hazard}`]) || 0])
  const named = mix.reduce((sum, [, n]) => sum + n, 0)
  mix.push(['other', total - named])
  return mix.filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1])
}

/** "24 events: 18 wildfire, 4 flood, 2 other": the count and the three largest hazards, then the rest together. */
export function clusterLabel(total: number, mix: [string, number][]): string {
  const top = mix.slice(0, 3).map(([hazard, n]) => `${n} ${hazardName(hazard).toLowerCase()}`)
  const rest = mix.slice(3).reduce((sum, [, n]) => sum + n, 0)
  if (rest) top.push(`${rest} more`)
  return `${total} events: ${top.join(', ')}`
}

/** Outer radius in pixels by event count, as the grey discs grew before. */
const radiusFor = (total: number) => (total < 10 ? 15 : total < 50 ? 18 : total < 200 ? 22 : 27)

const SVG = 'http://www.w3.org/2000/svg'

function circle(attributes: Record<string, string | number>): SVGCircleElement {
  const element = document.createElementNS(SVG, 'circle')
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value))
  return element
}

export interface ClusterColours {
  /** The centre disc and the gaps between segments. */
  disc: string
  /** The count and the outer hairline. */
  ink: string
  accent: string
}

/** A cluster as a button: the donut (decorative), the count, and the full mix as its accessible name. */
export function clusterButton(total: number, abbreviated: string, mix: [string, number][], ground: Ground, colours: ClusterColours): HTMLButtonElement {
  const r = radiusFor(total)
  const ring = total < 10 ? 4.5 : 5.5
  const mid = r - ring / 2
  const length = 2 * Math.PI * mid
  const gap = mix.length > 1 ? 1.5 : 0

  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'cluster'
  button.style.setProperty('--cluster-accent', colours.accent)
  button.style.width = button.style.height = `${2 * r}px`
  const label = clusterLabel(total, mix)
  button.setAttribute('aria-label', `${label}. Zoom in`)
  button.title = label

  const svg = document.createElementNS(SVG, 'svg')
  svg.setAttribute('viewBox', `${-r} ${-r} ${2 * r} ${2 * r}`)
  svg.setAttribute('aria-hidden', 'true')
  // The disc colour behind everything shows through the gaps, so neighbouring segments never touch.
  svg.append(circle({ r: r - 0.5, fill: colours.disc, stroke: colours.ink, 'stroke-opacity': 0.55, 'stroke-width': 1 }))
  let start = 0
  for (const [hazard, n] of mix) {
    const span = (n / total) * length
    svg.append(
      circle({
        r: mid,
        fill: 'none',
        stroke: HAZARDS[hazard].colour[ground],
        'stroke-width': ring,
        'stroke-dasharray': `${Math.max(span - gap, 0.5)} ${length}`,
        'stroke-dashoffset': -start,
        transform: 'rotate(-90)',
      }),
    )
    start += span
  }
  const count = document.createElement('span')
  count.textContent = abbreviated
  count.style.color = colours.ink
  button.append(svg, count)
  return button
}
