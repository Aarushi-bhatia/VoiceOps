import { NavLink, Outlet } from 'react-router-dom'
import { useHealth, useQueueStats } from '../hooks/useApi'
import { useTheme } from '../hooks/useTheme'
import { formatNumber } from '../lib/format'

const THEME_LABEL = { light: 'Light', dark: 'Dark', system: 'System' }

export default function Layout() {
  const { theme, cycle } = useTheme()
  const { data: health } = useHealth()
  const { data: queue } = useQueueStats()

  const nav = buildNav(queue)
  const degraded = health && health.status !== 'ok'
  const providers = health ? Object.entries(health.providers) : []
  const allMock = providers.length > 0 && providers.every(([, name]) => name === 'mock')

  return (
    <div className="min-h-full lg:grid lg:grid-cols-[232px_minmax(0,1fr)]">
      {/* Phones and tablets: a compact top bar with the same navigation. */}
      <header className="sticky top-0 z-20 border-b lg:hidden" style={{ borderColor: 'var(--border)', background: 'var(--surface-sunken)' }}>
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <Brand />
          <StatusDot degraded={degraded} />
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-2" aria-label="Main">
          {nav.map((item) => (
            <NavItem key={item.to} item={item} compact />
          ))}
        </nav>
      </header>

      <aside
        className="sticky top-0 hidden h-screen flex-col border-r lg:flex"
        style={{ borderColor: 'var(--border)', background: 'var(--surface-sunken)' }}
      >
        <div className="px-5 pt-5 pb-6">
          <Brand />
        </div>

        <nav className="flex flex-col gap-0.5 px-3" aria-label="Main">
          {nav.map((item) => (
            <NavItem key={item.to} item={item} />
          ))}
        </nav>

        <div className="mt-auto flex flex-col gap-3 border-t px-5 py-4 text-xs" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center justify-between gap-2">
            <StatusDot degraded={degraded} />
            {queue ? (
              <span className="mono" style={{ color: 'var(--text-muted)' }}>
                {formatNumber(queue.backlog)} waiting
              </span>
            ) : null}
          </div>

          <div className="flex flex-col gap-1" style={{ color: 'var(--text-muted)' }}>
            <span className="text-[11px] tracking-wide uppercase">Providers</span>
            {allMock ? (
              <span style={{ color: 'var(--text-secondary)' }}>All mocked, no live calls</span>
            ) : (
              <ul className="mono flex flex-col gap-0.5">
                {providers.map(([kind, name]) => (
                  <li key={kind} className="flex justify-between gap-2">
                    <span>{kind}</span>
                    <span style={{ color: name === 'mock' ? 'var(--text-muted)' : 'var(--text-secondary)' }}>{name}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={cycle}
              className="focus-ring rounded-md border px-2 py-1"
              style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
              aria-label={`Theme: ${THEME_LABEL[theme]}. Click to change.`}
            >
              Theme: {THEME_LABEL[theme]}
            </button>
            <a className="focus-ring underline" href="/docs" target="_blank" rel="noreferrer" style={{ color: 'var(--text-secondary)' }}>
              API docs
            </a>
          </div>
        </div>
      </aside>

      <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
        <div className="mx-auto w-full max-w-[1240px]">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

/** Badges surface live state on every page: work in flight, and anything a person must handle. */
function buildNav(queue) {
  const live = queue?.processing || 0
  const waiting = queue?.backlog || 0
  const dead = queue?.dead_letter || 0
  return [
    { to: '/', label: 'Dashboard', end: true, icon: IconGrid },
    { to: '/calls', label: 'Calls', icon: IconPhone, badge: live ? { value: live, tone: 'accent', title: `${live} calls in progress` } : null },
    { to: '/agents', label: 'Agents', icon: IconAgent },
    {
      to: '/queue',
      label: 'Queue',
      icon: IconQueue,
      badge: dead
        ? { value: dead, tone: 'critical', title: `${dead} calls in the dead-letter queue` }
        : waiting
          ? { value: waiting, tone: 'neutral', title: `${waiting} calls waiting` }
          : null,
    },
    { to: '/analytics', label: 'Analytics', icon: IconChart },
  ]
}

function NavItem({ item, compact = false }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={item.end}
      className={`focus-ring flex shrink-0 items-center gap-2.5 rounded-md text-sm transition-colors ${compact ? 'px-2.5 py-1.5' : 'px-2.5 py-2'}`}
      style={({ isActive }) => ({
        background: isActive ? 'var(--accent-soft)' : 'transparent',
        color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
        fontWeight: isActive ? 500 : 400,
      })}
    >
      {({ isActive }) => (
        <>
          <Icon style={{ color: isActive ? 'var(--series-1)' : 'var(--text-muted)' }} />
          <span>{item.label}</span>
          {item.badge ? <Badge {...item.badge} /> : null}
        </>
      )}
    </NavLink>
  )
}

function Badge({ value, tone, title }) {
  const styles = {
    accent: { background: 'var(--accent-soft)', color: 'var(--series-1)' },
    critical: { background: 'color-mix(in srgb, var(--status-critical) 18%, transparent)', color: 'var(--status-critical)' },
    neutral: { background: 'var(--surface-1)', color: 'var(--text-muted)' },
  }
  return (
    <span title={title} className="mono ml-auto rounded-full px-1.5 py-px text-[11px] leading-4" style={styles[tone]}>
      {formatNumber(value)}
    </span>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-lg" style={{ background: 'var(--series-1)', color: 'var(--on-accent)' }}>
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M3 7v4M6.5 4.5v9M10 6v6M13.5 3v12M17 7.5v3" />
        </svg>
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-sm font-semibold tracking-tight">VoiceOps</span>
        <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          CX operations
        </span>
      </span>
    </div>
  )
}

function StatusDot({ degraded }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: degraded ? 'var(--status-critical)' : 'var(--status-good)' }} />
      <span style={{ color: 'var(--text-secondary)' }}>{degraded ? 'Degraded' : 'Healthy'}</span>
    </span>
  )
}

function Svg({ children, style }) {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={style}>
      {children}
    </svg>
  )
}

const IconGrid = (props) => (
  <Svg {...props}>
    <rect x="2" y="2" width="5" height="5" rx="1" />
    <rect x="9" y="2" width="5" height="5" rx="1" />
    <rect x="2" y="9" width="5" height="5" rx="1" />
    <rect x="9" y="9" width="5" height="5" rx="1" />
  </Svg>
)

const IconPhone = (props) => (
  <Svg {...props}>
    <path d="M5.5 2.5 4 2a1 1 0 0 0-1.1.4C2.2 3.4 1.8 5 3.4 7.9a12 12 0 0 0 4.7 4.7c2.9 1.6 4.5 1.2 5.5.5a1 1 0 0 0 .4-1.1l-.5-1.5a1 1 0 0 0-1.2-.6l-1.6.5a7 7 0 0 1-3.3-3.3l.5-1.6a1 1 0 0 0-.6-1.2Z" />
  </Svg>
)

const IconAgent = (props) => (
  <Svg {...props}>
    <rect x="3" y="5" width="10" height="8" rx="2" />
    <path d="M8 2.5V5M6 9h.01M10 9h.01M1.5 8.5v2M14.5 8.5v2" />
  </Svg>
)

const IconQueue = (props) => (
  <Svg {...props}>
    <path d="M2.5 4h11M2.5 8h11M2.5 12h7" />
  </Svg>
)

const IconChart = (props) => (
  <Svg {...props}>
    <path d="M2.5 13.5h11M4.5 11V7.5M8 11V4.5M11.5 11V8.5" />
  </Svg>
)
