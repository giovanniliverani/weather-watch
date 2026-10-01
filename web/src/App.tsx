// The page: filters and the events list on the left, the map in the middle, the selected event's panel on the right.
// All view state (filters, selected event, map position, page) is mirrored into the URL query.
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import About from './About'
import { eventsQuery, fetchEvents, fetchHazards } from './api'
import FilterColumn from './FilterColumn'
import type { FlyTarget } from './MapView'
import Panel from './Panel'
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
  const openPage = (page: ViewState['page']) => {
    window.history.pushState(null, '', window.location.href)
    setView((v) => ({ ...v, page }))
  }

  if (view.page === 'about') return <About onBack={() => openPage('map')} />

  return (
    <div className="app">
      <FilterColumn
        filters={view.filters}
        hazards={hazards}
        meta={collection?.meta ?? null}
        points={points}
        counts={counts}
        loading={events.state === 'loading'}
        error={events.state === 'error' ? events.error : null}
        selectedId={view.eventId}
        onChange={setFilters}
        onSelect={selectFromList}
        onAbout={() => openPage('about')}
      />

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
        <aside className="panel panel-note">
          <p className="quiet">The event in this link is not shown with the current filters.</p>
          <button type="button" className="link" onClick={() => select(null)}>
            Clear the selection
          </button>
        </aside>
      ) : null}
    </div>
  )
}
