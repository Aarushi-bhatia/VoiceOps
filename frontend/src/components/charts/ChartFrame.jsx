import { useEffect, useState } from 'react'
import { chartTheme } from '../../lib/palette'

/**
 * Re-reads the palette whenever the theme changes so charts restep for the
 * surface they are actually drawn on, rather than flipping light colours.
 */
export function useChartTheme() {
  const [theme, setTheme] = useState(() => chartTheme())

  useEffect(() => {
    const refresh = () => setTheme(chartTheme())
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', refresh)
    const observer = new MutationObserver(refresh)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      media.removeEventListener('change', refresh)
      observer.disconnect()
    }
  }, [])

  return theme
}

/** Shared tooltip shell: surface, hairline ring, text-token ink. */
export function TooltipCard({ title, rows }) {
  return (
    <div
      className="rounded-lg px-3 py-2 text-xs shadow-lg"
      style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
    >
      {title ? <div className="mb-1 font-medium">{title}</div> : null}
      <div className="flex flex-col gap-0.5">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5" style={{ color: 'var(--text-secondary)' }}>
              {row.color ? (
                <span aria-hidden="true" className="h-2 w-2 rounded-[2px]" style={{ background: row.color }} />
              ) : null}
              {row.label}
            </span>
            <span className="tabular font-medium">{row.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** A legend is always present for two or more series. */
export function Legend({ items }) {
  if (!items?.length) return null
  return (
    <ul className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-2 w-2 rounded-[2px]" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  )
}
