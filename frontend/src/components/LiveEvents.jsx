import { useEventStream } from '../hooks/useEventStream'
import { relativeTime, titleCase } from '../lib/format'

const TONE = {
  completed: 'var(--status-good)',
  failed: 'var(--status-critical)',
  dead_lettered: 'var(--status-critical)',
  retry_scheduled: 'var(--status-warning)',
  canceled: 'var(--status-neutral)',
  transferred: 'var(--status-warning)',
  answered: 'var(--series-1)',
  dialing: 'var(--series-1)',
  enqueued: 'var(--status-neutral)',
}

export default function LiveEvents({ limit = 40 }) {
  const { events, connected } = useEventStream({ limit })

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
        <span aria-hidden="true" style={{ color: connected ? 'var(--status-good)' : 'var(--status-warning)' }}>
          {connected ? '●' : '▲'}
        </span>
        {connected ? 'Connected to the worker event stream' : 'Reconnecting…'}
      </div>

      {events.length === 0 ? (
        <p className="py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
          Waiting for activity. Queue a call to see events appear here.
        </p>
      ) : (
        <ul className="flex max-h-[320px] flex-col gap-1 overflow-y-auto pr-1">
          {events.map((event) => (
            <li key={event.key} className="flex items-baseline gap-2 rounded-md px-2 py-1.5 text-xs" style={{ background: 'var(--surface-sunken)' }}>
              <span aria-hidden="true" style={{ color: TONE[event.type] || 'var(--text-muted)' }}>
                ●
              </span>
              <span className="font-medium">{titleCase(event.type)}</span>
              <span className="truncate" style={{ color: 'var(--text-muted)' }}>
                {describe(event)}
              </span>
              <span className="tabular ml-auto shrink-0" style={{ color: 'var(--text-muted)' }}>
                {relativeTime(event.ts)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function describe(event) {
  const payload = event.payload || {}
  if (event.type === 'turn') return `${payload.role}: ${payload.text}`
  if (payload.outcome) return `outcome ${payload.outcome}`
  if (payload.reason) return payload.reason
  if (payload.intent) return `intent ${payload.intent}`
  if (payload.to) return payload.to
  return event.call_id ? `call ${String(event.call_id).slice(0, 8)}` : ''
}
