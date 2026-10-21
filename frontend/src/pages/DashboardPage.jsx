import { Link } from 'react-router-dom'
import CallVolumeChart from '../components/charts/CallVolumeChart'
import DataTable from '../components/DataTable'
import { ErrorState, Spinner } from '../components/Feedback'
import Panel from '../components/Panel'
import StatusBadge from '../components/StatusBadge'
import { useCalls, useHealth, useOverview, useQueueStats, useTimeseries } from '../hooks/useApi'
import { formatCents, formatDateTime, formatDuration, formatNumber, formatPercent } from '../lib/format'

const LINK_STYLE = { color: 'var(--series-1)' }

export default function DashboardPage() {
  const overview = useOverview(7)
  const series = useTimeseries(14)
  const queue = useQueueStats()
  const recent = useCalls({ limit: 8 })
  const health = useHealth()

  if (overview.isLoading) return <Spinner label="Loading dashboard" />
  if (overview.isError) return <ErrorState error={overview.error} onRetry={overview.refetch} />

  const data = overview.data
  const finished = data.completed + data.failed
  // Spend is priced from per-provider rates. With every provider mocked those
  // rates apply to calls that never happened, so the figure would mislead.
  const showSpend = Object.values(health.data?.providers || {}).some((name) => name !== 'mock')

  const metrics = [
    { label: 'Calls', value: formatNumber(data.total_calls), hint: `${formatNumber(data.in_flight)} in flight` },
    { label: 'Connect rate', value: formatPercent(data.connect_rate), hint: `${formatNumber(data.completed)} of ${formatNumber(finished)} finished calls` },
    { label: 'Resolved without a human', value: formatPercent(data.resolution_rate), hint: `${formatPercent(data.escalation_rate)} escalated` },
    { label: 'Avg call length', value: formatDuration(data.avg_duration_seconds), hint: `${data.avg_attempts} attempts avg` },
    ...(showSpend ? [{ label: 'Spend', value: formatCents(data.total_cost_cents), hint: 'estimated from provider rates' }] : []),
  ]

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Operations overview</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--text-muted)' }}>
          Automated support calls over the last 7 days.
        </p>
      </div>

      {/* One strip of hairline-separated cells reads as a single summary, not five competing boxes. */}
      <section
        aria-label="Key metrics"
        className={`grid grid-cols-2 gap-px overflow-hidden rounded-[10px] border ${showSpend ? 'lg:grid-cols-5' : 'lg:grid-cols-4'}`}
        style={{ borderColor: 'var(--border)', background: 'var(--border)' }}
      >
        {metrics.map((metric) => (
          <div key={metric.label} className="flex flex-col gap-1.5 px-5 py-4" style={{ background: 'var(--surface-1)' }}>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {metric.label}
            </span>
            <span className="tabular text-2xl font-semibold leading-none tracking-tight">{metric.value}</span>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {metric.hint}
            </span>
          </div>
        ))}
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel
          title="Call volume"
          description="Completed and failed calls per day, last 14 days"
          className="xl:col-span-2"
          actions={
            <Link to="/analytics" className="focus-ring text-xs underline" style={LINK_STYLE}>
              Failures and latency
            </Link>
          }
        >
          {series.isLoading ? <Spinner /> : <CallVolumeChart data={series.data || []} />}
        </Panel>

        <Panel
          title="Queue"
          description="Live from the queue, refreshed every 5 seconds"
          actions={
            <Link to="/queue" className="focus-ring text-xs underline" style={LINK_STYLE}>
              Inspect
            </Link>
          }
        >
          {queue.isLoading ? <Spinner /> : <QueuePipeline stats={queue.data} />}
        </Panel>
      </div>

      <Panel
        title="Recent calls"
        actions={
          <Link to="/calls" className="focus-ring text-xs underline" style={LINK_STYLE}>
            View all
          </Link>
        }
      >
        <DataTable
          loading={recent.isLoading}
          rows={recent.data?.items || []}
          columns={[
            { key: 'to_number', header: 'Number', render: (row) => <span className="mono">{row.to_number}</span> },
            { key: 'status', header: 'Status', render: (row) => <StatusBadge value={row.status} /> },
            { key: 'outcome', header: 'Outcome', render: (row) => <StatusBadge value={row.outcome} /> },
            { key: 'duration', header: 'Length', align: 'right', render: (row) => <span className="tabular">{formatDuration(row.duration_seconds)}</span> },
            { key: 'created_at', header: 'Created', align: 'right', render: (row) => <span className="tabular">{formatDateTime(row.created_at)}</span> },
          ]}
        />
      </Panel>
    </div>
  )
}

/**
 * The queue drawn as the path a call takes: parked until due, ready to claim,
 * leased to a worker. Dead letter sits apart because it leaves that path and
 * waits for a person.
 */
function QueuePipeline({ stats }) {
  const stages = [
    { label: 'Scheduled', hint: 'waiting for a time or a retry', value: stats.scheduled },
    { label: 'Ready', hint: 'can be claimed now', value: stats.ready },
    { label: 'In progress', hint: 'leased to a worker', value: stats.processing },
  ]
  const dead = stats.dead_letter

  return (
    <div className="flex h-full flex-col gap-4">
      <ol className="flex flex-col">
        {stages.map((stage, index) => (
          <li key={stage.label} className="relative flex items-start gap-3 pb-4 last:pb-0">
            {index < stages.length - 1 ? (
              <span aria-hidden="true" className="absolute top-3 left-[5px] h-full w-px" style={{ background: 'var(--baseline)' }} />
            ) : null}
            <span
              aria-hidden="true"
              className="relative mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full border-2"
              style={{
                borderColor: stage.value ? 'var(--series-1)' : 'var(--baseline)',
                background: stage.value ? 'var(--series-1)' : 'var(--surface-1)',
              }}
            />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">{stage.label}</span>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {stage.hint}
              </span>
            </span>
            <span className="mono text-lg" style={{ color: stage.value ? 'var(--text-primary)' : 'var(--text-muted)' }}>
              {formatNumber(stage.value)}
            </span>
          </li>
        ))}
      </ol>

      <div
        className="mt-auto flex items-center gap-3 rounded-lg px-3 py-2.5"
        style={{
          background: dead ? 'color-mix(in srgb, var(--status-critical) 12%, transparent)' : 'var(--surface-sunken)',
        }}
      >
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm" style={{ color: dead ? 'var(--status-critical)' : 'var(--text-primary)' }}>
            Dead letter
          </span>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {dead ? 'needs a person to retry or drop' : 'nothing needs a person'}
          </span>
        </span>
        <span className="mono text-lg" style={{ color: dead ? 'var(--status-critical)' : 'var(--text-muted)' }}>
          {formatNumber(dead)}
        </span>
      </div>
    </div>
  )
}
