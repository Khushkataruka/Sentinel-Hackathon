import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import MapPage from './pages/MapPage.jsx'
import SearchPage from './pages/SearchPage.jsx'
import RoutePage from './pages/RoutePage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import ViolationsPage from './pages/ViolationsPage.jsx'
import TrafficPage from './pages/TrafficPage.jsx'
import AdminPage from './pages/AdminPage.jsx'
import SightingsPage from './pages/SightingsPage.jsx'

const SCREENS = [
  { to: '/map', label: 'Map Overview' },
  { to: '/sightings', label: 'Live Sightings' },
  { to: '/search', label: 'Search' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/violations', label: 'Violations' },
  { to: '/traffic', label: 'Traffic' },
  { to: '/admin', label: 'Admin' }
]

export default function App() {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">Sentinel</div>
        <nav>
          {SCREENS.map((s) => (
            <NavLink key={s.to} to={s.to} className={({isActive}) => isActive ? "active" : ""}>
              {s.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/map" replace />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/sightings" element={<SightingsPage />} />
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
