export default function Panel({ title, description, actions, children, className = '' }) {
  return (
    <section className={`card flex flex-col ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b px-4 py-3" style={{ borderColor: 'var(--border)' }}>
          <div>
            {title ? <h2 className="text-sm font-semibold">{title}</h2> : null}
            {description ? (
              <p className="mt-0.5 text-xs" style={{ color: 'var(--text-muted)' }}>
                {description}
              </p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      )}
      <div className="flex-1 p-4">{children}</div>
    </section>
  )
}
