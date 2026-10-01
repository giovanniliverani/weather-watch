// The page: filters and the events list on the left, the map in the middle, the selected event's panel on the right.
// All view state (filters, selected event, map position, page) is mirrored into the URL query.
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import About from './About'
import { eventsQuery, fetchEvents, fetchHazards } from './api'
import EventList from './EventList'
import FiltersForm from './FiltersForm'
import type { FlyTarget } from './MapView'
import Panel from './Panel'
import StatusStrip from './StatusStrip'
import type { EventFeature, FootprintFeature } from './types'
import { readViewState, writeViewState, type Filters, type MapView as View, type ViewState } from './url'
import { useResource } from './useResource'

const MapView = lazy(() => import('./MapView'))
const hazardCache = new Map<string, string[]>()

export default function App() {
  const [view, setView] = useState<ViewState>(() => readViewState(window.location.search))
  const [flyTo, setFlyTo] = useState<FlyTarget | null>(null)

  // Mirror the view into the address bar; Back and Forward restore it.
  useEffect(() => {
    const query = writeViewState(view)
    if (query !== window.location.search) window.history.replaceState(null, '', `${window.location.pathname}${query}`)
  }, [view])
  useEffect(() => {
    const onPop = () => setView(readViewState(window.location.search))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const hazards = useResource('all', fetchHazards, hazardCache)
  const events = useResource(eventsQuery(view.filters), (signal) => fetchEvents(view.filters, signal))
  const collection = events.state === 'ready' ? events.data : events.state === 'loading' ? events.previous : undefined

  const { points, footprints, byId } = useMemo(() => {
    const points: EventFeature[] = []
    const footprints: FootprintFeature[] = []
    for (const feature of collection?.features ?? []) {
      // Footprint features carry a `role`; event features do not (the contract in architecture section 2).
      if ('role' in feature.properties) footprints.push(feature as FootprintFeature)
      else points.push(feature as EventFeature)
    }
    return { points, footprints, byId: new Map(points.map((point) => [point.properties.event_id, point])) }
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
  const openPage = (page: ViewState['page']) => {
    window.history.pushState(null, '', window.location.href)
    setView((v) => ({ ...v, page }))
  }

  if (view.page === 'about') return <About onBack={() => openPage('map')} />

  return (
    <div className="app">
      <header className="topbar">
        <h1>Extreme Weather Watch</h1>
        <StatusStrip meta={collection?.meta ?? null} shown={collection ? points.length : null} loading={events.state === 'loading'} />
        <button type="button" className="link" onClick={() => openPage('about')}>
          About and credits
        </button>
      </header>

      <section className="sidebar" aria-label="Filters and events">
        <FiltersForm filters={view.filters} hazards={hazards} onChange={setFilters} />
        {events.state === 'error' ? (
          <p className="error" role="alert">
            {events.error.message}
          </p>
        ) : (
          <EventList points={points} selectedId={view.eventId} onSelect={selectFromList} />
        )}
      </section>

      <Suspense fallback={<div className="map map-loading">Loading the map…</div>}>
        <MapView
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
        <Panel event={selected} onClose={() => select(null)} />
      ) : view.eventId && events.state === 'ready' ? (
        <aside className="panel">
          <p className="hint">The event in this link is not shown with the current filters.</p>
          <button type="button" className="link" onClick={() => select(null)}>
            Clear the selection
          </button>
        </aside>
      ) : null}
    </div>
  )
}
