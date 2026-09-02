import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import MapPage from './pages/MapPage.jsx'
import SearchPage from './pages/SearchPage.jsx'
import RoutePage from './pages/RoutePage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import ViolationsPage from './pages/ViolationsPage.jsx'
import TrafficPage from './pages/TrafficPage.jsx'
import AdminPage from './pages/AdminPage.jsx'

// The seven control-room screens from section 5.10. Each page is a shell:
// it calls the real endpoint and renders the shape that comes back. The
// visual design is deliberately absent -- wiring first, then looks.
const SCREENS = [
  { to: '/map', label: 'Map' },
  { to: '/search', label: 'Search' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/violations', label: 'Violations' },
  { to: '/traffic', label: 'Traffic' },
  { to: '/admin', label: 'Admin' }
]

export default function App() {
  return (
    <div className="app">
      <header>
        <span className="brand">Sentinel</span>
        <nav>
          {SCREENS.map((s) => (
            <NavLink key={s.to} to={s.to}>
              {s.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/map" replace />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/routes/:routeId" element={<RoutePage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/violations" element={<ViolationsPage />} />
          <Route path="/traffic" element={<TrafficPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Routes>
      </main>
    </div>
  )
}
