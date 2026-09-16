import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ErrorState, Spinner } from '../components/Feedback'
import Panel from '../components/Panel'
import StatCard from '../components/StatCard'
import StatusBadge from '../components/StatusBadge'
import TranscriptView, { NodePath } from '../components/TranscriptView'
import WorkflowEditor from '../components/WorkflowEditor'
import { useAgent, useAgentMutations, useAgentStats } from '../hooks/useApi'
import { formatDuration, formatNumber, formatPercent } from '../lib/format'

const input = { borderColor: 'var(--border)', background: 'var(--surface-1)', color: 'var(--text-primary)' }

export default function AgentDetailPage() {
  const { agentId } = useParams()
  const navigate = useNavigate()
  const { data: agent, isLoading, isError, error, refetch } = useAgent(agentId)
  const { data: stats } = useAgentStats(agentId)
  const { update, remove, simulate } = useAgentMutations()

  const [draft, setDraft] = useState(null)
  const [message, setMessage] = useState(null)
  const [simulation, setSimulation] = useState(null)

  useEffect(() => {
    if (agent) setDraft(agent)
  }, [agent])

  if (isLoading || !draft) return <Spinner label="Loading agent" />
  if (isError) return <ErrorState error={error} onRetry={refetch} />

  const dirty = JSON.stringify(draft) !== JSON.stringify(agent)

  const save = async () => {
    setMessage(null)
    try {
      await update.mutateAsync({
        id: agentId,
        body: {
          name: draft.name,
          description: draft.description,
          system_prompt: draft.system_prompt,
          voice_id: draft.voice_id,
          language: draft.language,
          llm_model: draft.llm_model,
          max_turns: draft.max_turns,
          max_call_seconds: draft.max_call_seconds,
          is_active: draft.is_active,
          workflow: draft.workflow,
        },
      })
      setMessage({ tone: 'good', text: 'Saved.' })
    } catch (err) {
      setMessage({ tone: 'bad', text: err.message })
    }
  }

  const runSimulation = async () => {
    setMessage(null)
    try {
      const result = await simulate.mutateAsync({
        id: agentId,
        body: { to_number: '+15551110001111', context: { customer_name: 'Dana' } },
      })
      setSimulation(result)
    } catch (err) {
      setMessage({ tone: 'bad', text: err.message })
    }
  }

  const destroy = async () => {
    setMessage(null)
    try {
      await remove.mutateAsync(agentId)
      navigate('/agents')
    } catch (err) {
      setMessage({ tone: 'bad', text: err.message })
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/agents" className="focus-ring text-xs underline" style={{ color: 'var(--text-muted)' }}>
            ← All agents
          </Link>
          <h1 className="mt-1 text-lg font-semibold">{draft.name}</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Version {agent.version} · {draft.workflow.nodes.length} steps
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={runSimulation} disabled={simulate.isPending} className="focus-ring rounded-md border px-3 py-1.5 text-sm disabled:opacity-50" style={{ borderColor: 'var(--border)' }}>
            {simulate.isPending ? 'Running…' : 'Test conversation'}
          </button>
          <button type="button" onClick={save} disabled={!dirty || update.isPending} className="focus-ring rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40" style={{ background: 'var(--series-1)' }}>
            {update.isPending ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
          </button>
        </div>
      </div>

      {message ? (
        <p className="text-sm" style={{ color: message.tone === 'bad' ? 'var(--status-critical)' : 'var(--status-good)' }}>
          {message.tone === 'bad' ? '■' : '●'} {message.text}
        </p>
      ) : null}

      {stats ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Calls" value={formatNumber(stats.total_calls)} />
          <StatCard label="Completed" value={formatNumber(stats.completed)} hint={`${formatNumber(stats.failed)} failed`} />
          <StatCard label="Resolved without a human" value={formatPercent(stats.resolution_rate)} accent="var(--status-good)" />
          <StatCard label="Avg call length" value={formatDuration(stats.avg_duration_seconds)} />
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Configuration" className="xl:col-span-1">
          <div className="flex flex-col gap-3">
            <Labeled label="Name">
              <input className="focus-ring w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Labeled>
            <Labeled label="Description">
              <input className="focus-ring w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.description || ''} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </Labeled>
            <Labeled label="System prompt — the agent's persona and tone">
              <textarea rows={4} className="focus-ring w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.system_prompt} onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })} />
            </Labeled>
            <div className="grid grid-cols-2 gap-3">
              <Labeled label="Voice">
                <input className="focus-ring w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.voice_id} onChange={(e) => setDraft({ ...draft, voice_id: e.target.value })} />
              </Labeled>
              <Labeled label="Language">
                <input className="focus-ring w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value })} />
              </Labeled>
              <Labeled label="Max turns">
                <input type="number" min={2} max={200} className="focus-ring tabular w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.max_turns} onChange={(e) => setDraft({ ...draft, max_turns: Number(e.target.value) })} />
              </Labeled>
              <Labeled label="Max seconds">
                <input type="number" min={30} max={7200} className="focus-ring tabular w-full rounded-md border px-2 py-1.5 text-sm" style={input} value={draft.max_call_seconds} onChange={(e) => setDraft({ ...draft, max_call_seconds: Number(e.target.value) })} />
              </Labeled>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="focus-ring" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} />
              Active — inactive agents reject new calls
            </label>
            <button type="button" onClick={destroy} disabled={remove.isPending} className="focus-ring mt-2 self-start rounded-md border px-3 py-1.5 text-sm" style={{ borderColor: 'var(--border)', color: 'var(--status-critical)' }}>
              Delete agent
            </button>
          </div>
        </Panel>

        <Panel title="Conversation workflow" description="Each step speaks, then decides where to go next" className="xl:col-span-2">
          <WorkflowEditor workflow={draft.workflow} onChange={(workflow) => setDraft({ ...draft, workflow })} />
        </Panel>
      </div>

      {simulation ? (
        <Panel
          title="Test conversation"
          description="Run against a simulated customer — no call is placed and nothing is charged"
          actions={<button type="button" onClick={() => setSimulation(null)} className="focus-ring rounded-md border px-2 py-1 text-xs" style={{ borderColor: 'var(--border)' }}>Dismiss</button>}
        >
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge value={simulation.outcome} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {simulation.ended_reason}
              </span>
            </div>
            <NodePath path={simulation.node_path} />
            {Object.keys(simulation.collected || {}).length ? (
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                Collected: {Object.entries(simulation.collected).map(([k, v]) => `${k}=${v}`).join(', ')}
              </p>
            ) : null}
            <TranscriptView turns={simulation.turns} />
          </div>
        </Panel>
      ) : null}
    </div>
  )
}

function Labeled({ label, children }) {
  return (
    <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      {label}
      {children}
    </label>
  )
}
