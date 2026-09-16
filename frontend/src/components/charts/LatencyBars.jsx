import { formatMs } from '../../lib/format'
import { useChartTheme } from './ChartFrame'

/**
 * Four ordered values (p50 -> p99). Too few points for a plot with axes, so
 * these are direct-labelled ordinal bars on a single-hue ramp - magnitude by
 * length, position by percentile.
 */
export default function LatencyBars({ latency }) {
  const theme = useChartTheme()
  const points = [
    { label: 'p50', value: latency?.p50_ms ?? 0 },
    { label: 'p90', value: latency?.p90_ms ?? 0 },
    { label: 'p95', value: latency?.p95_ms ?? 0 },
    { label: 'p99', value: latency?.p99_ms ?? 0 },
  ]
  const max = Math.max(...points.map((p) => p.value), 1)

  return (
    <div>
      <ul className="flex flex-col gap-2.5">
        {points.map((point, index) => (
          <li key={point.label} className="flex items-center gap-3">
            <span className="tabular w-8 text-xs" style={{ color: 'var(--text-muted)' }}>
              {point.label}
            </span>
            <span className="h-2.5 flex-1 overflow-hidden rounded-full" style={{ background: 'var(--surface-sunken)' }}>
              <span
                className="block h-full rounded-full"
                style={{ width: `${Math.max((point.value / max) * 100, 2)}%`, background: theme.ordinal[index] }}
              />
            </span>
            <span className="tabular w-16 text-right text-xs font-medium">{formatMs(point.value)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs" style={{ color: 'var(--text-muted)' }}>
        Time from the caller finishing a sentence to the agent starting its reply, across{' '}
        <span className="tabular">{latency?.samples ?? 0}</span> turns.
      </p>
    </div>
  )
}
