import { useEffect, useState } from 'react'
import { useCallMutations } from '../hooks/useApi'

const PRIORITIES = ['urgent', 'high', 'normal', 'low']

/**
 * Queue one call or a pasted batch. Numbers are split on newlines and commas so
 * a column pasted straight out of a spreadsheet works.
 */
export default function NewCallDialog({ agents, onClose }) {
  const [agentId, setAgentId] = useState(agents[0]?.id || '')
  const [numbers, setNumbers] = useState('')
  const [priority, setPriority] = useState('normal')
  const [scheduledAt, setScheduledAt] = useState('')
  const [customerName, setCustomerName] = useState('')
  const [error, setError] = useState(null)
  const { create, createBulk } = useCallMutations()

  useEffect(() => {
    const onKey = (event) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const parsed = numbers
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean)

  const submit = async (event) => {
    event.preventDefault()
    setError(null)
    if (!agentId) return setError('Choose an agent first.')
    if (parsed.length === 0) return setError('Add at least one phone number.')

    const metadata = customerName ? { customer_name: customerName } : {}
    const scheduled = scheduledAt ? new Date(scheduledAt).toISOString() : null

    try {
      if (parsed.length === 1) {
        await create.mutateAsync({
          agent_id: agentId,
          to_number: parsed[0],
          priority,
          scheduled_at: scheduled,
          metadata,
        })
      } else {
        await createBulk.mutateAsync({
          agent_id: agentId,
          priority,
          scheduled_at: scheduled,
          recipients: parsed.map((to_number) => ({ to_number, metadata })),
        })
      }
      onClose()
    } catch (err) {
      setError(err.message)
    }
  }

  const pending = create.isPending || createBulk.isPending

  return (
    <div className="fixed inset-0 z-40 grid place-items-center p-4">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <form
        onSubmit={submit}
        className="card relative z-10 flex w-full max-w-lg flex-col gap-3 p-5"
        role="dialog"
        aria-modal="true"
        aria-label="Queue a call"
      >
        <h2 className="text-base font-semibold">Queue a call</h2>

        <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
          Agent
          <select className="focus-ring rounded-md border px-2 py-1.5 text-sm" style={inputStyle} value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id} disabled={!agent.is_active}>
                {agent.name}
                {agent.is_active ? '' : ' (inactive)'}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
          Phone numbers — one per line for a batch
          <textarea
            rows={4}
            className="focus-ring tabular rounded-md border px-2 py-1.5 text-sm"
            style={inputStyle}
            value={numbers}
            onChange={(e) => setNumbers(e.target.value)}
            placeholder={'+15551110001111\n+15551110002222'}
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
            Priority
            <select className="focus-ring rounded-md border px-2 py-1.5 text-sm" style={inputStyle} value={priority} onChange={(e) => setPriority(e.target.value)}>
              {PRIORITIES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
            Schedule for (optional)
            <input type="datetime-local" className="focus-ring rounded-md border px-2 py-1.5 text-sm" style={inputStyle} value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} />
          </label>
        </div>

        <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
          Customer name (available to prompts as {'{customer_name}'})
          <input className="focus-ring rounded-md border px-2 py-1.5 text-sm" style={inputStyle} value={customerName} onChange={(e) => setCustomerName(e.target.value)} placeholder="Dana" />
        </label>

        {error ? (
          <p className="text-sm" style={{ color: 'var(--status-critical)' }}>
            ■ {error}
          </p>
        ) : null}

        <div className="mt-1 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="focus-ring rounded-md border px-3 py-1.5 text-sm" style={{ borderColor: 'var(--border)' }}>
            Cancel
          </button>
          <button type="submit" disabled={pending} className="focus-ring rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" style={{ background: 'var(--series-1)' }}>
            {pending ? 'Queueing…' : parsed.length > 1 ? `Queue ${parsed.length} calls` : 'Queue call'}
          </button>
        </div>
      </form>
    </div>
  )
}

const inputStyle = { borderColor: 'var(--border)', background: 'var(--surface-1)', color: 'var(--text-primary)' }
