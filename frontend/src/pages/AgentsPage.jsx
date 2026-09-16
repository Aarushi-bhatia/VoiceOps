import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import DataTable from '../components/DataTable'
import { EmptyState, ErrorState } from '../components/Feedback'
import Panel from '../components/Panel'
import StatusBadge from '../components/StatusBadge'
import { useAgentMutations, useAgents } from '../hooks/useApi'
import { formatDateTime } from '../lib/format'
import { STARTER_WORKFLOW } from '../lib/workflowTemplates'

export default function AgentsPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [error, setError] = useState(null)
  const query = useAgents({ search: search || undefined, limit: 100 })
  const { create } = useAgentMutations()

  const addAgent = async () => {
    setError(null)
    try {
      const agent = await create.mutateAsync({
        name: `New agent ${new Date().toLocaleTimeString()}`,
        description: 'Draft agent — edit the workflow before using it.',
        system_prompt: 'You are a concise, friendly support agent.',
        workflow: STARTER_WORKFLOW,
        is_active: false,
      })
      navigate(`/agents/${agent.id}`)
    } catch (err) {
      setError(err.message)
    }
  }

  if (query.isError) return <ErrorState error={query.error} onRetry={query.refetch} />

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Agents</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Each agent is a persona plus a conversation workflow.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            className="focus-ring rounded-md border px-2 py-1.5 text-sm"
            style={{ borderColor: 'var(--border)', background: 'var(--surface-1)', color: 'var(--text-primary)' }}
            placeholder="Search agents"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button type="button" onClick={addAgent} disabled={create.isPending} className="focus-ring rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" style={{ background: 'var(--series-1)' }}>
            New agent
          </button>
        </div>
      </div>

      {error ? (
        <p className="text-sm" style={{ color: 'var(--status-critical)' }}>
          ■ {error}
        </p>
      ) : null}

      <Panel>
        <DataTable
          loading={query.isLoading}
          rows={query.data?.items || []}
          onRowClick={(row) => navigate(`/agents/${row.id}`)}
          empty={<EmptyState title="No agents yet" description="Create one to start automating calls." />}
          columns={[
            {
              key: 'name',
              header: 'Name',
              render: (row) => (
                <div>
                  <div className="font-medium" style={{ color: 'var(--text-primary)' }}>
                    {row.name}
                  </div>
                  {row.description ? <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{row.description}</div> : null}
                </div>
              ),
            },
            { key: 'is_active', header: 'State', render: (row) => <StatusBadge value={row.is_active ? 'active' : 'inactive'} tone={row.is_active ? 'good' : 'neutral'} /> },
            { key: 'nodes', header: 'Nodes', align: 'right', render: (row) => <span className="tabular">{row.workflow?.nodes?.length ?? 0}</span> },
            { key: 'version', header: 'Version', align: 'right', render: (row) => <span className="tabular">v{row.version}</span> },
            { key: 'voice_id', header: 'Voice' },
            { key: 'updated_at', header: 'Updated', align: 'right', render: (row) => <span className="tabular">{formatDateTime(row.updated_at)}</span> },
          ]}
        />
      </Panel>
    </div>
  )
}
