// The shapes `eww serve` returns (docs/architecture.md section 2). The frontend reads them and never derives data.
import type { Geometry } from 'geojson'

export interface EventProperties {
  event_id: string
  hazard_type: string
  title: string
  status: string
  started_at: string | null
  ended_at: string | null
  last_observed_at: string | null
  severity_score: number | null
  severity_label: string | null
  country_iso3: string | null
  precision: string
  glide_number: string | null
  source_ids: string[]
  ems_activation: boolean
  doc_count: number
  post_count: number
  video_count: number
  summary: string | null
  summary_updated_at: string | null
  thumbnail_url: string | null
  detail_url: string | null
}

export interface EventFeature {
  type: 'Feature'
  id: string
  geometry: { type: 'Point'; coordinates: [number, number] }
  properties: EventProperties
}

export interface FootprintFeature {
  type: 'Feature'
  geometry: Geometry
  properties: { event_id: string; role: string; observed_at: string | null; source_id: string }
}

export interface Heartbeat {
  data_as_of: string | null
  last_collector_run_at: string | null
  missed_runs_7d: number
  expected_runs_7d: number
  generated_at: string
  /** True when collection is behind (the rule lives in eww.api); the strip turns red on it. */
  pipeline_stale: boolean
}

export interface EventCollection {
  type: 'FeatureCollection'
  features: (EventFeature | FootprintFeature)[]
  meta: Heartbeat
}

export interface DocumentItem {
  document_id: string
  source_id: string
  kind: 'article' | 'report' | 'post' | 'video'
  title: string | null
  text_excerpt: string | null
  url: string
  author: string | null
  publisher: string | null
  published_at: string | null
  media_url: string | null
  media_kind: string | null
  copies: number
  publishers: string[]
  urls: string[]
}

export interface Attribution {
  id: string
  name: string
  attribution: string
  terms_url: string | null
}

export interface Forecast {
  attribution: string
  current: {
    temperature_c: number | null
    precipitation_mm: number | null
    wind_kmh: number | null
    conditions: string
    observed_at: string | null
  }
  daily: { date: string; high_c: number | null; low_c: number | null; precipitation_mm: number | null; conditions: string }[]
}
