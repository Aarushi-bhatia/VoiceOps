import { useMemo, useState } from 'react'
import { FIELD_TYPES, NODE_TYPES, OUTCOMES } from '../lib/workflowTemplates'
import { titleCase } from '../lib/format'

const input = { borderColor: 'var(--border)', background: 'var(--surface-1)', color: 'var(--text-primary)' }

/**
 * Structured editor for the conversation graph.
 *
 * The backend is the authority on validity (dangling edges, unreachable nodes,
 * missing terminals are all rejected there). This editor surfaces the same
 * problems inline so they are caught before saving.
 */
export default function WorkflowEditor({ workflow, onChange }) {
  const [rawMode, setRawMode] = useState(false)
  const [rawText, setRawText] = useState(() => JSON.stringify(workflow, null, 2))
  const [rawError, setRawError] = useState(null)

  const nodeIds = useMemo(() => workflow.nodes.map((node) => node.id), [workflow])
  const problems = useMemo(() => validate(workflow), [workflow])

  const patchNode = (index, patch) => {
    const nodes = workflow.nodes.map((node, i) => (i === index ? { ...node, ...patch } : node))
    onChange({ ...workflow, nodes })
  }

  const changeType = (index, type) => {
    const node = workflow.nodes[index]
    const base = { id: node.id, type, label: node.label }
    const defaults = {
      say: { prompt: node.prompt || 'Say something.', next: nodeIds.find((id) => id !== node.id) || null },
      collect: {
        prompt: node.prompt || 'What is your order number?',
        field: 'order_id',
        field_type: 'order_id',
        max_attempts: 2,
        next: nodeIds.find((id) => id !== node.id) || null,
      },
      branch: {
        prompt: node.prompt || 'How can I help?',
        intents: [{ name: 'yes', description: 'agrees', examples: [], next: nodeIds.find((id) => id !== node.id) || node.id }],
        default: nodeIds.find((id) => id !== node.id) || node.id,
        max_attempts: 2,
      },
      transfer: { prompt: node.prompt || null, destination: 'support-queue', outcome: 'escalated' },
      hangup: { prompt: node.prompt || 'Thanks for calling.', outcome: 'resolved' },
    }
    const nodes = workflow.nodes.map((n, i) => (i === index ? { ...base, ...defaults[type] } : n))
    onChange({ ...workflow, nodes })
  }

  const addNode = () => {
    let suffix = workflow.nodes.length + 1
    while (nodeIds.includes(`node_${suffix}`)) suffix += 1
    const node = { id: `node_${suffix}`, type: 'say', label: 'New step', prompt: 'Say something.', next: null }
    onChange({ ...workflow, nodes: [...workflow.nodes, node] })
  }

  const removeNode = (index) => {
    const removed = workflow.nodes[index].id
    const nodes = workflow.nodes.filter((_, i) => i !== index).map((node) => clearEdgesTo(node, removed))
    onChange({
      ...workflow,
      start_node: workflow.start_node === removed ? nodes[0]?.id || '' : workflow.start_node,
      nodes,
    })
  }

  const applyRaw = () => {
    try {
      const parsed = JSON.parse(rawText)
      setRawError(null)
      onChange(parsed)
      setRawMode(false)
    } catch (err) {
      setRawError(err.message)
    }
  }

  if (rawMode) {
    return (
      <div className="flex flex-col gap-2">
        <textarea
          className="focus-ring h-[420px] w-full rounded-md border p-3 font-mono text-xs"
          style={input}
          value={rawText}
          onChange={(event) => setRawText(event.target.value)}
          spellCheck={false}
        />
        {rawError ? (
          <p className="text-sm" style={{ color: 'var(--status-critical)' }}>
            ■ {rawError}
          </p>
        ) : null}
        <div className="flex gap-2">
          <button type="button" onClick={applyRaw} className="focus-ring rounded-md px-3 py-1.5 text-sm text-white" style={{ background: 'var(--series-1)' }}>
            Apply JSON
          </button>
          <button type="button" onClick={() => setRawMode(false)} className="focus-ring rounded-md border px-3 py-1.5 text-sm" style={{ borderColor: 'var(--border)' }}>
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          Start at
          <select className="focus-ring rounded-md border px-2 py-1 text-sm" style={input} value={workflow.start_node} onChange={(event) => onChange({ ...workflow, start_node: event.target.value })}>
            {nodeIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={addNode} className="focus-ring rounded-md border px-2.5 py-1 text-sm" style={{ borderColor: 'var(--border)' }}>
          + Add step
        </button>
        <button
          type="button"
          onClick={() => {
            setRawText(JSON.stringify(workflow, null, 2))
            setRawMode(true)
          }}
          className="focus-ring ml-auto rounded-md border px-2.5 py-1 text-xs"
          style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
        >
          Edit as JSON
        </button>
      </div>

      {problems.length ? (
        <ul className="rounded-lg p-3 text-sm" style={{ background: 'var(--surface-sunken)' }}>
          {problems.map((problem) => (
            <li key={problem} style={{ color: 'var(--status-critical)' }}>
              ■ {problem}
            </li>
          ))}
        </ul>
      ) : null}

      <ol className="flex flex-col gap-3">
        {workflow.nodes.map((node, index) => (
          <li key={index} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="focus-ring w-40 rounded-md border px-2 py-1 font-mono text-xs"
                style={input}
                value={node.id}
                onChange={(event) => renameNode(workflow, onChange, index, event.target.value)}
                aria-label="Step id"
              />
              <select className="focus-ring rounded-md border px-2 py-1 text-sm" style={input} value={node.type} onChange={(event) => changeType(index, event.target.value)} aria-label="Step type">
                {NODE_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.label}
                  </option>
                ))}
              </select>
              <input
                className="focus-ring flex-1 rounded-md border px-2 py-1 text-sm"
                style={input}
                value={node.label || ''}
                placeholder="Label (optional)"
                onChange={(event) => patchNode(index, { label: event.target.value || null })}
                aria-label="Step label"
              />
              {workflow.start_node === node.id ? (
                <span className="rounded-full border px-2 py-0.5 text-[10px] uppercase" style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
                  start
                </span>
              ) : null}
              <button type="button" onClick={() => removeNode(index)} className="focus-ring rounded-md border px-2 py-1 text-xs" style={{ borderColor: 'var(--border)', color: 'var(--status-critical)' }} aria-label={`Remove ${node.id}`}>
                Remove
              </button>
            </div>

            <p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
              {NODE_TYPES.find((t) => t.value === node.type)?.help}
            </p>

            <div className="mt-2 flex flex-col gap-2">
              {node.type !== 'transfer' || node.prompt !== undefined ? (
                <Text label="Prompt" value={node.prompt || ''} onChange={(value) => patchNode(index, { prompt: value || null })} placeholder="What the agent says" />
              ) : null}

              {(node.type === 'collect' || node.type === 'branch') && (
                <Text label="Reprompt" value={node.reprompt || ''} onChange={(value) => patchNode(index, { reprompt: value || null })} placeholder="Said when the first answer isn't usable" />
              )}

              {node.type === 'collect' && (
                <div className="grid gap-2 sm:grid-cols-4">
                  <Small label="Field" value={node.field || ''} onChange={(value) => patchNode(index, { field: value })} />
                  <Select label="Type" value={node.field_type || 'text'} options={FIELD_TYPES} onChange={(value) => patchNode(index, { field_type: value })} />
                  <Select label="Attempts" value={String(node.max_attempts ?? 2)} options={['1', '2', '3', '4', '5']} onChange={(value) => patchNode(index, { max_attempts: Number(value) })} />
                  <Select label="Then go to" value={node.next || ''} options={['', ...nodeIds]} onChange={(value) => patchNode(index, { next: value || null })} />
                </div>
              )}

              {node.type === 'collect' && (
                <Select label="If it can't be captured" value={node.on_failure || ''} options={['', ...nodeIds]} onChange={(value) => patchNode(index, { on_failure: value || null })} />
              )}

              {node.type === 'say' && (
                <Select label="Then go to" value={node.next || ''} options={['', ...nodeIds]} onChange={(value) => patchNode(index, { next: value || null })} />
              )}

              {node.type === 'branch' && (
                <BranchIntents node={node} nodeIds={nodeIds} onPatch={(patch) => patchNode(index, patch)} />
              )}

              {node.type === 'transfer' && (
                <div className="grid gap-2 sm:grid-cols-2">
                  <Small label="Destination queue" value={node.destination || ''} onChange={(value) => patchNode(index, { destination: value })} />
                  <Select label="Outcome" value={node.outcome || 'escalated'} options={OUTCOMES} onChange={(value) => patchNode(index, { outcome: value })} />
                </div>
              )}

              {node.type === 'hangup' && (
                <Select label="Outcome" value={node.outcome || 'resolved'} options={OUTCOMES} onChange={(value) => patchNode(index, { outcome: value })} />
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

function BranchIntents({ node, nodeIds, onPatch }) {
  const intents = node.intents || []
  const patchIntent = (index, patch) => onPatch({ intents: intents.map((intent, i) => (i === index ? { ...intent, ...patch } : intent)) })

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
          Intents
        </span>
        <button
          type="button"
          className="focus-ring rounded-md border px-2 py-0.5 text-xs"
          style={{ borderColor: 'var(--border)' }}
          onClick={() => onPatch({ intents: [...intents, { name: `intent_${intents.length + 1}`, description: '', examples: [], next: nodeIds[0] }] })}
        >
          + Intent
        </button>
      </div>
      {intents.map((intent, index) => (
        <div key={index} className="grid gap-2 rounded-md p-2 sm:grid-cols-4" style={{ background: 'var(--surface-sunken)' }}>
          <Small label="Name" value={intent.name} onChange={(value) => patchIntent(index, { name: value })} />
          <div className="sm:col-span-2">
            <Small label="Description the model matches on" value={intent.description || ''} onChange={(value) => patchIntent(index, { description: value })} />
          </div>
          <Select label="Go to" value={intent.next} options={nodeIds} onChange={(value) => patchIntent(index, { next: value })} />
          <div className="sm:col-span-3">
            <Small
              label="Examples (comma separated)"
              value={(intent.examples || []).join(', ')}
              onChange={(value) => patchIntent(index, { examples: value.split(',').map((s) => s.trim()).filter(Boolean) })}
            />
          </div>
          <div className="flex items-end">
            <button type="button" onClick={() => onPatch({ intents: intents.filter((_, i) => i !== index) })} className="focus-ring rounded-md border px-2 py-1 text-xs" style={{ borderColor: 'var(--border)', color: 'var(--status-critical)' }}>
              Remove
            </button>
          </div>
        </div>
      ))}
      <Select label="If nothing matches" value={node.default || ''} options={nodeIds} onChange={(value) => onPatch({ default: value })} />
    </div>
  )
}

function Text({ label, value, onChange, placeholder }) {
  return (
    <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      {label}
      <textarea rows={2} className="focus-ring rounded-md border px-2 py-1.5 text-sm" style={input} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function Small({ label, value, onChange }) {
  return (
    <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      {label}
      <input className="focus-ring rounded-md border px-2 py-1 text-sm" style={input} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function Select({ label, value, options, onChange }) {
  return (
    <label className="flex flex-col gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      {label}
      <select className="focus-ring rounded-md border px-2 py-1 text-sm" style={input} value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option === '' ? '— none —' : titleCase(option)}
          </option>
        ))}
      </select>
    </label>
  )
}

function clearEdgesTo(node, removedId) {
  const next = { ...node }
  if (next.next === removedId) next.next = null
  if (next.on_failure === removedId) next.on_failure = null
  if (next.default === removedId) next.default = null
  if (next.intents) next.intents = next.intents.filter((intent) => intent.next !== removedId)
  return next
}

function renameNode(workflow, onChange, index, newId) {
  const oldId = workflow.nodes[index].id
  const nodes = workflow.nodes.map((node, i) => {
    const renamed = i === index ? { ...node, id: newId } : { ...node }
    if (renamed.next === oldId) renamed.next = newId
    if (renamed.on_failure === oldId) renamed.on_failure = newId
    if (renamed.default === oldId) renamed.default = newId
    if (renamed.intents) renamed.intents = renamed.intents.map((intent) => (intent.next === oldId ? { ...intent, next: newId } : intent))
    return renamed
  })
  onChange({ ...workflow, start_node: workflow.start_node === oldId ? newId : workflow.start_node, nodes })
}

/** Mirrors the server-side graph checks so mistakes surface before saving. */
function validate(workflow) {
  const problems = []
  const ids = workflow.nodes.map((node) => node.id)
  const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index)
  if (duplicates.length) problems.push(`Duplicate step ids: ${[...new Set(duplicates)].join(', ')}`)
  if (!ids.includes(workflow.start_node)) problems.push(`Start step "${workflow.start_node}" does not exist`)

  for (const node of workflow.nodes) {
    for (const target of edgesOf(node)) {
      if (!ids.includes(target)) problems.push(`${node.id} points at "${target}", which does not exist`)
    }
  }
  if (!workflow.nodes.some((node) => node.type === 'hangup' || node.type === 'transfer')) {
    problems.push('Add at least one hang up or transfer step so calls can end')
  }

  const reachable = new Set()
  const stack = [workflow.start_node]
  while (stack.length) {
    const current = stack.pop()
    if (reachable.has(current)) continue
    reachable.add(current)
    const node = workflow.nodes.find((n) => n.id === current)
    if (node) stack.push(...edgesOf(node))
  }
  const unreachable = ids.filter((id) => !reachable.has(id))
  if (unreachable.length) problems.push(`Unreachable steps: ${unreachable.join(', ')}`)

  return problems
}

function edgesOf(node) {
  return [node.next, node.on_failure, node.default, ...(node.intents || []).map((intent) => intent.next)].filter(Boolean)
}
