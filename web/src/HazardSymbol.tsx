// A hazard symbol as inline SVG, for the legend, the events list and the panel; the map draws the same shapes.
import { BOX, GLYPH_STROKE, hazardStyle, HOLLOW_FILL, MARK_INK, ringRadii, SYMBOL_INK, type SeverityMark } from './symbols'
import { useTheme } from './theme'

interface Props {
  hazard: string
  ended?: boolean
  mark?: SeverityMark
  /** The disc's size in pixels; severity rings draw outside it. */
  size?: number
}

export default function HazardSymbol({ hazard, ended = false, mark = 'none', size = 20 }: Props) {
  const ground = useTheme()
  const { glyphs } = hazardStyle(hazard)
  const colour = hazardStyle(hazard).colour[ground]
  const ink = ended ? colour : SYMBOL_INK
  const rings = ringRadii(mark)
  const pad = rings.length ? (BOX - 24) / 2 : 0
  const box = 24 + 2 * pad
  return (
    <svg
      className="symbol"
      width={(size * box) / 24}
      height={(size * box) / 24}
      viewBox={`${-pad} ${-pad} ${box} ${box}`}
      aria-hidden="true"
      focusable="false"
    >
      {rings.map((r) => (
        <circle key={r} cx="12" cy="12" r={r} fill="none" stroke={MARK_INK[ground]} strokeWidth="1.4" />
      ))}
      <circle cx="12" cy="12" r="10.5" fill={ended ? HOLLOW_FILL[ground] : colour} stroke={ended ? colour : SYMBOL_INK} strokeWidth={ended ? 2 : 1} />
      {glyphs.map((glyph) =>
        glyph.mode === 'fill' ? (
          <path key={glyph.d} d={glyph.d} fill={ink} />
        ) : (
          <path key={glyph.d} d={glyph.d} fill="none" stroke={ink} strokeWidth={GLYPH_STROKE} strokeLinecap="round" strokeLinejoin="round" />
        ),
      )}
    </svg>
  )
}
