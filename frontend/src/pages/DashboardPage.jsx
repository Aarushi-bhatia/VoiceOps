import { Link } from 'react-router-dom'
import CallVolumeChart from '../components/charts/CallVolumeChart'
import CategoryBars from '../components/charts/CategoryBars'
import LatencyBars from '../components/charts/LatencyBars'
import { Legend } from '../components/charts/ChartFrame'
import DataTable from '../components/DataTable'
import { ErrorState, Spinner } from '../components/Feedback'
import LiveEvents from '../components/LiveEvents'
import Panel from '../components/Panel'
import StatCard from '../components/StatCard'
import StatusBadge from '../components/StatusBadge'
import { useCalls, useOverview, useQueueStats, useTimeseries } from '../hooks/useApi'
import { formatCents, formatDateTime, formatDuration, formatNumber, formatPercent } from '../lib/format'

export default function DashboardPage() {
  const overview = useOverview(7)
  const series = useTimeseries(14)
  const queue = useQueueStats()
  const recent = useCalls({ limit: 8 })

  if (overview.isLoading) return <Spinner label="Loading dashboard" />
  if (overview.isError) return <ErrorState error={overview.error} onRetry={overview.refetch} />

  const data = overview.data

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-lg font-semibold">Operations overview</h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Last 7 days of automated support calls.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="Calls" value={formatNumber(data.total_calls)} hint={`${formatNumber(data.in_flight)} in flight`} />
        <StatCard
          label="Connect rate"
          value={formatPercent(data.connect_rate)}
          hint="reached a conversation"
          accent="var(--series-1)"
        />
        <StatCard
          label="Resolved without a human"
          value={formatPercent(data.resolution_rate)}
          hint={`${formatPercent(data.escalation_rate)} escalated`}
          accent="var(--status-good)"
        />
        <StatCard label="Avg call length" value={formatDuration(data.avg_duration_seconds)} hint={`${data.avg_attempts} attempts avg`} />
        <StatCard label="Spend" value={formatCents(data.total_cost_cents)} hint="STT + LLM + TTS + telephony" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Call volume" description="Completed and failed calls per day" className="xl:col-span-2">
          {series.isLoading ? <Spinner /> : <CallVolumeChart data={series.data || []} />}
        </Panel>

        <Panel title="Queue health" description="Live queue depth and worker activity">
          {queue.isLoading ? (
            <Spinner />
          ) : (
            <div className="flex flex-col gap-3">
              <dl className="grid grid-cols-2 gap-3">
                <QueueStat label="Ready" value={queue.data.ready} />
                <QueueStat label="In progress" value={queue.data.processing} />
                <QueueStat label="Scheduled / retrying" value={queue.data.scheduled} />
                <QueueStat label="Dead letter" value={queue.data.dead_letter} tone={queue.data.dead_letter > 0 ? 'critical' : undefined} />
              </dl>
              <Link to="/queue" className="focus-ring text-xs underline" style={{ color: 'var(--series-1)' }}>
                Inspect the queue →
              </Link>
            </div>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="How calls ended" description="Outcome of every completed conversation">
          <CategoryBars data={(data.outcomes || []).map((o) => ({ name: o.outcome, value: o.count }))} />
        </Panel>

        <Panel title="Why calls failed" description="Failure category, split by whether it is retried">
          <CategoryBars
            data={(data.failures || []).map((f) => ({
              name: f.category,
              value: f.count,
              retryable: f.retryable,
              note: f.retryable ? 'retried with backoff' : 'permanent — no retry',
            }))}
            colorFor={(row, theme) => (row.retryable ? theme.series[0] : theme.series[1])}
          />
          <Legend
            items={[
              { label: 'Retried with backoff', color: 'var(--series-1)' },
              { label: 'Permanent', color: 'var(--series-2)' },
            ]}
          />
        </Panel>

        <Panel title="Agent response latency" description="Per customer turn, speech recognised to reply started">
          <LatencyBars latency={data.turn_latency} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Recent calls" className="xl:col-span-2" actions={<Link to="/calls" className="focus-ring text-xs underline" style={{ color: 'var(--series-1)' }}>View all</Link>}>
          <DataTable
            loading={recent.isLoading}
            rows={recent.data?.items || []}
            columns={[
              { key: 'to_number', header: 'Number', render: (row) => <span className="tabular">{row.to_number}</span> },
              { key: 'status', header: 'Status', render: (row) => <StatusBadge value={row.status} /> },
              { key: 'outcome', header: 'Outcome', render: (row) => <StatusBadge value={row.outcome} /> },
              { key: 'duration', header: 'Length', align: 'right', render: (row) => formatDuration(row.duration_seconds) },
              { key: 'created_at', header: 'Created', align: 'right', render: (row) => formatDateTime(row.created_at) },
            ]}
          />
        </Panel>

        <Panel title="Live activity" description="Streamed from the workers">
          <LiveEvents />
        </Panel>
      </div>
    </div>
  )
}

function QueueStat({ label, value, tone }) {
  return (
    <div className="rounded-lg p-3" style={{ background: 'var(--surface-sunken)' }}>
      <dt className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {label}
      </dt>
      <dd className="tabular mt-1 text-2xl font-semibold" style={{ color: tone === 'critical' ? 'var(--status-critical)' : 'var(--text-primary)' }}>
        {formatNumber(value)}
      </dd>
    </div>
  )
}
