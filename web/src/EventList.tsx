// Every shown event as a list, so each one is reachable by keyboard and screen reader without the map.
import { memo } from 'react'
import { hazardName } from './format'
import HazardSymbol from './HazardSymbol'
import { severityMark } from './symbols'
import type { EventFeature } from './types'

interface Props {
  points: EventFeature[]
  selectedId: string | null
  onSelect: (eventId: string) => void
}

function EventList({ points, selectedId, onSelect }: Props) {
  if (points.length === 0) return <p className="quiet">No events match these filters.</p>
  return (
    <ul className="event-list" aria-label="Events shown on the map">
      {points.map(({ properties: p }) => (
        <li key={p.event_id}>
          <button type="button" aria-current={p.event_id === selectedId ? 'true' : undefined} onClick={() => onSelect(p.event_id)}>
            <HazardSymbol hazard={p.hazard_type} ended={p.status === 'ended'} mark={severityMark(p.severity_label)} size={16} />
            <span className="event-title">{p.title}</span>
            <span className="event-meta">
              {hazardName(p.hazard_type)} · {p.severity_label ?? 'severity not stated'}
              {p.status === 'ended' ? ' · ended' : ''}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

export default memo(EventList)
