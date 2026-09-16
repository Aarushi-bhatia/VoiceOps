import { NavLink, Outlet } from 'react-router-dom'
import { useHealth, useQueueStats } from '../hooks/useApi'
import { useTheme } from '../hooks/useTheme'
import { formatNumber } from '../lib/format'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/calls', label: 'Calls' },
  { to: '/agents', label: 'Agents' },
  { to: '/queue', label: 'Queue' },
  { to: '/analytics', label: 'Analytics' },
]

const THEME_LABEL = { light: 'Light', dark: 'Dark', system: 'System' }

export default function Layout() {
  const { theme, cycle } = useTheme()
  const { data: health } = useHealth()
  const { data: queue } = useQueueStats()

  const degraded = health && health.status !== 'ok'

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 border-b backdrop-blur" style={{ borderColor: 'var(--border)', background: 'color-mix(in srgb, var(--surface-page) 88%, transparent)' }}>
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="grid h-7 w-7 place-items-center rounded-lg text-sm font-bold text-white" style={{ background: 'var(--series-1)' }}>
              V
            </span>
            <span className="text-sm font-semibold tracking-tight">VoiceOps</span>
            <span className="rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide" style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
              CX Operations
            </span>
          </div>

          <nav className="flex items-center gap-1" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `focus-ring rounded-md px-3 py-1.5 text-sm transition-colors ${isActive ? 'font-medium' : ''}`
                }
                style={({ isActive }) => ({
                  background: isActive ? 'var(--surface-1)' : 'transparent',
                  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                  border: `1px solid ${isActive ? 'var(--border)' : 'transparent'}`,
                })}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3 text-xs">
            {queue ? (
              <span className="tabular hidden sm:inline" style={{ color: 'var(--text-muted)' }}>
                {formatNumber(queue.backlog)} waiting · {formatNumber(queue.processing)} live
              </span>
            ) : null}
            <span className="inline-flex items-center gap-1.5" title={health ? `db ${health.database} · redis ${health.redis}` : 'checking'}>
              <span aria-hidden="true" style={{ color: degraded ? 'var(--status-critical)' : 'var(--status-good)' }}>
                {degraded ? '■' : '●'}
              </span>
              <span style={{ color: 'var(--text-secondary)' }}>{degraded ? 'Degraded' : 'Healthy'}</span>
            </span>
            <button
              type="button"
              onClick={cycle}
              className="focus-ring rounded-md border px-2 py-1"
              style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
              aria-label={`Theme: ${THEME_LABEL[theme]}. Click to change.`}
            >
              {THEME_LABEL[theme]}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6">
        <Outlet />
      </main>

      <footer className="border-t px-4 py-3 text-xs" style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-2">
          <span>
            Providers: {health ? Object.entries(health.providers).map(([k, v]) => `${k}=${v}`).join(' · ') : '—'}
          </span>
          <a className="focus-ring underline" href="/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
        </div>
      </footer>
    </div>
  )
}
