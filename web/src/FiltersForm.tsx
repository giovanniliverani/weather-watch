import { HAZARD_COLOURS, OTHER_COLOUR, SEVERITY_STEPS, WINDOW_PRESETS } from './config'
import { hazardName } from './format'
import type { Resource } from './useResource'
import type { Filters } from './url'

interface Props {
  filters: Filters
  hazards: Resource<string[]>
  onChange: (next: Filters) => void
}

/** An empty hazard list means every hazard; the API reads it the same way. */
function toggleHazard(all: string[], chosen: string[], hazard: string): string[] {
  const current = chosen.length ? chosen : all
  const next = current.includes(hazard) ? current.filter((h) => h !== hazard) : [...current, hazard]
  if (next.length === 0 || next.length === all.length) return []
  return next.sort()
}

export default function FiltersForm({ filters, hazards, onChange }: Props) {
  const all = hazards.state === 'ready' ? hazards.data : []
  const isPreset = (WINDOW_PRESETS as readonly number[]).includes(filters.days)

  return (
    <form className="filters" aria-label="Filters" onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Window</legend>
        <div className="segmented">
          {WINDOW_PRESETS.map((days) => (
            <label key={days}>
              <input
                type="radio"
                name="days"
                value={days}
                checked={filters.days === days}
                onChange={() => onChange({ ...filters, days })}
              />
              <span>{days} d</span>
            </label>
          ))}
        </div>
        {isPreset ? null : <p className="hint">Showing the last {filters.days} days (from the link).</p>}
      </fieldset>

      <fieldset>
        <legend>Minimum severity</legend>
        <select
          value={filters.minSeverity}
          onChange={(e) => onChange({ ...filters, minSeverity: Number(e.target.value) })}
        >
          {SEVERITY_STEPS.map((step) => (
            <option key={step.value} value={step.value}>
              {step.label}
            </option>
          ))}
        </select>
      </fieldset>

      <fieldset>
        <legend>Hazards</legend>
        {hazards.state === 'error' ? <p className="hint">The hazard list did not load: {hazards.error.message}</p> : null}
        <ul className="hazards">
          {all.map((hazard) => {
            const checked = filters.hazards.length === 0 || filters.hazards.includes(hazard)
            return (
              <li key={hazard}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onChange({ ...filters, hazards: toggleHazard(all, filters.hazards, hazard) })}
                  />
                  <span className="swatch" style={{ background: HAZARD_COLOURS[hazard] ?? OTHER_COLOUR }} aria-hidden="true" />
                  {hazardName(hazard)}
                </label>
              </li>
            )
          })}
        </ul>
        {filters.hazards.length ? (
          <button type="button" className="link" onClick={() => onChange({ ...filters, hazards: [] })}>
            Show all hazards
          </button>
        ) : null}
      </fieldset>

      <label className="toggle">
        <input type="checkbox" checked={filters.footprints} onChange={(e) => onChange({ ...filters, footprints: e.target.checked })} />
        Footprints (flood and fire areas the feeds supply)
      </label>
    </form>
  )
}
