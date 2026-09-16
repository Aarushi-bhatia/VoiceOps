/**
 * A hero figure with its label. No plot, so no tooltip - the number is the
 * whole point, and any comparison is spelled out in `delta`.
 */
export default function StatCard({ label, value, hint, delta, deltaTone = 'neutral', accent }) {
  const deltaColor =
    deltaTone === 'good'
      ? 'var(--status-good)'
      : deltaTone === 'bad'
        ? 'var(--status-critical)'
        : 'var(--text-muted)'

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium tracking-wide uppercase" style={{ color: 'var(--text-muted)' }}>
          {label}
        </span>
        {accent ? <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: accent }} /> : null}
      </div>
      <div className="mt-2 text-3xl font-semibold leading-none" style={{ color: 'var(--text-primary)' }}>
        {value}
      </div>
      {(hint || delta) && (
        <div className="mt-2 flex items-baseline gap-2 text-xs">
          {delta ? <span style={{ color: deltaColor }}>{delta}</span> : null}
          {hint ? <span style={{ color: 'var(--text-muted)' }}>{hint}</span> : null}
        </div>
      )}
    </div>
  )
}
