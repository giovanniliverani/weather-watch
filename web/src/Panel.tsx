// The side panel for the selected event: Details, News, Posts and Weather tabs (WAI-ARIA tabs pattern).
import { useId, useRef, useState, type KeyboardEvent } from 'react'
import { fetchDocuments, fetchForecast } from './api'
import { formatDay, formatWhen, hazardName, measure, plural } from './format'
import HazardSymbol from './HazardSymbol'
import type { DocumentItem, EventFeature, Forecast } from './types'
import { useResource, type Resource } from './useResource'

const TABS = ['Details', 'News', 'Posts', 'Weather'] as const
type Tab = (typeof TABS)[number]

// Answers kept for the session: reopening an event or its Weather tab does not ask the API again.
const documentCache = new Map<string, DocumentItem[]>()
const forecastCache = new Map<string, Forecast>()

interface Props {
  event: EventFeature
  onClose: () => void
}

export default function Panel({ event, onClose }: Props) {
  const p = event.properties
  const [lon, lat] = event.geometry.coordinates
  const [tab, setTab] = useState<Tab>('Details')
  // The event whose forecast was asked for by hovering or focusing the Weather tab; other events wait for a click.
  const [weatherWantedFor, setWeatherWantedFor] = useState<string | null>(null)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const baseId = useId()

  const documents = useResource(p.event_id, (signal) => fetchDocuments(p.event_id, signal), documentCache)
  const forecastKey = `${lat.toFixed(4)},${lon.toFixed(4)}`
  const forecast = useResource(weatherWantedFor === p.event_id || tab === 'Weather' ? forecastKey : null, (signal) => fetchForecast(lat, lon, signal), forecastCache)

  function onTabKey(e: KeyboardEvent, index: number) {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    const target = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : step ? (index + step + TABS.length) % TABS.length : null
    if (target === null) return
    e.preventDefault()
    setTab(TABS[target])
    tabRefs.current[target]?.focus()
  }

  return (
    <aside className="panel" aria-labelledby={`${baseId}-title`}>
      <header className="panel-head">
        <HazardSymbol hazard={p.hazard_type} ended={p.status === 'ended'} size={28} />
        <div>
          <h2 id={`${baseId}-title`}>{p.title}</h2>
          <p className="quiet">
            {hazardName(p.hazard_type)} · {p.severity_label ?? 'severity not stated'}
            {p.status === 'ended' ? ' · ended' : ''}
          </p>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close event">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <div role="tablist" aria-label="Event information" className="tabs">
        {TABS.map((name, index) => (
          <button
            key={name}
            ref={(el) => {
              tabRefs.current[index] = el
            }}
            type="button"
            role="tab"
            id={`${baseId}-tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`${baseId}-panel-${name}`}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => setTab(name)}
            onKeyDown={(e) => onTabKey(e, index)}
            onPointerEnter={name === 'Weather' ? () => setWeatherWantedFor(p.event_id) : undefined}
            onFocus={name === 'Weather' ? () => setWeatherWantedFor(p.event_id) : undefined}
          >
            {name}
            {name === 'News' && p.doc_count ? <span className="count">{p.doc_count}</span> : null}
            {name === 'Posts' && p.post_count + p.video_count ? <span className="count">{p.post_count + p.video_count}</span> : null}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${baseId}-panel-${tab}`} aria-labelledby={`${baseId}-tab-${tab}`} tabIndex={0} className="tab-body">
        {tab === 'Details' ? <Details event={event} /> : null}
        {tab === 'News' ? <DocumentList resource={documents} kinds={['article', 'report']} empty="No articles or reports attached to this event yet." /> : null}
        {tab === 'Posts' ? <DocumentList resource={documents} kinds={['post', 'video']} empty="No posts, photos or videos attached to this event yet." /> : null}
        {tab === 'Weather' ? <Weather resource={forecast} /> : null}
      </div>
    </aside>
  )
}

function Details({ event }: { event: EventFeature }) {
  const p = event.properties
  const [lon, lat] = event.geometry.coordinates
  return (
    <>
      {p.ems_activation ? <p className="badge">Copernicus EMS activation</p> : null}
      <dl className="facts">
        <dt>Hazard</dt>
        <dd>{hazardName(p.hazard_type)}</dd>
        <dt>Severity</dt>
        <dd>{p.severity_label ?? 'not stated'}</dd>
        <dt>Status</dt>
        <dd>{p.status}</dd>
        <dt>Started</dt>
        <dd>{formatWhen(p.started_at)}</dd>
        {p.ended_at ? (
          <>
            <dt>Ended</dt>
            <dd>{formatWhen(p.ended_at)}</dd>
          </>
        ) : null}
        <dt>Last observed</dt>
        <dd>{formatWhen(p.last_observed_at)}</dd>
        <dt>Country</dt>
        <dd>{p.country_iso3 ?? 'not stated'}</dd>
        <dt>Position</dt>
        <dd>
          {lat.toFixed(3)}, {lon.toFixed(3)} ({p.precision})
        </dd>
        <dt>Sources</dt>
        <dd>{p.source_ids.join(', ') || 'none'}</dd>
        {p.glide_number ? (
          <>
            <dt>GLIDE</dt>
            <dd>{p.glide_number}</dd>
          </>
        ) : null}
      </dl>
      {p.summary ? (
        <section className="summary">
          <h3>Summary</h3>
          <p>{p.summary}</p>
          {p.summary_updated_at ? <p className="hint">As of {formatWhen(p.summary_updated_at)}</p> : null}
        </section>
      ) : null}
      {p.detail_url ? (
        <p>
          <a href={p.detail_url} target="_blank" rel="noreferrer">
            Open the source's page
          </a>
        </p>
      ) : null}
    </>
  )
}

function Media({ item }: { item: DocumentItem }) {
  const [broken, setBroken] = useState(false)
  if (!item.media_url || broken) return null
  if (item.media_kind === 'video') {
    return <video className="media" src={item.media_url} controls preload="none" onError={() => setBroken(true)} />
  }
  if (item.media_kind !== 'image') return null
  return (
    <a href={item.url} target="_blank" rel="noreferrer" tabIndex={-1} aria-hidden="true">
      <img className="media" src={item.media_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />
    </a>
  )
}

function DocumentList({ resource, kinds, empty }: { resource: Resource<DocumentItem[]>; kinds: DocumentItem['kind'][]; empty: string }) {
  if (resource.state === 'loading' || resource.state === 'idle') return <p className="hint">Loading…</p>
  if (resource.state === 'error') return <p className="error">Could not load documents: {resource.error.message}</p>
  // Splitting by kind into the two tabs is presentation, as in app.py.
  const items = resource.data.filter((item) => kinds.includes(item.kind))
  if (items.length === 0) return <p className="hint">{empty}</p>
  return (
    <ul className="documents">
      {items.map((item) => {
        const who = item.author ?? item.publisher ?? item.source_id
        const text = item.kind === 'post' ? (item.text_excerpt ?? item.title) : (item.title ?? item.text_excerpt)
        return (
          <li key={item.document_id}>
            <a href={item.url} target="_blank" rel="noreferrer" className="doc-title">
              {text ?? item.url}
            </a>
            <p className="hint">
              {who} · {formatWhen(item.published_at)}
              {item.copies > 1 ? ` · ${plural(item.copies, 'copy')} from ${item.publishers.join(', ') || 'one source'}` : null}
            </p>
            <Media item={item} />
          </li>
        )
      })}
    </ul>
  )
}

function Weather({ resource }: { resource: Resource<Forecast> }) {
  if (resource.state === 'loading' || resource.state === 'idle') return <p className="hint">Loading the forecast…</p>
  if (resource.state === 'error') {
    return <p className="error">{resource.error.message.startsWith('Open-Meteo') ? 'Open-Meteo did not answer. Try again in a minute.' : resource.error.message}</p>
  }
  const { current, daily, attribution } = resource.data
  return (
    <>
      <p className="now">
        <span className="temp">{measure(current.temperature_c, '°C')}</span> {current.conditions}
      </p>
      <p className="hint">
        Precipitation {measure(current.precipitation_mm, 'mm')} · wind {measure(current.wind_kmh, 'km/h')}
        {current.observed_at ? ` · observed ${current.observed_at.replace('T', ' ')} local time` : null}
      </p>
      <table className="forecast">
        <caption>Next {daily.length} days</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Conditions</th>
            <th scope="col">High</th>
            <th scope="col">Low</th>
            <th scope="col">Rain</th>
          </tr>
        </thead>
        <tbody>
          {daily.map((day) => (
            <tr key={day.date}>
              <th scope="row">{formatDay(day.date)}</th>
              <td>{day.conditions}</td>
              <td>{measure(day.high_c, '°C')}</td>
              <td>{measure(day.low_c, '°C')}</td>
              <td>{measure(day.precipitation_mm, 'mm')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="credit">{attribution}</p>
    </>
  )
}
