// Hazard symbols, after weather-chart conventions, drawn once and used by both the map icons and the legend.
// Each is a disc in the hazard colour with a dark glyph (active) or a coloured ring and glyph (ended).
// Presentation only: which hazard and whether it ended come from the API (hazard_type, status).

/** What a symbol sits on: the map's basemap or the page's theme decides it. */
export type Ground = 'dark' | 'light'

export interface Glyph {
  /** SVG path data in a 24 x 24 box centred on (12, 12). */
  d: string
  mode: 'stroke' | 'fill'
}

export interface HazardStyle {
  /** One colour per ground, so the disc keeps at least 3:1 against what it sits on. */
  colour: Record<Ground, string>
  glyphs: Glyph[]
}

const stroke = (d: string): Glyph => ({ d, mode: 'stroke' })
const fill = (d: string): Glyph => ({ d, mode: 'fill' })

/** Dark ground: each colour is at least 6:1 on it, and every pair, the accent and the selection ring included,
 *  is at least 13 apart in CIEDE2000. Light ground: the same hues darkened to at least 3.2:1 on #f2f3f0 and on
 *  white, every pair at least 10 apart. So colour alone tells the hazards apart, as does shape. */
export const HAZARDS: Record<string, HazardStyle> = {
  flood: {
    colour: { dark: '#38c6f4', light: '#0090bc' },
    glyphs: [stroke('M6 9q1.5-1.6 3 0t3 0 3 0 3 0M6 12.5q1.5-1.6 3 0t3 0 3 0 3 0M6 16q1.5-1.6 3 0t3 0 3 0 3 0')],
  },
  tropical_cyclone: {
    colour: { dark: '#ff6fb5', light: '#de5098' },
    // The chart symbol for a tropical cyclone: an eye with two spiral arms.
    glyphs: [stroke('M14.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 1 1 5 0M12 9.5c0-3 3-4.5 5.5-3.5M12 14.5c0 3-3 4.5-5.5 3.5')],
  },
  severe_storm: { colour: { dark: '#c39bff', light: '#9974d4' }, glyphs: [fill('M13.2 5.5 8 13h3.6l-1.2 5.5L16 11h-3.7z')] },
  wildfire: {
    colour: { dark: '#ff8a3d', light: '#d6681a' },
    glyphs: [fill('M12 5.5c1.4 2.6 4 4 4 7.3a4 4 0 0 1-8 0c0-1.8.9-3 1.9-3.8 0 1.4.7 2.3 1.4 2.3-.4-2 .1-4 .7-5.8z')],
  },
  heatwave: {
    colour: { dark: '#ffe14a', light: '#998600' },
    glyphs: [stroke('M14.6 12a2.6 2.6 0 1 1-5.2 0 2.6 2.6 0 1 1 5.2 0M12 5.5v1.8M12 16.7v1.8M5.5 12h1.8M16.7 12h1.8M7.4 7.4l1.3 1.3M15.3 15.3l1.3 1.3M7.4 16.6l1.3-1.3M15.3 8.7l1.3-1.3')],
  },
  // The chart symbol for snow: a six-armed star.
  coldwave: { colour: { dark: '#a5f3fc', light: '#43929b' }, glyphs: [stroke('M12 5.5v13M6.4 8.75l11.2 6.5M6.4 15.25l11.2-6.5')] },
  // Cracked dry ground: a slab with cracks running through it.
  drought: {
    colour: { dark: '#a9b84e', light: '#7d8d23' },
    glyphs: [stroke('M6.5 7.5h11v9h-11zM6.5 11.5 9.5 12l1.5-4.5M9.5 12l1 4.5M11 7.5l3 3.5 3.5-.5M14 11l-.5 5.5')],
  },
  landslide: {
    colour: { dark: '#c4925f', light: '#ad7d4b' },
    glyphs: [stroke('M5.5 17.5 17 7'), fill('M11.2 15.2h2.6v2.6h-2.6zM15.2 14h2.2v2.2h-2.2zM14.6 17.6h1.9v1.9h-1.9z')],
  },
  volcano: { colour: { dark: '#ff5c57', light: '#ef4d4b' }, glyphs: [fill('M5.5 18.5 9.6 11h4.8l4.1 7.5z'), stroke('M10.6 8.6l-.8-1.8M12 8.2V6M13.4 8.6l.8-1.8')] },
  // A seismograph trace.
  earthquake: { colour: { dark: '#3ddc97', light: '#009b5c' }, glyphs: [stroke('M5 12h2.2l1.5-4 2 8 2-8 2 8 1.5-4H19')] },
  tsunami: {
    colour: { dark: '#1fb5a5', light: '#009788' },
    glyphs: [stroke('M5 16.5c2.5 0 3.5-7.5 8-7.5 3 0 4.5 2.2 4.5 4-1.8-1.6-4.4-.5-4 1.8.2 1.1 1.2 1.7 2.3 1.7H5')],
  },
  other: { colour: { dark: '#8e959d', light: '#818890' }, glyphs: [stroke('M12 6.5v7'), fill('M10.8 16.4a1.2 1.2 0 1 0 2.4 0 1.2 1.2 0 1 0-2.4 0')] },
}

