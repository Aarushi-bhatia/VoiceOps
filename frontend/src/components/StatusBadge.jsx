import { titleCase } from '../lib/format'
import { STATUS_TONE } from '../lib/palette'

// Status is never carried by colour alone: every badge shows a glyph and a word.
const TONES = {
  good: { color: 'var(--status-good)', glyph: '●' },
  warning: { color: 'var(--status-warning)', glyph: '▲' },
  serious: { color: 'var(--status-serious)', glyph: '▲' },
  critical: { color: 'var(--status-critical)', glyph: '■' },
  info: { color: 'var(--series-1)', glyph: '◆' },
  neutral: { color: 'var(--status-neutral)', glyph: '○' },
}

export default function StatusBadge({ value, tone, size = 'sm' }) {
  if (!value) return <span className="text-[var(--text-muted)]">—</span>
  const resolved = TONES[tone || STATUS_TONE[value] || 'neutral']
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-medium ${
        size === 'sm' ? 'text-xs' : 'text-sm'
      }`}
      style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
    >
      <span aria-hidden="true" style={{ color: resolved.color, fontSize: '0.7em' }}>
        {resolved.glyph}
      </span>
      {titleCase(value)}
    </span>
  )
}
