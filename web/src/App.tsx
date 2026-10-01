// The page: filters and the events list on the left, the map in the middle, the selected event's panel on the right.
// View state (filters, selected event, map position, page) is mirrored into the URL query; viewer preferences
// (theme, basemap) are kept per browser instead (theme.ts).
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import About from './About'
import { eventsQuery, fetchEvents, fetchHazards, fetchSeveritySteps } from './api'
import { AUTO_BASEMAP, BASEMAPS } from './config'
import FilterColumn from './FilterColumn'
import type { FlyTarget } from './MapView'
import Panel from './Panel'
import { BASEMAP_KEY, readPreference, readTheme, savePreference, THEME_KEY, ThemeContext, type Theme } from './theme'
import type { EventFeature, FootprintFeature, SeverityStep } from './types'
import { readViewState, writeViewState, type Filters, type MapView as View, type ViewState } from './url'
import { useResource } from './useResource'

const MapView = lazy(() => import('./MapView'))
const hazardCache = new Map<string, string[]>()

/** How many event selections deep the current history entry is (0 when it was not pushed by a selection). */
const selectionDepth = (): number => {
  const depth = (window.history.state as { ewwDepth?: unknown } | null)?.ewwDepth
  return typeof depth === 'number' && depth > 0 ? depth : 0
}
const severityStepCache = new Map<string, SeverityStep[]>()

/** 'auto' or a BASEMAPS id; anything else saved earlier falls back to 'auto'. */
const readBasemapChoice = () => {
  const saved = readPreference(BASEMAP_KEY)
  return saved && BASEMAPS.some((b) => b.id === saved) ? saved : 'auto'
}

