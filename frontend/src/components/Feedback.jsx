export function Spinner({ label = 'Loading' }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm" style={{ color: 'var(--text-muted)' }} role="status">
      <span
        aria-hidden="true"
        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
      />
      {label}…
    </div>
  )
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="card p-4" role="alert">
      <p className="text-sm font-medium" style={{ color: 'var(--status-critical)' }}>
        ■ Something went wrong
      </p>
      <p className="mt-1 text-sm" style={{ color: 'var(--text-secondary)' }}>
        {error?.message || 'Unknown error'}
      </p>
      {onRetry ? (
        <button type="button" className="mt-3 rounded-md border px-3 py-1.5 text-sm focus-ring" style={{ borderColor: 'var(--border)' }} onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

export function EmptyState({ title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="max-w-sm text-sm" style={{ color: 'var(--text-muted)' }}>
          {description}
        </p>
      ) : null}
      {action}
    </div>
  )
}
