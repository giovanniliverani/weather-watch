// Every shown event as a list, so each one is reachable by keyboard and screen reader without the map.
import { memo } from 'react'
import { HAZARD_COLOURS, OTHER_COLOUR } from './config'
import { hazardName } from './format'
import type { EventFeature } from './types'

interface Props {
  points: EventFeature[]
  selectedId: string | null
  onSelect: (eventId: string) => void
}

function EventList({ points, selectedId, onSelect }: Props) {
  if (points.length === 0) return <p className="hint">No events match these filters.</p>
  return (
    <ul className="event-list" aria-label="Events shown on the map">
      {points.map(({ properties: p }) => (
        <li key={p.event_id}>
          <button type="button" aria-current={p.event_id === selectedId ? 'true' : undefined} onClick={() => onSelect(p.event_id)}>
            <span className="swatch" style={{ background: HAZARD_COLOURS[p.hazard_type] ?? OTHER_COLOUR }} aria-hidden="true" />
            <span className="event-title">{p.title}</span>
            <span className="event-meta">
              {hazardName(p.hazard_type)} · {p.severity_label ?? 'severity not stated'}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

export default memo(EventList)
