/** Display helpers. All of these must tolerate null/undefined from the API. */

export const titleCase = (value) =>
  String(value ?? '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())

export function formatNumber(value, { digits = 0 } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercent(value, { digits = 1 } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(Number(value) * 100).toFixed(digits)}%`
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.round(Number(seconds))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, '0')}s`
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}

export function formatMs(value) {
  if (value === null || value === undefined) return '—'
  const ms = Number(value)
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

export function formatCents(cents) {
  if (cents === null || cents === undefined) return '—'
  return `$${(Number(cents) / 100).toFixed(2)}`
}

export function formatDateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function relativeTime(value) {
  if (!value) return '—'
  const delta = (new Date(value).getTime() - Date.now()) / 1000
  const units = [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
  ]
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, seconds] of units) {
    if (Math.abs(delta) >= seconds || unit === 'second') {
      return formatter.format(Math.round(delta / seconds), unit)
    }
  }
  return '—'
}
