// The MapLibre map. Loaded lazily (React.lazy in App) so the page shell paints before the map library arrives.
import {
  GeolocateControl,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
  type StyleSpecification,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker beside its own file, which bundling moves; Vite bundles the worker and gives its address.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef, useState } from 'react'
import type { Basemap } from './config'
import { BOX, drawSymbol, iconId, MARK_INK, type Ground, type SeverityMark } from './symbols'
import type { EventFeature, FootprintFeature } from './types'
import type { MapView as View } from './url'

export interface FlyTarget {
  lon: number
  lat: number
  /** Changes on every request, so asking twice for the same place still moves the map. */
  nonce: number
}

interface Props {
  basemap: Basemap
  points: EventFeature[]
  footprints: FootprintFeature[]
  selectedId: string | null
  initialView: View | null
  flyTo: FlyTarget | null
  onSelect: (eventId: string) => void
  onMove: (view: View) => void
}

setWorkerUrl(workerUrl)

/** A symbol's whole box on the map, severity rings included; the disc inside is 22 px. */
const SYMBOL_PX = (22 * BOX) / 24
/** Fonts for the cluster counts; OpenFreeMap serves Noto Sans Regular, also to raster-only styles. */
const GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf'
/** Cluster discs, counts, the selection ring and footprints, per basemap ground (index.css --raise and --surface; keep in step). */
const CHROME: Record<Ground, { ink: string; disc: string; accent: string }> = {
  dark: { ink: MARK_INK.dark, disc: '#212121', accent: '#8ab4ff' },
  light: { ink: MARK_INK.light, disc: '#ffffff', accent: '#2357c6' },
}

/** A basemap as something MapLibre can load: a style URL, or a one-layer style around raster tiles. */
function styleFor(basemap: Basemap): string | StyleSpecification {
  if (basemap.kind === 'style') return basemap.url
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: {
      basemap: { type: 'raster', tiles: basemap.tiles, tileSize: basemap.tileSize, maxzoom: basemap.maxzoom, attribution: basemap.attribution },
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  }
}

/** A plain local style, used when the chosen basemap fails to load, so the events still draw. */
function fallbackStyle(ground: Ground): StyleSpecification {
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': ground === 'dark' ? '#0c0c0c' : '#f2f3f0' } }],
  }
}

const collection = <F,>(features: F[]) => ({ type: 'FeatureCollection' as const, features })

/** Add the event layers on top of whatever basemap style is loaded; runs again after every style switch. */
function addLayers(map: MapLibreMap, ground: Ground) {
  const { ink, disc, accent } = CHROME[ground]
  map.addSource('footprints', { type: 'geojson', data: collection([]) })
  map.addLayer({
    id: 'footprint-fill',
    type: 'fill',
    source: 'footprints',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': ink, 'fill-opacity': 0.08 },
  })
  map.addLayer({
    id: 'footprint-line',
    type: 'line',
    source: 'footprints',
    paint: { 'line-color': ink, 'line-opacity': 0.55, 'line-width': 1, 'line-dasharray': [3, 2] },
  })

  map.addSource('events', { type: 'geojson', data: collection([]), cluster: true, clusterMaxZoom: 6, clusterRadius: 36 })
  map.addLayer({
    id: 'clusters',
    type: 'circle',
    source: 'events',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': disc,
      'circle-stroke-color': ink,
      'circle-stroke-opacity': 0.7,
      'circle-stroke-width': 1.25,
      'circle-radius': ['step', ['get', 'point_count'], 12, 10, 15, 50, 19, 200, 24],
    },
  })
  map.addLayer({
    id: 'cluster-count',
    type: 'symbol',
    source: 'events',
    filter: ['has', 'point_count'],
    layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 12, 'text-font': ['Noto Sans Regular'], 'text-allow-overlap': true },
    paint: { 'text-color': ink },
  })
  map.addLayer({
    id: 'event-selected',
    type: 'circle',
    source: 'events',
    filter: ['==', ['get', 'event_id'], ''],
    // In the accent colour (the page's "selected" colour) and clear of the severity rings, so selection never reads
    // as one more severity ring.
    paint: { 'circle-opacity': 0, 'circle-radius': 19.5, 'circle-stroke-color': accent, 'circle-stroke-width': 3 },
  })
  map.addLayer({
    id: 'event-points',
    type: 'symbol',
    source: 'events',
    filter: ['!', ['has', 'point_count']],
    layout: {
      // Images are drawn on demand (the missing-image resolver below), so any hazard_type the API sends gets a symbol.
      'icon-image': [
        'concat',
        `hz-${ground}-`,
        ['get', 'hazard_type'],
        '-',
        ['case', ['==', ['get', 'status'], 'ended'], 'ended', 'active'],
        '-',
        // The severity mark follows the API's severity_band (symbols.ts severityMark), no thresholds here.
        ['match', ['get', 'severity_band'], 'Red', 'red', 'Orange', 'orange', 'none'],
      ],
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      // Active events above ended ones, more severe above less severe.
      'symbol-sort-key': ['+', ['coalesce', ['get', 'severity_score'], 0], ['case', ['==', ['get', 'status'], 'ended'], 0, 10]],
    },
  })
}

function fitToPoints(map: MapLibreMap, points: EventFeature[]) {
  if (points.length === 0) return
  const bounds = new LngLatBounds()
  for (const point of points) bounds.extend(point.geometry.coordinates)
  map.fitBounds(bounds, { padding: 48, maxZoom: 6, duration: 0 })
}

