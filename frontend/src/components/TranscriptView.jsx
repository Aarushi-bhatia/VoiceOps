import { formatMs, titleCase } from '../lib/format'

/** The conversation as it happened, with per-stage latency on customer turns. */
export default function TranscriptView({ turns = [] }) {
  if (!turns.length) {
    return (
      <p className="py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
        No transcript — this call never reached a conversation.
      </p>
    )
  }

  return (
    <ol className="flex flex-col gap-2">
      {turns.map((turn, index) => {
        const isAgent = turn.role === 'agent'
        return (
          <li key={turn.index ?? index} className={`flex ${isAgent ? 'justify-start' : 'justify-end'}`}>
            <div
              className="max-w-[80%] rounded-xl px-3 py-2"
              style={{
                background: isAgent ? 'var(--surface-sunken)' : 'color-mix(in srgb, var(--series-1) 12%, var(--surface-1))',
                border: '1px solid var(--border)',
              }}
            >
              <div className="mb-0.5 flex items-center gap-2 text-[10px] uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                <span>{isAgent ? 'Agent' : 'Customer'}</span>
                {turn.node_id ? <span>· {turn.node_id}</span> : null}
              </div>
              <p className="text-sm" style={{ color: 'var(--text-primary)' }}>
                {turn.text || <em style={{ color: 'var(--text-muted)' }}>(silence)</em>}
              </p>
              {!isAgent && (turn.stt_ms || turn.llm_ms) ? (
                <div className="tabular mt-1 flex gap-3 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {turn.stt_ms ? <span>STT {formatMs(turn.stt_ms)}</span> : null}
                  {turn.llm_ms ? <span>LLM {formatMs(turn.llm_ms)}</span> : null}
                  {turn.confidence ? <span>conf {Number(turn.confidence).toFixed(2)}</span> : null}
                </div>
              ) : null}
              {isAgent && turn.tts_ms ? (
                <div className="tabular mt-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  TTS {formatMs(turn.tts_ms)}
                </div>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export function NodePath({ path = [] }) {
  if (!path.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs">
      {path.map((node, index) => (
        <span key={`${node}-${index}`} className="flex items-center gap-1">
          <code className="rounded px-1.5 py-0.5" style={{ background: 'var(--surface-sunken)', color: 'var(--text-secondary)' }}>
            {node}
          </code>
          {index < path.length - 1 ? <span style={{ color: 'var(--text-muted)' }}>→</span> : null}
        </span>
      ))}
    </div>
  )
}

export function EventTimeline({ events = [] }) {
  if (!events.length) return null
  return (
    <ol className="flex flex-col gap-1.5">
      {events.map((event, index) => (
        <li key={index} className="flex items-baseline gap-2 text-xs">
          <span className="tabular shrink-0" style={{ color: 'var(--text-muted)' }}>
            {new Date(event.created_at).toLocaleTimeString()}
          </span>
          <span className="font-medium">{titleCase(event.type)}</span>
          <span className="truncate" style={{ color: 'var(--text-muted)' }}>
            {summarise(event.payload)}
          </span>
        </li>
      ))}
    </ol>
  )
}

function summarise(payload = {}) {
  const parts = Object.entries(payload)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .slice(0, 4)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join('>') : value}`)
  return parts.join(' · ')
}