export default function App() {
  const [view, setView] = useState<ViewState>(() => readViewState(window.location.search))
  const [flyTo, setFlyTo] = useState<FlyTarget | null>(null)
  const [theme, setTheme] = useState<Theme>(readTheme)
  const [basemapChoice, setBasemapChoice] = useState<string>(readBasemapChoice)

  // Mirror the view into the address bar, so a reload restores it. Selecting an event adds a history entry, so Back
  // (or a phone's back gesture) closes the panel or returns to the previous event; filters and map moves replace the
  // entry, so the history stays short. A view that came from Back itself, or from the opening link, adds nothing.
  // Each pushed entry records how many selections deep it is (ewwDepth), so closing the panel can step back to where
  // it opened instead of leaving a dead Back step.
  const shownEventId = useRef(view.eventId)
  const fromHistory = useRef(false)
  useEffect(() => {
    const query = writeViewState(view)
    const url = `${window.location.pathname}${query}`
    const newSelection = view.eventId !== null && view.eventId !== shownEventId.current && !fromHistory.current
    shownEventId.current = view.eventId
    fromHistory.current = false
    if (newSelection) window.history.pushState({ ewwDepth: selectionDepth() + 1 }, '', url)
    else if (query !== window.location.search) window.history.replaceState(window.history.state, '', url)
  }, [view])
  useEffect(() => {
    // Back changes the selection and the page only: the filters and the map position stay as they are now.
    const onPop = () => {
      fromHistory.current = true
      const popped = readViewState(window.location.search)
      setView((v) => ({ ...v, eventId: popped.eventId, page: popped.page }))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    savePreference(THEME_KEY, theme)
  }, [theme])
  useEffect(() => savePreference(BASEMAP_KEY, basemapChoice), [basemapChoice])

  const basemapId = basemapChoice === 'auto' ? AUTO_BASEMAP[theme] : basemapChoice
  const basemap = BASEMAPS.find((b) => b.id === basemapId) ?? BASEMAPS[0]

  // "Try again" after the API was down: a new attempt number is a new request key, so both are asked again.
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  const hazards = useResource(`all#${attempt}`, fetchHazards, hazardCache)
  const severitySteps = useResource(`all#${attempt}`, fetchSeveritySteps, severityStepCache)
  const events = useResource(`${eventsQuery(view.filters)}#${attempt}`, (signal) => fetchEvents(view.filters, signal))
  const collection = events.state === 'ready' ? events.data : events.state === 'loading' ? events.previous : undefined

  // A link may name a hazard the API no longer lists (renamed, or a typo); drop it once the list is known.
  const knownHazards = hazards.state === 'ready' ? hazards.data : null
  useEffect(() => {
    if (!knownHazards) return
    setView((v) => {
      const kept = v.filters.hazards.filter((h) => knownHazards.includes(h))
      return kept.length === v.filters.hazards.length ? v : { ...v, filters: { ...v.filters, hazards: kept } }
    })
  }, [knownHazards])

  const { points, footprints, byId, counts } = useMemo(() => {
    const points: EventFeature[] = []
    const footprints: FootprintFeature[] = []
    const counts = new Map<string, number>()
    for (const feature of collection?.features ?? []) {
      // Footprint features carry a `role`; event features do not (the contract in architecture section 2).
      if ('role' in feature.properties) {
        footprints.push(feature as FootprintFeature)
      } else {
        const point = feature as EventFeature
        points.push(point)
        counts.set(point.properties.hazard_type, (counts.get(point.properties.hazard_type) ?? 0) + 1)
      }
    }
    return { points, footprints, counts, byId: new Map(points.map((point) => [point.properties.event_id, point])) }
  }, [collection])

  const selected = view.eventId ? byId.get(view.eventId) : undefined

  const setFilters = useCallback((filters: Filters) => setView((v) => ({ ...v, filters })), [])
  const select = useCallback((eventId: string | null) => setView((v) => ({ ...v, eventId })), [])
  const onMove = useCallback((map: View) => setView((v) => ({ ...v, map })), [])
  const selectFromList = useCallback(
    (eventId: string) => {
      select(eventId)
      const point = byId.get(eventId)
      if (point) setFlyTo({ lon: point.geometry.coordinates[0], lat: point.geometry.coordinates[1], nonce: Date.now() })
    },
    [byId, select],
  )
  // Closing the panel from inside it puts focus back where the user was: the event's list row, else the list
  // button. The focus moves in an effect, after the panel has gone from the page.
  const returnFocusTo = useRef<string | null>(null)
  const closePanel = useCallback(
    (eventId: string) => {
      returnFocusTo.current = document.activeElement?.closest('.panel') ? eventId : null
      // Step back past the selections this panel's history holds; the popped entry then clears the selection.
      const depth = selectionDepth()
      if (depth > 0) window.history.go(-depth)
      else select(null)
    },
    [select],
  )
  useEffect(() => {
    const eventId = returnFocusTo.current
    if (view.eventId !== null || eventId === null) return
    returnFocusTo.current = null
    const row = document.querySelector<HTMLElement>(`.event-list [data-event-id="${CSS.escape(eventId)}"]`)
    const target = row && row.offsetParent !== null ? row : document.querySelector<HTMLElement>('.list-toggle')
    target?.focus()
  }, [view.eventId])
  const openPage = (page: ViewState['page']) => {
    window.history.pushState(null, '', window.location.href)
    setView((v) => ({ ...v, page }))
  }

  if (view.page === 'about') {
    return (
      <ThemeContext.Provider value={theme}>
        <About onBack={() => openPage('map')} />
      </ThemeContext.Provider>
    )
  }

  return (
    <ThemeContext.Provider value={theme}>
      <div className="app">
        <FilterColumn
          filters={view.filters}
          hazards={hazards}
          severitySteps={severitySteps}
          meta={collection?.meta ?? null}
          points={points}
          counts={counts}
          loading={events.state === 'loading'}
          error={events.state === 'error' ? events.error : null}
          selectedId={view.eventId}
          theme={theme}
          basemapChoice={basemapChoice}
          basemap={basemap}
          onChange={setFilters}
          onSelect={selectFromList}
          onTheme={setTheme}
          onBasemap={setBasemapChoice}
          onRetry={retry}
          onAbout={() => openPage('about')}
        />

        <Suspense fallback={<div className="map map-loading">Loading the map…</div>}>
          <MapView
            basemap={basemap}
            points={points}
            footprints={footprints}
            selectedId={view.eventId}
            initialView={view.map}
            flyTo={flyTo}
            onSelect={select}
            onMove={onMove}
          />
        </Suspense>

        {selected ? (
          <Panel event={selected} onClose={() => closePanel(selected.properties.event_id)} />
        ) : view.eventId && events.state === 'ready' ? (
          <aside className="panel panel-note">
            <p className="quiet">The event in this link is not shown with the current filters.</p>
            <button type="button" className="link" onClick={() => closePanel(view.eventId!)}>
              Clear the selection
            </button>
          </aside>
        ) : null}
      </div>
    </ThemeContext.Provider>
  )
}
