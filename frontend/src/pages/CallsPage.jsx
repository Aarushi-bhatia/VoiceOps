import { useState } from 'react'
import CallDrawer from '../components/CallDrawer'
import DataTable from '../components/DataTable'
import { EmptyState, ErrorState } from '../components/Feedback'
import NewCallDialog from '../components/NewCallDialog'
import Panel from '../components/Panel'
import StatusBadge from '../components/StatusBadge'
import { useAgents, useCalls } from '../hooks/useApi'
import { formatDateTime, formatDuration, titleCase } from '../lib/format'

const STATUSES = ['queued', 'scheduled', 'dialing', 'in_progress', 'retrying', 'completed', 'failed', 'canceled']
const PAGE_SIZE = 25

export default function CallsPage() {
  const [filters, setFilters] = useState({ status: '', agent_id: '', to_number: '', failed_only: false })
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(false)

  const { data: agents } = useAgents({ limit: 100 })
  const query = useCalls({
    limit: PAGE_SIZE,
    offset,
    status: filters.status || undefined,
    agent_id: filters.agent_id || undefined,
    to_number: filters.to_number || undefined,
    failed_only: filters.failed_only || undefined,
  })

  const update = (patch) => {
    setFilters((current) => ({ ...current, ...patch }))
    setOffset(0)
  }

  if (query.isError) return <ErrorState error={query.error} onRetry={query.refetch} />

  const total = query.data?.total ?? 0
  const agentNames = Object.fromEntries((agents?.items || []).map((a) => [a.id, a.name]))

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Calls</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Every call the platform has placed, with its transcript and outcome.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="focus-ring rounded-md px-3 py-1.5 text-sm font-medium text-white"
          style={{ background: 'var(--series-1)' }}
        >
          Queue a call
        </button>
      </div>

      {/* Filters sit in one row above the table. */}
      <div className="card flex flex-wrap items-end gap-3 p-3">
        <Field label="Status">
          <select className="focus-ring w-40 rounded-md border px-2 py-1.5 text-sm" style={selectStyle} value={filters.status} onChange={(e) => update({ status: e.target.value })}>
            <option value="">All statuses</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {titleCase(status)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Agent">
          <select className="focus-ring w-52 rounded-md border px-2 py-1.5 text-sm" style={selectStyle} value={filters.agent_id} onChange={(e) => update({ agent_id: e.target.value })}>
            <option value="">All agents</option>
            {(agents?.items || []).map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Number contains">
          <input
            className="focus-ring w-40 rounded-md border px-2 py-1.5 text-sm"
            style={selectStyle}
            value={filters.to_number}
            onChange={(e) => update({ to_number: e.target.value })}
            placeholder="+1555…"
          />
        </Field>
        <label className="flex items-center gap-2 pb-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <input type="checkbox" className="focus-ring" checked={filters.failed_only} onChange={(e) => update({ failed_only: e.target.checked })} />
          Failures only
        </label>
        <span className="tabular ml-auto pb-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
          {total} call{total === 1 ? '' : 's'}
        </span>
      </div>

      <Panel>
        <DataTable
          loading={query.isLoading}
          rows={query.data?.items || []}
          onRowClick={(row) => setSelected(row.id)}
          empty={<EmptyState title="No calls match these filters" description="Clear a filter, or queue a new call to get started." />}
          columns={[
            { key: 'to_number', header: 'Number', render: (row) => <span className="tabular font-medium" style={{ color: 'var(--text-primary)' }}>{row.to_number}</span> },
            { key: 'agent', header: 'Agent', render: (row) => agentNames[row.agent_id] || '—' },
            { key: 'status', header: 'Status', render: (row) => <StatusBadge value={row.status} /> },
            { key: 'outcome', header: 'Outcome', render: (row) => <StatusBadge value={row.outcome} /> },
            { key: 'attempt', header: 'Attempt', align: 'right', render: (row) => <span className="tabular">{row.attempt}/{row.max_attempts}</span> },
            { key: 'duration', header: 'Length', align: 'right', render: (row) => <span className="tabular">{formatDuration(row.duration_seconds)}</span> },
            { key: 'created_at', header: 'Created', align: 'right', render: (row) => <span className="tabular">{formatDateTime(row.created_at)}</span> },
          ]}
          footer={
            total > PAGE_SIZE ? (
              <div className="flex items-center justify-between gap-3 pt-3 text-sm">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))} className="focus-ring rounded-md border px-3 py-1 disabled:opacity-40" style={{ borderColor: 'var(--border)' }}>
                  Previous
                </button>
                <span className="tabular text-xs" style={{ color: 'var(--text-muted)' }}>
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
                </span>
                <button type="button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)} className="focus-ring rounded-md border px-3 py-1 disabled:opacity-40" style={{ borderColor: 'var(--border)' }}>
                  Next
                </button>
              </div>
            ) : null
          }
        />
      </Panel>

      {selected ? <CallDrawer callId={selected} onClose={() => setSelected(null)} /> : null}
      {creating ? <NewCallDialog agents={agents?.items || []} onClose={() => setCreating(false)} /> : null}
    </div>
  )
}

const selectStyle = { borderColor: 'var(--border)', background: 'var(--surface-1)', color: 'var(--text-primary)' }

function Field({ label, children }) {
  return (
    <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      {label}
      {children}
    </label>
  )
}
