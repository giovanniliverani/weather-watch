// The MapLibre map. Loaded lazily (React.lazy in App) so the page shell paints before the map library arrives.
import {
  GeolocateControl,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  type GeoJSONSource,
  type MapLayerMouseEvent,
  type StyleSpecification,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef } from 'react'
import { BASEMAP_STYLE_URL, HAZARD_COLOURS, OTHER_COLOUR } from './config'
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

const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#dfe6ea' } }],
}

type CirclePaint = NonNullable<Extract<StyleSpecification['layers'][number], { type: 'circle' }>['paint']>
// A spread list cannot satisfy the expression's tuple type, hence the cast through unknown.
const hazardColour = ['match', ['get', 'hazard_type'], ...Object.entries(HAZARD_COLOURS).flat(), OTHER_COLOUR] as unknown as CirclePaint['circle-color']

const collection = <F,>(features: F[]) => ({ type: 'FeatureCollection' as const, features })

function addLayers(map: MapLibreMap) {
  map.addSource('footprints', { type: 'geojson', data: collection([]) })
  map.addLayer({
    id: 'footprint-fill',
    type: 'fill',
    source: 'footprints',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': '#d62728', 'fill-opacity': 0.12 },
  })
  map.addLayer({ id: 'footprint-line', type: 'line', source: 'footprints', paint: { 'line-color': '#7a1f1f', 'line-width': 1 } })

  map.addSource('events', {
    type: 'geojson',
    data: collection([]),
    cluster: true,
    clusterMaxZoom: 6,
    clusterRadius: 36,
  })
  map.addLayer({
    id: 'clusters',
    type: 'circle',
    source: 'events',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': '#ffffff',
      'circle-stroke-color': '#1a1a1a',
      'circle-stroke-width': 1,
      'circle-radius': ['step', ['get', 'point_count'], 11, 10, 14, 50, 18, 200, 23],
    },
  })
  if (map.getStyle().glyphs) {
    map.addLayer({
      id: 'cluster-count',
      type: 'symbol',
      source: 'events',
      filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 11, 'text-font': ['Noto Sans Regular'] },
      paint: { 'text-color': '#1a1a1a' },
    })
  }
  map.addLayer({
    id: 'event-points',
    type: 'circle',
    source: 'events',
    filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-color': hazardColour,
      'circle-radius': 6,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1,
    },
  })
  map.addLayer({
    id: 'event-selected',
    type: 'circle',
    source: 'events',
    filter: ['==', ['get', 'event_id'], ''],
    paint: { 'circle-color': 'rgba(0,0,0,0)', 'circle-radius': 11, 'circle-stroke-color': '#1a1a1a', 'circle-stroke-width': 2 },
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
      style: BASEMAP_STYLE_URL ?? EMPTY_STYLE,
      center: initialView ? [initialView.lon, initialView.lat] : [0, 20],
      zoom: initialView?.zoom ?? 1.5,
      attributionControl: { compact: true },
      dragRotate: false,
    })
    map.touchZoomRotate.disableRotation()
    map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right')
    map.addControl(new GeolocateControl({ positionOptions: { enableHighAccuracy: false }, trackUserLocation: false }), 'bottom-right')
    mapRef.current = map

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
      const feature = e.features?.[0]
      const id = feature?.properties?.event_id
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

  return <div ref={container} className="map" role="region" aria-label="Map of events" />
}
