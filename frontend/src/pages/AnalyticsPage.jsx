import { useState } from 'react'
import CallVolumeChart from '../components/charts/CallVolumeChart'
import CategoryBars from '../components/charts/CategoryBars'
import { Legend } from '../components/charts/ChartFrame'
import LatencyBars from '../components/charts/LatencyBars'
import DataTable from '../components/DataTable'
import { ErrorState, Spinner } from '../components/Feedback'
import Panel from '../components/Panel'
import StatCard from '../components/StatCard'
import { useAgentPerformance, useOverview, useTimeseries } from '../hooks/useApi'
import { formatCents, formatDuration, formatNumber, formatPercent } from '../lib/format'

const RANGES = [
  { days: 1, label: 'Today' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
]

export default function AnalyticsPage() {
  const [days, setDays] = useState(30)
  const overview = useOverview(days)
  const series = useTimeseries(days, days <= 2 ? 'hour' : 'day')
  const agents = useAgentPerformance(days)

  if (overview.isError) return <ErrorState error={overview.error} onRetry={overview.refetch} />
  if (overview.isLoading) return <Spinner label="Loading analytics" />

  const data = overview.data

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Analytics</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Outcomes, failures and cost across the selected window.
          </p>
        </div>
        {/* A single row of range presets sits above the charts. */}
        <div className="flex items-center gap-1 rounded-lg border p-1" style={{ borderColor: 'var(--border)' }} role="group" aria-label="Time range">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              onClick={() => setDays(range.days)}
              aria-pressed={days === range.days}
              className="focus-ring rounded-md px-2.5 py-1 text-sm"
              style={{
                background: days === range.days ? 'var(--surface-1)' : 'transparent',
                color: days === range.days ? 'var(--text-primary)' : 'var(--text-secondary)',
                border: `1px solid ${days === range.days ? 'var(--border)' : 'transparent'}`,
              }}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <StatCard label="Calls" value={formatNumber(data.total_calls)} />
        <StatCard label="Completed" value={formatNumber(data.completed)} />
        <StatCard label="Failed" value={formatNumber(data.failed)} accent={data.failed ? 'var(--status-critical)' : undefined} />
        <StatCard label="Connect rate" value={formatPercent(data.connect_rate)} />
        <StatCard label="Resolution rate" value={formatPercent(data.resolution_rate)} accent="var(--status-good)" />
        <StatCard label="Spend" value={formatCents(data.total_cost_cents)} />
      </div>

      <Panel title="Call volume" description={`Completed and failed calls per ${days <= 2 ? 'hour' : 'day'}`}>
        {series.isLoading ? <Spinner /> : <CallVolumeChart data={series.data || []} height={300} />}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="How calls ended" description="Outcome of every completed conversation">
          <CategoryBars data={(data.outcomes || []).map((o) => ({ name: o.outcome, value: o.count }))} />
        </Panel>
        <Panel title="Why calls failed" description="Split by whether the queue retries it">
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
        <Panel title="Agent response latency" description="Per customer turn">
          <LatencyBars latency={data.turn_latency} />
        </Panel>
      </div>

      <Panel title="Per-agent performance" description="Ranked by call volume in this window">
        <DataTable
          loading={agents.isLoading}
          rows={agents.data || []}
          keyField="agent_id"
          columns={[
            { key: 'agent_name', header: 'Agent', render: (row) => <span className="font-medium" style={{ color: 'var(--text-primary)' }}>{row.agent_name}</span> },
            { key: 'total_calls', header: 'Calls', align: 'right', render: (row) => <span className="tabular">{formatNumber(row.total_calls)}</span> },
            { key: 'completed', header: 'Completed', align: 'right', render: (row) => <span className="tabular">{formatNumber(row.completed)}</span> },
            { key: 'failed', header: 'Failed', align: 'right', render: (row) => <span className="tabular">{formatNumber(row.failed)}</span> },
            { key: 'resolution_rate', header: 'Resolved', align: 'right', render: (row) => <span className="tabular">{formatPercent(row.resolution_rate)}</span> },
            { key: 'avg_turns', header: 'Avg turns', align: 'right', render: (row) => <span className="tabular">{row.avg_turns}</span> },
            { key: 'avg_duration_seconds', header: 'Avg length', align: 'right', render: (row) => <span className="tabular">{formatDuration(row.avg_duration_seconds)}</span> },
            { key: 'total_cost_cents', header: 'Cost', align: 'right', render: (row) => <span className="tabular">{formatCents(row.total_cost_cents)}</span> },
          ]}
        />
      </Panel>
    </div>
  )
}