export const hazardStyle = (hazard: string): HazardStyle => HAZARDS[hazard] ?? HAZARDS.other

/** Glyph ink on a filled disc (dark on every hazard colour, at least 5:1). */
export const SYMBOL_INK = '#101317'
/** The hollow (ended) disc's centre: the ground it sits on. */
export const HOLLOW_FILL: Record<Ground, string> = { dark: '#101317', light: '#ffffff' }
export const GLYPH_STROKE = 1.9

/** Severity, shown as rings outside the disc: one for Orange, two for Red. Read from the API's severity_label
 *  as given; any other label (Green, an EONET acreage, none) gets no mark. */
export type SeverityMark = 'none' | 'orange' | 'red'
export const severityMark = (label: string | null): SeverityMark => (label === 'Red' ? 'red' : label === 'Orange' ? 'orange' : 'none')
/** The rings' ink: the map's or the page's text colour on that ground. */
export const MARK_INK: Record<Ground, string> = { dark: '#e8e8e8', light: '#1b1b1b' }
/** A symbol sits in a 32-unit box: the 24-unit disc and glyph in the middle, the severity rings around it. */
export const BOX = 32
const PAD = (BOX - 24) / 2
const RINGS: Record<SeverityMark, number[]> = { none: [], orange: [13.4], red: [13.4, 15.4] }
export const ringRadii = (mark: SeverityMark) => RINGS[mark]

/** The map's icon id for one hazard, state, severity mark and ground; one image is registered per combination. */
export const iconId = (hazard: string, ended: boolean, mark: SeverityMark, ground: Ground) =>
  `hz-${ground}-${hazard}-${ended ? 'ended' : 'active'}-${mark}`

/** Draw one symbol onto a canvas for MapLibre (addImage). `size` is the whole box in CSS pixels. */
export function drawSymbol(hazard: string, ended: boolean, mark: SeverityMark, ground: Ground, size: number, pixelRatio: number): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = Math.round(size * pixelRatio)
  const ctx = canvas.getContext('2d')!
  ctx.scale((size * pixelRatio) / BOX, (size * pixelRatio) / BOX)
  const { glyphs } = hazardStyle(hazard)
  const colour = hazardStyle(hazard).colour[ground]

  ctx.lineWidth = 1.4
  ctx.strokeStyle = MARK_INK[ground]
  for (const radius of RINGS[mark]) {
    ctx.beginPath()
    ctx.arc(BOX / 2, BOX / 2, radius, 0, Math.PI * 2)
    ctx.stroke()
  }

  ctx.translate(PAD, PAD)
  ctx.beginPath()
  ctx.arc(12, 12, 10.5, 0, Math.PI * 2)
  ctx.fillStyle = ended ? HOLLOW_FILL[ground] : colour
  ctx.fill()
  ctx.lineWidth = ended ? 2 : 1
  ctx.strokeStyle = ended ? colour : SYMBOL_INK
  ctx.stroke()

  const ink = ended ? colour : SYMBOL_INK
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const glyph of glyphs) {
    const path = new Path2D(glyph.d)
    if (glyph.mode === 'fill') {
      ctx.fillStyle = ink
      ctx.fill(path)
    } else {
      ctx.lineWidth = GLYPH_STROKE
      ctx.strokeStyle = ink
      ctx.stroke(path)
    }
  }
  return ctx.getImageData(0, 0, canvas.width, canvas.height)
}
