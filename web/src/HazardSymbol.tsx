// A hazard symbol as inline SVG, for the legend, the events list and the panel; the map draws the same shapes.
import { GLYPH_STROKE, hazardStyle, HOLLOW_FILL, SYMBOL_INK } from './symbols'
import { useTheme } from './theme'

interface Props {
  hazard: string
  ended?: boolean
  size?: number
}

export default function HazardSymbol({ hazard, ended = false, size = 20 }: Props) {
  const ground = useTheme()
  const { glyphs } = hazardStyle(hazard)
  const colour = hazardStyle(hazard).colour[ground]
  const ink = ended ? colour : SYMBOL_INK
  return (
    <svg className="symbol" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
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
