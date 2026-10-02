// The left column: name, data age and theme switch (the top bar on a phone), then the filters, the legend,
// the map style and the events list.
import { useId, useState } from 'react'
import { AUTO_BASEMAP, BASEMAPS, WINDOW_PRESETS, type Basemap } from './config'
import EventList from './EventList'
import { formatShortWhen, formatWhen, hazardName, plural } from './format'
import HazardSymbol from './HazardSymbol'
import { Chevron, Moon, Sun } from './icons'
import type { Theme } from './theme'
import type { EventFeature, Heartbeat, SeverityStep } from './types'
import type { Resource } from './useResource'
import type { Filters } from './url'

interface Props {
  filters: Filters
  hazards: Resource<string[]>
  severitySteps: Resource<SeverityStep[]>
  meta: Heartbeat | null
  points: EventFeature[]
  /** Events per hazard_type in the current answer; hazards filtered out are absent. */
  counts: Map<string, number>
  loading: boolean
  error: Error | null
  selectedId: string | null
  theme: Theme
  /** 'auto' or a BASEMAPS id. */
  basemapChoice: string
  /** The basemap on screen ('auto' resolved). */
  basemap: Basemap
  onChange: (next: Filters) => void
  onSelect: (eventId: string) => void
  onTheme: (theme: Theme) => void
  onBasemap: (choice: string) => void
  /** Ask the API again after a failed request. */
  onRetry: () => void
  onAbout: () => void
}

/** An empty hazard list means every hazard; the API reads it the same way. Only hazards the API lists count. */
function toggleHazard(all: string[], chosen: string[], hazard: string): string[] {
  const known = chosen.filter((h) => all.includes(h))
  const current = known.length ? known : all
  const next = current.includes(hazard) ? current.filter((h) => h !== hazard) : [...current, hazard]
  if (next.length === 0 || next.length === all.length) return []
  return next.sort()
}

export default function FilterColumn(props: Props) {
  const { filters, hazards, severitySteps, meta, points, counts, loading, error, selectedId, theme, basemapChoice, basemap } = props
  const { onChange, onSelect, onTheme, onBasemap, onRetry, onAbout } = props
  const basemapId = useId()
  const severityHintId = useId()
  const steps = severitySteps.state === 'ready' ? severitySteps.data : []
  const chosenStep = steps.find((step) => step.value === filters.minSeverity)
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
              <p>
                <span className="wide-only">Data as of {formatWhen(meta.data_as_of)}</span>
                <span className="narrow-only">as of {formatShortWhen(meta.data_as_of)}</span>
              </p>
              {meta.pipeline_stale ? (
                <p className="stamp" title={`Collection is behind: ${meta.missed_runs_7d} of ${meta.expected_runs_7d} runs missed in 7 days.`}>
                  Stale<span className="wide-only"> · {meta.missed_runs_7d} of {meta.expected_runs_7d} runs missed</span>
                </p>
              ) : (
                <p className="quiet wide-only">
                  {meta.missed_runs_7d} of {meta.expected_runs_7d} runs missed in 7 days
                </p>
              )}
            </>
          ) : (
            <p className="quiet">{loading ? 'Loading events…' : 'No data yet'}</p>
          )}
        </div>
        <div className="head-actions">
          <button
            type="button"
            className="icon-button theme-toggle"
            aria-pressed={theme === 'light'}
            aria-label="Light theme"
            title={theme === 'light' ? 'Switch to the dark theme' : 'Switch to the light theme'}
            onClick={() => onTheme(theme === 'light' ? 'dark' : 'light')}
          >
            {theme === 'light' ? <Moon /> : <Sun />}
          </button>
          <button type="button" className="filters-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
            Filters
          </button>
        </div>
      </header>

      <div className="column-body" id={bodyId} data-open={open}>
        <fieldset className="group">
          <legend>Window</legend>
          <div className="chips">
            {WINDOW_PRESETS.map((days) => (
              <label key={days} className="chip">
                <input
                  type="radio"
                  name="days"
                  aria-label={days === 1 ? 'Last day' : `Last ${days} days`}
                  checked={filters.days === days}
                  onChange={() => onChange({ ...filters, days })}
                />
                <span aria-hidden="true">{days}d</span>
              </label>
            ))}
          </div>
          {(WINDOW_PRESETS as readonly number[]).includes(filters.days) ? null : (
            <p className="quiet">The link asks for the last {filters.days} days.</p>
          )}
        </fieldset>

        <fieldset className="group">
          <legend>Hazards</legend>
          {hazards.state === 'error' ? (
            <p className="error">
              The hazard list did not load: {hazards.error.message}{' '}
              <button type="button" className="link" onClick={onRetry}>
                Try again
              </button>
            </p>
          ) : null}
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
            <ul className="key quiet" aria-label="How symbols read; rings show the severity score's band">
              <li>
                <HazardSymbol hazard="other" ended size={14} /> ended
              </li>
              <li>
                <HazardSymbol hazard="other" mark="orange" size={14} /> Orange band
              </li>
              <li>
                <HazardSymbol hazard="other" mark="red" size={14} /> Red band
              </li>
            </ul>
            {filters.hazards.length ? (
              <button type="button" className="link" onClick={() => onChange({ ...filters, hazards: [] })}>
                Show all
              </button>
            ) : null}
          </div>
          <p className="hint key-note">Numbered rings are groups of events, coloured by their hazard mix; zoom in to split them.</p>
        </fieldset>

        <fieldset className="group">
          <legend>Severity</legend>
          {severitySteps.state === 'error' ? (
            <p className="error">
              The severity steps did not load: {severitySteps.error.message}{' '}
              <button type="button" className="link" onClick={onRetry}>
                Try again
              </button>
            </p>
          ) : null}
          <div className="chips chips-2">
            {steps.map((step) => (
              <label key={step.value} className="chip">
                <input
                  type="radio"
                  name="severity"
                  aria-describedby={severityHintId}
                  checked={filters.minSeverity === step.value}
                  onChange={() => onChange({ ...filters, minSeverity: step.value })}
                />
                <span>{step.label}</span>
              </label>
            ))}
          </div>
          <p className="quiet severity-hint" id={severityHintId}>
            {chosenStep ? chosenStep.hint : steps.length ? `The link asks for a score of ${filters.minSeverity} and up.` : null}
          </p>
        </fieldset>

        <label className="check">
          <input type="checkbox" checked={filters.footprints} onChange={(e) => onChange({ ...filters, footprints: e.target.checked })} />
          Footprints <span className="quiet">(flood and fire areas)</span>
        </label>

        <div className="group">
          <label className="group-label" htmlFor={basemapId}>
            Map style
          </label>
          <div className="select-wrap">
            <select id={basemapId} className="select" value={basemapChoice} onChange={(e) => onBasemap(e.target.value)}>
              <option value="auto">Auto ({BASEMAPS.find((b) => b.id === AUTO_BASEMAP[theme])?.name})</option>
              {BASEMAPS.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
            <Chevron />
          </div>
          <p className="quiet credit-line">{basemap.attribution}</p>
        </div>

        {error ? (
          <p className="error" role="alert">
            {error.message}{' '}
            <button type="button" className="link" onClick={onRetry}>
              Try again
            </button>
          </p>
        ) : (
          <button type="button" className="list-toggle" aria-expanded={listOpen} aria-controls={listId} onClick={() => setListOpen(!listOpen)}>
            <span>{plural(points.length, 'event')} · list</span>
            <Chevron />
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
