import { useState } from 'react'
import DataTable from '../components/DataTable'
import { EmptyState, ErrorState } from '../components/Feedback'
import LiveEvents from '../components/LiveEvents'
import Panel from '../components/Panel'
import StatCard from '../components/StatCard'
import { useCallMutations, useDeadLetter, useQueueStats } from '../hooks/useApi'
import { formatDateTime, formatNumber, titleCase } from '../lib/format'

export default function QueuePage() {
  const stats = useQueueStats()
  const dead = useDeadLetter({ limit: 100 })
  const { requeue, purge } = useCallMutations()
  const [busyId, setBusyId] = useState(null)

  if (stats.isError) return <ErrorState error={stats.error} onRetry={stats.refetch} />

  const counters = stats.data?.counters || {}

  const act = async (action, id) => {
    setBusyId(id)
    try {
      await action.mutateAsync(id)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">Queue</h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Calls waiting, in flight, backing off, and those that gave up.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Ready" value={formatNumber(stats.data?.ready)} hint="claimable now" accent="var(--series-1)" />
        <StatCard label="In progress" value={formatNumber(stats.data?.processing)} hint="leased to a worker" />
        <StatCard label="Scheduled" value={formatNumber(stats.data?.scheduled)} hint="future or backing off" accent="var(--status-warning)" />
        <StatCard
          label="Dead letter"
          value={formatNumber(stats.data?.dead_letter)}
          hint="needs a human decision"
          accent={stats.data?.dead_letter ? 'var(--status-critical)' : undefined}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Lifetime counters" description="Since the queue was last reset" className="xl:col-span-2">
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {['enqueued', 'claimed', 'completed', 'retried', 'failed', 'dead_lettered', 'reclaimed', 'canceled'].map((key) => (
              <div key={key} className="rounded-lg p-3" style={{ background: 'var(--surface-sunken)' }}>
                <dt className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {titleCase(key)}
                </dt>
                <dd className="tabular mt-0.5 text-xl font-semibold">{formatNumber(counters[key] || 0)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs" style={{ color: 'var(--text-muted)' }}>
            <strong>Reclaimed</strong> counts calls whose worker lease expired mid-call — they are re-queued automatically.
          </p>
        </Panel>

        <Panel title="Live activity">
          <LiveEvents limit={30} />
        </Panel>
      </div>

      <Panel
        title="Dead letter queue"
        description="Calls that exhausted their retries or failed permanently. Requeue to try again, or purge to drop them."
      >
        <DataTable
          loading={dead.isLoading}
          rows={dead.data || []}
          keyField="call_id"
          empty={<EmptyState title="Nothing dead-lettered" description="Every call has either succeeded or is still being retried." />}
          columns={[
            { key: 'to_number', header: 'Number', render: (row) => <span className="tabular font-medium" style={{ color: 'var(--text-primary)' }}>{row.to_number}</span> },
            { key: 'priority', header: 'Priority', render: (row) => titleCase(row.priority) },
            { key: 'attempt', header: 'Attempts', align: 'right', render: (row) => <span className="tabular">{row.attempt}/{row.max_attempts}</span> },
            { key: 'last_error', header: 'Reason', render: (row) => <span title={row.last_error}>{row.last_error || '—'}</span> },
            { key: 'enqueued_at', header: 'Queued', align: 'right', render: (row) => <span className="tabular">{formatDateTime(row.enqueued_at)}</span> },
            {
              key: 'actions',
              header: '',
              align: 'right',
              render: (row) => (
                <span className="flex justify-end gap-2">
                  <button
                    type="button"
                    disabled={busyId === row.call_id}
                    onClick={() => act(requeue, row.call_id)}
                    className="focus-ring rounded-md px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                    style={{ background: 'var(--series-1)' }}
                  >
                    Requeue
                  </button>
                  <button
                    type="button"
                    disabled={busyId === row.call_id}
                    onClick={() => act(purge, row.call_id)}
                    className="focus-ring rounded-md border px-2 py-1 text-xs disabled:opacity-50"
                    style={{ borderColor: 'var(--border)', color: 'var(--status-critical)' }}
                  >
                    Purge
                  </button>
                </span>
              ),
            },
          ]}
        />
      </Panel>
    </div>
  )
}
