// The MapLibre map. Loaded lazily (React.lazy in App) so the page shell paints before the map library arrives.
import {
  GeolocateControl,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre looks for its worker beside its own file, which bundling moves; Vite bundles the worker and gives its address.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef } from 'react'
import { BASEMAP_STYLE_URL } from './config'
import { drawSymbol, iconId } from './symbols'
import type { EventFeature, FootprintFeature } from './types'
import type { MapView as View } from './url'

export interface FlyTarget {
  lon: number
  lat: number
  /** Changes on every request, so asking twice for the same place still moves the map. */
  nonce: number
}

interface Props {
  points: EventFeature[]
  footprints: FootprintFeature[]
  selectedId: string | null
  initialView: View | null
  flyTo: FlyTarget | null
  onSelect: (eventId: string) => void
  onMove: (view: View) => void
}

setWorkerUrl(workerUrl)

const SYMBOL_PX = 22
const LIGHT = '#e6e8eb'
const PANEL = '#1f242b'

const collection = <F,>(features: F[]) => ({ type: 'FeatureCollection' as const, features })

function addLayers(map: MapLibreMap) {
  map.addSource('footprints', { type: 'geojson', data: collection([]) })
  map.addLayer({
    id: 'footprint-fill',
    type: 'fill',
    source: 'footprints',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': LIGHT, 'fill-opacity': 0.08 },
  })
  map.addLayer({
    id: 'footprint-line',
    type: 'line',
    source: 'footprints',
    paint: { 'line-color': LIGHT, 'line-opacity': 0.55, 'line-width': 1, 'line-dasharray': [3, 2] },
  })

  map.addSource('events', { type: 'geojson', data: collection([]), cluster: true, clusterMaxZoom: 6, clusterRadius: 36 })
  map.addLayer({
    id: 'clusters',
    type: 'circle',
    source: 'events',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': PANEL,
      'circle-stroke-color': LIGHT,
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
    // Noto Sans Regular is the font the OpenFreeMap styles serve.
    layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 12, 'text-font': ['Noto Sans Regular'], 'text-allow-overlap': true },
    paint: { 'text-color': LIGHT },
  })
  map.addLayer({
    id: 'event-selected',
    type: 'circle',
    source: 'events',
    filter: ['==', ['get', 'event_id'], ''],
    paint: { 'circle-opacity': 0, 'circle-radius': 16, 'circle-stroke-color': LIGHT, 'circle-stroke-width': 2.5 },
  })
  map.addLayer({
    id: 'event-points',
    type: 'symbol',
    source: 'events',
    filter: ['!', ['has', 'point_count']],
    layout: {
      // Images are drawn on demand (styleimagemissing below), so any hazard_type the API sends gets a symbol.
      'icon-image': ['concat', 'hz-', ['get', 'hazard_type'], '-', ['case', ['==', ['get', 'status'], 'ended'], 'ended', 'active']],
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

export default function MapView({ points, footprints, selectedId, initialView, flyTo, onSelect, onMove }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const ready = useRef(false)
  const fitted = useRef(initialView !== null)
  const latest = useRef({ points, footprints, selectedId, onSelect, onMove })
  latest.current = { points, footprints, selectedId, onSelect, onMove }

  // Create the map once; data, selection and moves flow in through the effects below.
  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current!,
      style: BASEMAP_STYLE_URL,
      center: initialView ? [initialView.lon, initialView.lat] : [0, 20],
      zoom: initialView?.zoom ?? 1.5,
      // The tile credit stays spelled out, never folded into an info button.
      attributionControl: { compact: false },
      dragRotate: false,
    })
    map.touchZoomRotate.disableRotation()
    // Controls sit top-right: the bottom-right corner is kept free for a later "add an event" control.
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new GeolocateControl({ positionOptions: { enableHighAccuracy: false }, trackUserLocation: false }), 'top-right')
    mapRef.current = map

    map.on('styleimagemissing', (e) => {
      const match = /^hz-(.+)-(active|ended)$/.exec(e.id)
      if (!match || map.hasImage(e.id)) return
      const ratio = window.devicePixelRatio || 1
      map.addImage(iconId(match[1], match[2] === 'ended'), drawSymbol(match[1], match[2] === 'ended', SYMBOL_PX, ratio), { pixelRatio: ratio })
    })

    map.on('load', () => {
      addLayers(map)
      ready.current = true
      const { points: p, footprints: f, selectedId: s } = latest.current
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
      const zoom = await (map.getSource('events') as GeoJSONSource).getClusterExpansionZoom(feature.properties.cluster_id)
      map.easeTo({ center: feature.geometry.coordinates as [number, number], zoom })
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
      ready.current = false
      map.remove()
      mapRef.current = null
    }
    // The map is created once; initialView only seeds it.
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready.current) return
    ;(map.getSource('events') as GeoJSONSource).setData(collection(points))
    if (!fitted.current && points.length) {
      fitToPoints(map, points)
      fitted.current = true
    }
  }, [points])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready.current) return
    ;(map.getSource('footprints') as GeoJSONSource).setData(collection(footprints))
  }, [footprints])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready.current) return
    map.setFilter('event-selected', ['==', ['get', 'event_id'], selectedId ?? ''])
  }, [selectedId])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !flyTo) return
    map.easeTo({ center: [flyTo.lon, flyTo.lat], zoom: Math.max(map.getZoom(), 7) })
  }, [flyTo])

  return <div ref={container} className="map" role="region" aria-label="Map of events. Every event is also in the events list." />
}
