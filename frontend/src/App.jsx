import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import MapPage from './pages/MapPage.jsx'
import SearchPage from './pages/SearchPage.jsx'
import RoutePage from './pages/RoutePage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import ViolationsPage from './pages/ViolationsPage.jsx'
import TrafficPage from './pages/TrafficPage.jsx'
import AdminPage from './pages/AdminPage.jsx'
import SightingsPage from './pages/SightingsPage.jsx'
import WatchlistPage from './pages/WatchlistPage.jsx'
import Icon from './components/Icon.jsx'

const NAVIGATION = [
  {
    label: 'Operations',
    items: [
      { to: '/map', label: 'Command map', icon: 'map' },
      { to: '/sightings', label: 'Live sightings', icon: 'scan' },
      { to: '/alerts', label: 'Alert queue', icon: 'alert' },
      { to: '/watchlist', label: 'Watchlist', icon: 'target' },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { to: '/search', label: 'Vehicle search', icon: 'search' },
      { to: '/violations', label: 'Violations', icon: 'shield' },
      { to: '/traffic', label: 'Traffic signals', icon: 'pulse' },
    ],
  },
  {
    label: 'System',
    items: [{ to: '/admin', label: 'Network control', icon: 'settings' }],
  },
]

function SystemClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const interval = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(interval)
  }, [])
  return (
    <time dateTime={now.toISOString()}>
      {now.toLocaleTimeString('en-GB', {
        timeZone: 'Asia/Kolkata',
        hour12: false,
      })}
    </time>
  )
}

export default function App() {
  const location = useLocation()
  const [navOpen, setNavOpen] = useState(false)
  const title = location.pathname.startsWith('/routes/')
    ? 'Route analysis'
    : NAVIGATION.flatMap((group) => group.items).find((item) => item.to === location.pathname)
        ?.label || 'Command center'
  useEffect(() => setNavOpen(false), [location.pathname])
  useEffect(() => {
    if (!navOpen) return
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setNavOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [navOpen])

  return (
    <div className="app">
      <a className="skip-link" href="#workspace">
        Skip to workspace
      </a>
      <button
        className="nav-toggle"
        type="button"
        onClick={() => setNavOpen((open) => !open)}
        aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
        aria-expanded={navOpen}
        aria-controls="primary-navigation"
      >
        <Icon name={navOpen ? 'close' : 'menu'} size={21} />
      </button>
      <aside
        id="primary-navigation"
        className={`sidebar${navOpen ? ' is-open' : ''}`}
        aria-label="Primary navigation"
      >
        <NavLink to="/map" className="brand-lockup" aria-label="Sentinel command map">
          <div className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
          <div>
            <div className="brand">SENTINEL</div>
            <div className="brand-subtitle">PUBLIC SAFETY GRID</div>
          </div>
        </NavLink>
        <nav className="primary-nav">
          {NAVIGATION.map((group, index) => (
            <div
              className="nav-group"
              style={{ '--nav-delay': `${index * 55}ms` }}
              key={group.label}
            >
              <div className="nav-label">{group.label}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                >
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="signal-widget">
          <div className="signal-radar" aria-hidden="true">
            <i />
            <i />
            <i />
            <b />
          </div>
          <div>
            <span>CONTROL REGION</span>
            <strong>GUJARAT / IN</strong>
          </div>
        </div>
        <div className="sidebar-footer">
          <div className="network-state">
            <Icon name="shield" size={12} /> CITY INTELLIGENCE
          </div>
          <div className="operator-card">
            <div className="operator-avatar">OP</div>
            <div>
              <strong>Operator workspace</strong>
              <span>OBSERVE / ANALYZE / RESPOND</span>
            </div>
          </div>
        </div>
      </aside>
      {navOpen && (
        <button
          className="nav-scrim"
          type="button"
          aria-label="Dismiss navigation"
          onClick={() => setNavOpen(false)}
        />
      )}
      <main className="main-content" id="workspace" tabIndex={-1}>
        <header className="topbar">
          <div className="crumbs">
            <span>Sentinel</span>
            <b>/</b>
            <strong>{title}</strong>
          </div>
          <div className="topbar-context">
            <NavLink to="/search" className="system-chip">
              <Icon name="search" size={13} /> QUICK SEARCH
            </NavLink>
            <span className="clock">
              <SystemClock /> <small>IST</small>
            </span>
          </div>
        </header>
        <div className="page-content" key={location.pathname}>
          <Routes>
            <Route path="/" element={<Navigate to="/map" replace />} />
            <Route path="/map" element={<MapPage />} />
            <Route path="/sightings" element={<SightingsPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/routes/:routeId" element={<RoutePage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/watchlist" element={<WatchlistPage />} />
            <Route path="/violations" element={<ViolationsPage />} />
            <Route path="/traffic" element={<TrafficPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="*" element={<Navigate to="/map" replace />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
