/**
 * Chart colour roles, read from the CSS custom properties in index.css so the
 * charts follow the active theme without re-rendering logic.
 *
 * Series colours are assigned in fixed slot order and never cycled: a chart
 * that would need a ninth series folds the tail into "Other" instead.
 */
const read = (name, fallback) => {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name)
  return value.trim() || fallback
}

export function chartTheme() {
  return {
    series: [read('--series-1', '#2a78d6'), read('--series-2', '#eb6834'), read('--series-3', '#1baf7a'), read('--series-4', '#eda100')],
    ordinal: [read('--ordinal-1', '#86b6ef'), read('--ordinal-2', '#5598e7'), read('--ordinal-3', '#2a78d6'), read('--ordinal-4', '#1c5cab')],
    grid: read('--gridline', '#e1e0d9'),
    axis: read('--baseline', '#c3c2b7'),
    muted: read('--text-muted', '#898781'),
    text: read('--text-primary', '#0b0b0b'),
    surface: read('--surface-1', '#fcfcfb'),
    border: read('--border', 'rgba(11,11,11,0.1)'),
  }
}

/** Status colours are reserved for state and always paired with a label. */
export const STATUS_TONE = {
  completed: 'good',
  resolved: 'good',
  in_progress: 'info',
  dialing: 'info',
  queued: 'neutral',
  scheduled: 'neutral',
  retrying: 'warning',
  escalated: 'warning',
  failed: 'critical',
  canceled: 'neutral',
  no_answer: 'serious',
  voicemail: 'neutral',
  customer_hung_up: 'serious',
  unresolved: 'warning',
  callback_scheduled: 'info',
}
