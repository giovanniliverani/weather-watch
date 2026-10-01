// Hazard symbols, after weather-chart conventions, drawn once and used by both the map icons and the legend.
// Each is a disc in the hazard colour with a dark glyph (active) or a coloured ring and glyph (ended).
// Presentation only: which hazard and whether it ended come from the API (hazard_type, status).

export interface Glyph {
  /** SVG path data in a 24 x 24 box centred on (12, 12). */
  d: string
  mode: 'stroke' | 'fill'
}

export interface HazardStyle {
  colour: string
  glyphs: Glyph[]
}

const stroke = (d: string): Glyph => ({ d, mode: 'stroke' })
const fill = (d: string): Glyph => ({ d, mode: 'fill' })

/** Colours chosen for a dark ground: each is at least 3:1 against it and none is the accent. */
export const HAZARDS: Record<string, HazardStyle> = {
  flood: { colour: '#38c6f4', glyphs: [stroke('M6 9q1.5-1.6 3 0t3 0 3 0 3 0M6 12.5q1.5-1.6 3 0t3 0 3 0 3 0M6 16q1.5-1.6 3 0t3 0 3 0 3 0')] },
  tropical_cyclone: {
    colour: '#ff6fb5',
    // The chart symbol for a tropical cyclone: an eye with two spiral arms.
    glyphs: [stroke('M14.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 1 1 5 0M12 9.5c0-3 3-4.5 5.5-3.5M12 14.5c0 3-3 4.5-5.5 3.5')],
  },
  severe_storm: { colour: '#c39bff', glyphs: [fill('M13.2 5.5 8 13h3.6l-1.2 5.5L16 11h-3.7z')] },
  wildfire: { colour: '#ff8a3d', glyphs: [fill('M12 5.5c1.4 2.6 4 4 4 7.3a4 4 0 0 1-8 0c0-1.8.9-3 1.9-3.8 0 1.4.7 2.3 1.4 2.3-.4-2 .1-4 .7-5.8z')] },
  heatwave: {
    colour: '#ffd166',
    glyphs: [stroke('M14.6 12a2.6 2.6 0 1 1-5.2 0 2.6 2.6 0 1 1 5.2 0M12 5.5v1.8M12 16.7v1.8M5.5 12h1.8M16.7 12h1.8M7.4 7.4l1.3 1.3M15.3 15.3l1.3 1.3M7.4 16.6l1.3-1.3M15.3 8.7l1.3-1.3')],
  },
  // The chart symbol for snow: a six-armed star.
  coldwave: { colour: '#cfe8ff', glyphs: [stroke('M12 5.5v13M6.4 8.75l11.2 6.5M6.4 15.25l11.2-6.5')] },
  drought: { colour: '#d9b44a', glyphs: [stroke('M5.5 9h13M8 9l1.6 3.2-.9 3M12.5 9l-1 2.8 1.6 3.4M16.5 9l-1.4 3 .8 3.5')] },
  landslide: { colour: '#c4925f', glyphs: [stroke('M5.5 17.5 17 7'), fill('M11.2 15.2h2.6v2.6h-2.6zM15.2 14h2.2v2.2h-2.2zM14.6 17.6h1.9v1.9h-1.9z')] },
  volcano: { colour: '#ff5c57', glyphs: [fill('M5.5 18.5 9.6 11h4.8l4.1 7.5z'), stroke('M10.6 8.6l-.8-1.8M12 8.2V6M13.4 8.6l.8-1.8')] },
  // A seismograph trace.
  earthquake: { colour: '#3ddc97', glyphs: [stroke('M5 12h2.2l1.5-4 2 8 2-8 2 8 1.5-4H19')] },
  tsunami: { colour: '#22c5b5', glyphs: [stroke('M5 16.5c2.5 0 3.5-7.5 8-7.5 3 0 4.5 2.2 4.5 4-1.8-1.6-4.4-.5-4 1.8.2 1.1 1.2 1.7 2.3 1.7H5')] },
  other: { colour: '#a7b0ba', glyphs: [stroke('M12 6.5v7'), fill('M10.8 16.4a1.2 1.2 0 1 0 2.4 0 1.2 1.2 0 1 0-2.4 0')] },
}

export const hazardStyle = (hazard: string): HazardStyle => HAZARDS[hazard] ?? HAZARDS.other

/** Ink for glyphs on a filled disc and for the hollow disc's centre; matches the map's dark ground. */
export const SYMBOL_INK = '#101317'
export const GLYPH_STROKE = 1.9

/** The map's icon id for a hazard and state; one image is registered per pair. */
export const iconId = (hazard: string, ended: boolean) => `hz-${hazard}-${ended ? 'ended' : 'active'}`

/** Draw one symbol onto a canvas for MapLibre (addImage). `size` is in CSS pixels. */
export function drawSymbol(hazard: string, ended: boolean, size: number, pixelRatio: number): ImageData {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = Math.round(size * pixelRatio)
  const ctx = canvas.getContext('2d')!
  const scale = (size * pixelRatio) / 24
  ctx.scale(scale, scale)
  const { colour, glyphs } = hazardStyle(hazard)

  ctx.beginPath()
  ctx.arc(12, 12, 10.5, 0, Math.PI * 2)
  ctx.fillStyle = ended ? SYMBOL_INK : colour
  ctx.fill()
  if (ended) {
    ctx.lineWidth = 2
    ctx.strokeStyle = colour
    ctx.stroke()
  } else {
    ctx.lineWidth = 1
    ctx.strokeStyle = SYMBOL_INK
    ctx.stroke()
  }

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
