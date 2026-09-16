import { useEffect } from 'react'
import { useCall, useCallMutations } from '../hooks/useApi'
import { formatCents, formatDateTime, formatDuration, titleCase } from '../lib/format'
import { Spinner } from './Feedback'
import StatusBadge from './StatusBadge'
import TranscriptView, { EventTimeline, NodePath } from './TranscriptView'

export default function CallDrawer({ callId, onClose }) {
  const { data: call, isLoading } = useCall(callId)
  const { cancel, retry } = useCallMutations()

  useEffect(() => {
    const onKey = (event) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const terminal = call && ['completed', 'failed', 'canceled'].includes(call.status)

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button type="button" aria-label="Close details" className="flex-1 bg-black/30" onClick={onClose} />
      <aside
        className="flex w-full max-w-xl flex-col overflow-y-auto border-l shadow-2xl"
        style={{ background: 'var(--surface-page)', borderColor: 'var(--border)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Call details"
      >
        <header className="sticky top-0 flex items-center justify-between gap-3 border-b px-4 py-3" style={{ borderColor: 'var(--border)', background: 'var(--surface-page)' }}>
          <div>
            <h2 className="tabular text-sm font-semibold">{call?.to_number || 'Call'}</h2>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {callId}
            </p>
          </div>
          <button type="button" onClick={onClose} className="focus-ring rounded-md border px-2 py-1 text-sm" style={{ borderColor: 'var(--border)' }}>
            Close
          </button>
        </header>

        {isLoading || !call ? (
          <Spinner label="Loading call" />
        ) : (
          <div className="flex flex-col gap-4 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge value={call.status} />
              {call.outcome ? <StatusBadge value={call.outcome} /> : null}
              {call.failure_category ? <StatusBadge value={call.failure_category} tone="critical" /> : null}
              <span className="tabular ml-auto text-xs" style={{ color: 'var(--text-muted)' }}>
                attempt {call.attempt}/{call.max_attempts}
              </span>
            </div>

            {call.failure_reason ? (
              <p className="rounded-lg p-3 text-sm" style={{ background: 'var(--surface-sunken)', color: 'var(--text-secondary)' }}>
                {call.failure_reason}
              </p>
            ) : null}

            {call.summary ? (
              <section>
                <h3 className="mb-1 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                  Summary
                </h3>
                <p className="text-sm">{call.summary}</p>
              </section>
            ) : null}

            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Field label="Queued" value={formatDateTime(call.queued_at)} />
              <Field label="Started" value={formatDateTime(call.started_at)} />
              <Field label="Ended" value={formatDateTime(call.ended_at)} />
              <Field label="Length" value={formatDuration(call.duration_seconds)} />
              <Field label="Priority" value={titleCase(call.priority)} />
              <Field label="Cost" value={formatCents(call.cost_cents)} />
              {call.next_retry_at ? <Field label="Next retry" value={formatDateTime(call.next_retry_at)} /> : null}
              {call.queue_position !== null && call.queue_position !== undefined ? (
                <Field label="Queue position" value={`#${call.queue_position + 1}`} />
              ) : null}
            </dl>

            {Object.keys(call.collected_data || {}).length ? (
              <section>
                <h3 className="mb-1 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                  Collected
                </h3>
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  {Object.entries(call.collected_data).map(([key, value]) => (
                    <Field key={key} label={titleCase(key)} value={String(value)} />
                  ))}
                </dl>
              </section>
            ) : null}

            <section>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                Path taken
              </h3>
              <NodePath path={pathFromEvents(call.events)} />
            </section>

            <section>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                Transcript
              </h3>
              <TranscriptView turns={call.turns} />
            </section>

            <section>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                Events
              </h3>
              <EventTimeline events={call.events} />
            </section>

            <div className="flex gap-2 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
              <button
                type="button"
                disabled={terminal || cancel.isPending}
                onClick={() => cancel.mutate(call.id)}
                className="focus-ring rounded-md border px-3 py-1.5 text-sm disabled:opacity-40"
                style={{ borderColor: 'var(--border)' }}
              >
                Cancel call
              </button>
              <button
                type="button"
                disabled={!terminal || retry.isPending}
                onClick={() => retry.mutate(call.id)}
                className="focus-ring rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-40"
                style={{ background: 'var(--series-1)' }}
              >
                Retry now
              </button>
            </div>
          </div>
        )}
      </aside>
    </div>
  )
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {label}
      </dt>
      <dd className="tabular">{value}</dd>
    </div>
  )
}

function pathFromEvents(events = []) {
  const completed = [...events].reverse().find((event) => event.type === 'completed')
  return completed?.payload?.node_path || []
}