export default function MapView({ basemap, points, footprints, selectedId, initialView, flyTo, onSelect, onMove }: Props) {
  const container = useRef<HTMLElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const fitted = useRef(initialView !== null)
  const shownBasemap = useRef(basemap.id)
  const styleLoaded = useRef(false)
  const usingFallback = useRef(false)
  const [failedBasemap, setFailedBasemap] = useState<string | null>(null)
  const latest = useRef({ basemap, points, footprints, selectedId, onSelect, onMove })
  latest.current = { basemap, points, footprints, selectedId, onSelect, onMove }

  // Create the map once; the basemap, data, selection and moves flow in through the effects below.
  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current!,
      style: styleFor(latest.current.basemap),
      center: initialView ? [initialView.lon, initialView.lat] : [0, 20],
      zoom: initialView?.zoom ?? 1.5,
      // The tile credit stays spelled out, never folded into an info button.
      attributionControl: { compact: false },
      dragRotate: false,
      // The canvas is MapLibre's own labelled region; this is what a screen reader announces for it.
      locale: { 'Map.Title': 'Map of events. Every event is also in the events list.' },
    })
    map.touchZoomRotate.disableRotation()
    // Controls sit top-right: the bottom-right corner is kept free for a later "add an event" control.
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new GeolocateControl({ positionOptions: { enableHighAccuracy: false }, trackUserLocation: false }), 'top-right')
    mapRef.current = map

    // Symbols are drawn the first time a feature needs one; MapLibre waits for this before giving up on the image.
    map.setMissingStyleImageResolver((id) => {
      const match = /^hz-(dark|light)-(.+)-(active|ended)-(none|orange|red)$/.exec(id)
      if (!match || map.hasImage(id)) return
      const [, ground, hazard, state, mark] = match as unknown as [string, Ground, string, string, SeverityMark]
      const ended = state === 'ended'
      const ratio = window.devicePixelRatio || 1
      map.addImage(iconId(hazard, ended, mark, ground), drawSymbol(hazard, ended, mark, ground, SYMBOL_PX, ratio), { pixelRatio: ratio })
    })

    // A basemap style that cannot load (tile host down, offline, blocked by a proxy) never fires 'style.load', so the
    // pins would never be added: fall back to a plain local background and say so.
    map.on('error', () => {
      if (styleLoaded.current || usingFallback.current) return
      usingFallback.current = true
      setFailedBasemap(latest.current.basemap.name)
      map.setStyle(fallbackStyle(latest.current.basemap.ground), { diff: false })
    })

    // Every style load (the first, and each basemap switch) wipes added layers, so they are put back here.
    map.on('style.load', () => {
      styleLoaded.current = true
      const { basemap: b, points: p, footprints: f, selectedId: s } = latest.current
      addLayers(map, b.ground)
      ;(map.getSource('events') as GeoJSONSource).setData(collection(p))
      ;(map.getSource('footprints') as GeoJSONSource).setData(collection(f))
      map.setFilter('event-selected', ['==', ['get', 'event_id'], s ?? ''])
      if (!fitted.current && p.length) {
        fitToPoints(map, p)
        fitted.current = true
      }
    })

    map.on('click', 'event-points', (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.event_id
      if (typeof id === 'string') latest.current.onSelect(id)
    })
    map.on('click', 'clusters', async (e: MapLayerMouseEvent) => {
      const feature = e.features?.[0]
      if (!feature || feature.geometry.type !== 'Point') return
      try {
        const zoom = await (map.getSource('events') as GeoJSONSource).getClusterExpansionZoom(feature.properties.cluster_id)
        map.easeTo({ center: feature.geometry.coordinates as [number, number], zoom })
      } catch {
        // The clusters were rebuilt (new data) between the click and the answer; the next click works.
      }
    })
    for (const layer of ['event-points', 'clusters']) {
      map.on('mouseenter', layer, () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', layer, () => (map.getCanvas().style.cursor = ''))
    }
    map.on('moveend', () => {
      const center = map.getCenter()
      latest.current.onMove({ zoom: map.getZoom(), lat: center.lat, lon: center.lng })
    })

    return () => {
      map.remove()
      mapRef.current = null
    }
    // The map is created once; initialView only seeds it.
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || shownBasemap.current === basemap.id) return
    shownBasemap.current = basemap.id
    loadBasemap(basemap)
  }, [basemap])

  // Before the first style load the events source does not exist yet; 'style.load' then uses the latest values.
  useEffect(() => {
    const map = mapRef.current
    const source = map?.getSource('events') as GeoJSONSource | undefined
    if (!map || !source) return
    source.setData(collection(points))
    if (!fitted.current && points.length) {
      fitToPoints(map, points)
      fitted.current = true
    }
  }, [points])

  useEffect(() => {
    ;(mapRef.current?.getSource('footprints') as GeoJSONSource | undefined)?.setData(collection(footprints))
  }, [footprints])

  useEffect(() => {
    const map = mapRef.current
    if (map?.getLayer('event-selected')) map.setFilter('event-selected', ['==', ['get', 'event_id'], selectedId ?? ''])
  }, [selectedId])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !flyTo) return
    map.easeTo({ center: [flyTo.lon, flyTo.lat], zoom: Math.max(map.getZoom(), 7) })
  }, [flyTo])

  /** Load the chosen basemap again after it failed (the tile host may be back). */
  function loadBasemap(next: Basemap) {
    styleLoaded.current = false
    usingFallback.current = false
    setFailedBasemap(null)
    mapRef.current?.setStyle(styleFor(next), { diff: false })
  }
  const retryBasemap = () => loadBasemap(latest.current.basemap)

  return (
    <div className="map-area">
      <main ref={container} className="map" />
      {failedBasemap ? (
        <p className="map-note" role="status">
          The {failedBasemap} map style did not load, so the events sit on a plain background.{' '}
          <button type="button" className="link" onClick={retryBasemap}>
            Try again
          </button>
        </p>
      ) : null}
    </div>
  )
}
