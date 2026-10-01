import { formatWhen, plural } from './format'
import type { Heartbeat } from './types'

interface Props {
  meta: Heartbeat | null
  shown: number | null
  loading: boolean
}

export default function StatusStrip({ meta, shown, loading }: Props) {
  if (!meta) {
    return (
      <p className="status" role="status">
        {loading ? 'Loading events…' : 'No data yet'}
      </p>
    )
  }
  return (
    <p className="status" role="status" data-stale={meta.pipeline_stale ? 'true' : undefined}>
      {meta.pipeline_stale ? <strong>Collection is behind. </strong> : null}
      Data as of {formatWhen(meta.data_as_of)} · {meta.missed_runs_7d} of {meta.expected_runs_7d} collector runs missed in 7 days
      {shown === null ? null : ` · ${plural(shown, 'event')} shown`}
      {loading ? ' · updating…' : null}
    </p>
  )
}
