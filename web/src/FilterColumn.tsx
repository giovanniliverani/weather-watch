// The left column: name and data age (the top bar on a phone), then the filters, the legend and the events list.
import { useId, useState } from 'react'
import { SEVERITY_STEPS, WINDOW_PRESETS } from './config'
import EventList from './EventList'
import { formatWhen, hazardName, plural } from './format'
import HazardSymbol from './HazardSymbol'
import type { EventFeature, Heartbeat } from './types'
import type { Resource } from './useResource'
import type { Filters } from './url'

interface Props {
  filters: Filters
  hazards: Resource<string[]>
  meta: Heartbeat | null
  points: EventFeature[]
  /** Events per hazard_type in the current answer; hazards filtered out are absent. */
  counts: Map<string, number>
  loading: boolean
  error: Error | null
  selectedId: string | null
  onChange: (next: Filters) => void
  onSelect: (eventId: string) => void
  onAbout: () => void
}

/** An empty hazard list means every hazard; the API reads it the same way. */
function toggleHazard(all: string[], chosen: string[], hazard: string): string[] {
  const current = chosen.length ? chosen : all
  const next = current.includes(hazard) ? current.filter((h) => h !== hazard) : [...current, hazard]
  if (next.length === 0 || next.length === all.length) return []
  return next.sort()
}

export default function FilterColumn(props: Props) {
  const { filters, hazards, meta, points, counts, loading, error, selectedId, onChange, onSelect, onAbout } = props
  const [open, setOpen] = useState(false)
  const [listOpen, setListOpen] = useState(false)
  const bodyId = useId()
  const listId = useId()
  const all = hazards.state === 'ready' ? hazards.data : []

  return (
    <section className="column" aria-label="Filters and events">
      <header className="column-head">
        <h1>Extreme Weather Watch</h1>
        <div className="freshness" role="status">
          {meta ? (
            <>
              <p>Data as of {formatWhen(meta.data_as_of)}</p>
              {meta.pipeline_stale ? (
                <p className="stamp" title="The pipeline reports that collection is behind.">
                  Stale · {meta.missed_runs_7d} of {meta.expected_runs_7d} runs missed
                </p>
              ) : (
                <p className="quiet">
                  {meta.missed_runs_7d} of {meta.expected_runs_7d} runs missed in 7 days
                </p>
              )}
            </>
          ) : (
            <p className="quiet">{loading ? 'Loading events…' : 'No data yet'}</p>
          )}
        </div>
        <button type="button" className="filters-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
          Filters
        </button>
      </header>

      <div className="column-body" id={bodyId} data-open={open}>
        <fieldset className="group">
          <legend>Window</legend>
          <div className="chips">
            {WINDOW_PRESETS.map((days) => (
              <label key={days} className="chip">
                <input type="radio" name="days" checked={filters.days === days} onChange={() => onChange({ ...filters, days })} />
                <span>{days}d</span>
              </label>
            ))}
          </div>
          {(WINDOW_PRESETS as readonly number[]).includes(filters.days) ? null : (
            <p className="quiet">The link asks for the last {filters.days} days.</p>
          )}
        </fieldset>

        <fieldset className="group">
          <legend>Hazards</legend>
          {hazards.state === 'error' ? <p className="error">The hazard list did not load: {hazards.error.message}</p> : null}
          <ul className="legend">
            {all.map((hazard) => {
              const shown = filters.hazards.length === 0 || filters.hazards.includes(hazard)
              const count = counts.get(hazard) ?? 0
              return (
                <li key={hazard}>
                  <label className="legend-row" data-shown={shown}>
                    <input
                      type="checkbox"
                      checked={shown}
                      onChange={() => onChange({ ...filters, hazards: toggleHazard(all, filters.hazards, hazard) })}
                    />
                    <HazardSymbol hazard={hazard} />
                    <span className="legend-name">{hazardName(hazard)}</span>
                    <span className="legend-count">{shown ? count.toLocaleString('en-GB') : 'off'}</span>
                  </label>
                </li>
              )
            })}
          </ul>
          <div className="legend-foot">
            <span className="quiet">
              <HazardSymbol hazard="wildfire" ended size={14} /> hollow = ended
            </span>
            {filters.hazards.length ? (
              <button type="button" className="link" onClick={() => onChange({ ...filters, hazards: [] })}>
                Show all
              </button>
            ) : null}
          </div>
        </fieldset>

        <fieldset className="group">
          <legend>Severity</legend>
          <div className="chips chips-2">
            {SEVERITY_STEPS.map((step) => (
              <label key={step.value} className="chip" title={step.hint}>
                <input
                  type="radio"
                  name="severity"
                  checked={filters.minSeverity === step.value}
                  onChange={() => onChange({ ...filters, minSeverity: step.value })}
                />
                <span>{step.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <label className="check">
          <input type="checkbox" checked={filters.footprints} onChange={(e) => onChange({ ...filters, footprints: e.target.checked })} />
          Footprints <span className="quiet">(flood and fire areas)</span>
        </label>

        {error ? (
          <p className="error" role="alert">
            {error.message}
          </p>
        ) : (
          <button type="button" className="list-toggle" aria-expanded={listOpen} aria-controls={listId} onClick={() => setListOpen(!listOpen)}>
            {plural(points.length, 'event')} · list
          </button>
        )}
        <div id={listId} hidden={!listOpen}>
          <EventList points={points} selectedId={selectedId} onSelect={onSelect} />
        </div>

        <button type="button" className="link about-link" onClick={onAbout}>
          About and credits
        </button>
      </div>
    </section>
  )
}
