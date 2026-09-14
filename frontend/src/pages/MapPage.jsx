import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { api } from '../api.js'
import VideoPlayer from '../components/VideoPlayer.jsx'
import OnboardCameraModal from '../components/OnboardCameraModal.jsx'
import Icon from '../components/Icon.jsx'
import './map-dashboard.css'

const CENTRE = [23.0225, 72.5714]

function pointFor(camera) {
  if (camera.lat == null || camera.lon == null || camera.lat === '' || camera.lon === '')
    return null
  const lat = Number(camera.lat)
  const lon = Number(camera.lon)
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
    ? [lat, lon]
    : null
}

function healthFor(camera) {
  if (!camera.enabled) return { key: 'offline', label: 'Disabled' }
  if (!camera.surveyed) return { key: 'survey', label: 'Needs survey' }
  return (
    {
      ok: { key: 'ok', label: 'Healthy' },
      degraded: { key: 'degraded', label: 'Degraded' },
      silent: { key: 'silent', label: 'Silent' },
      down: { key: 'down', label: 'Offline' },
    }[camera.verdict] || { key: 'unknown', label: 'Health pending' }
  )
}

function MapControls({ points, focus, reset }) {
  const map = useMap()
  useEffect(() => {
    if (points.length) map.fitBounds(points, { padding: [45, 45], maxZoom: 12, animate: false })
    else map.setView(CENTRE, 12)
  }, [map, points, reset])
  useEffect(() => {
    if (focus)
      map.flyTo(focus, 15, {
        duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 0.7,
      })
  }, [map, focus])
  return (
    <div className="map-zoom-controls">
      <button type="button" aria-label="Zoom in" onClick={() => map.zoomIn()}>
        <Icon name="plus" />
      </button>
      <button type="button" aria-label="Zoom out" onClick={() => map.zoomOut()}>
        <Icon name="minus" />
      </button>
    </div>
  )
}

function NetworkMetrics({ cameras, loading }) {
  const surveyed = cameras.filter((camera) => camera.surveyed).length
  const known = cameras.filter((camera) => camera.detections_1h != null)
  const signals = known.reduce((sum, camera) => sum + Number(camera.detections_1h), 0)
  const metrics = [
    ['Network endpoints', cameras.length, 'registered cameras', 'camera'],
    [
      'Survey coverage',
      cameras.length ? `${Math.round((surveyed / cameras.length) * 100)}%` : '—',
      `${surveyed} cameras profiled`,
      'target',
    ],
    [
      'Healthy endpoints',
      cameras.filter((camera) => healthFor(camera).key === 'ok').length,
      `${cameras.filter((camera) => healthFor(camera).key === 'unknown').length} awaiting health telemetry`,
      'pulse',
    ],
    [
      'Detections / hour',
      known.length ? signals.toLocaleString() : '—',
      known.length ? `reported by ${known.length} cameras` : 'awaiting telemetry',
      'scan',
    ],
  ]
  return (
    <section className="sentinel-metrics" aria-label="Network metrics">
      {metrics.map(([label, value, note, icon]) => (
        <div key={label}>
          <span>
            {label}
            <Icon name={icon} size={15} />
          </span>
          <strong>{loading && !cameras.length ? '—' : value}</strong>
          <small>{note}</small>
        </div>
      ))}
    </section>
  )
}

function CameraDetail({ camera }) {
  const health = healthFor(camera)
  return (
    <section className="sentinel-camera-detail">
      <div className="sentinel-detail-head">
        <div>
          <span>Selected endpoint</span>
          <h2>{camera.name || camera.camera_id}</h2>
        </div>
        <div className={`sentinel-health-pill ${health.key}`}>
          <i />
          {health.label}
        </div>
      </div>
      <VideoPlayer cameraId={camera.camera_id} title={camera.camera_id} />
      <dl>
        <div>
          <dt>Department</dt>
          <dd>{camera.department_code || 'Unassigned'}</dd>
        </div>
        <div>
          <dt>Camera type</dt>
          <dd>{camera.kind?.toUpperCase() || 'Unspecified'}</dd>
        </div>
        <div>
          <dt>Detections / hour</dt>
          <dd>{camera.detections_1h ?? 'Not reported'}</dd>
        </div>
        <div>
          <dt>Survey trust</dt>
          <dd>
            {camera.surveyed && camera.trust_level != null
              ? `${Math.round(Number(camera.trust_level) * 100)}%`
              : 'Not surveyed'}
          </dd>
        </div>
      </dl>
      <div className="camera-capabilities">
        <span className={camera.plate_viable ? 'enabled' : ''}>Plate processing</span>
        <span className={camera.density_viable ? 'enabled' : ''}>Traffic density</span>
      </div>
    </section>
  )
}

export default function MapPage() {
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [focus, setFocus] = useState(null)
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [showOnboard, setShowOnboard] = useState(false)
  const [minimal, setMinimal] = useState(false)
  const [recenter, setRecenter] = useState(0)
  const requestId = useRef(0)
  const fetchCameras = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    try {
      const rows = await api.cameras()
      if (id !== requestId.current) return
      setCameras(rows)
      setSelectedId((previous) =>
        rows.some((camera) => camera.camera_id === previous)
          ? previous
          : (rows[0]?.camera_id ?? null),
      )
      setError(null)
    } catch (err) {
      if (id === requestId.current) setError(err.message)
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [])
  useEffect(() => {
    fetchCameras()
    return () => {
      requestId.current++
    }
  }, [fetchCameras])
  const points = useMemo(() => cameras.map(pointFor).filter(Boolean), [cameras])
  const filtered = useMemo(
    () =>
      cameras.filter((camera) => {
        const health = healthFor(camera).key
        const matchesFilter =
          filter === 'all' ||
          (filter === 'healthy' && health === 'ok') ||
          (filter === 'attention' && !['ok', 'unknown'].includes(health))
        return (
          matchesFilter &&
          `${camera.name} ${camera.camera_id} ${camera.department_code}`
            .toLowerCase()
            .includes(query.trim().toLowerCase())
        )
      }),
    [cameras, filter, query],
  )
  const selected = cameras.find((camera) => camera.camera_id === selectedId)
  const choose = (camera) => {
    setSelectedId(camera.camera_id)
    setFocus(pointFor(camera))
  }
  const surveyed = cameras.filter((camera) => camera.surveyed).length
  const attention = cameras.filter(
    (camera) => !['ok', 'unknown'].includes(healthFor(camera).key),
  ).length

  return (
    <div className="sentinel-map-page">
      <header className="sentinel-command-header">
        <div>
          <div className="sentinel-eyebrow">
            <i />
            {loading
              ? 'Synchronizing network'
              : error
                ? 'Connection interrupted'
                : 'Network connected'}
            <span className="map-region-label">GUJARAT / INDIA</span>
          </div>
          <h1>
            City intelligence<span>.</span>
          </h1>
          <p>A wider view. A faster response. Your city, in focus.</p>
        </div>
        <div className="sentinel-header-actions">
          <button
            className="sentinel-icon-button"
            type="button"
            aria-label="Refresh camera network"
            disabled={loading}
            onClick={fetchCameras}
          >
            <Icon name="refresh" />
          </button>
          <button className="sentinel-onboard" type="button" onClick={() => setShowOnboard(true)}>
            <Icon name="plus" />
            Onboard camera
          </button>
        </div>
      </header>
      {error && (
        <div className="sentinel-demo-notice" role="status">
          <Icon name="alert" />
          <span>
            {error} {cameras.length > 0 && 'Showing the last loaded network.'}
          </span>
          <button onClick={fetchCameras} disabled={loading}>
            Retry connection
          </button>
        </div>
      )}
      <NetworkMetrics cameras={cameras} loading={loading} />
      <section className="sentinel-operations">
        <div className="sentinel-map-card">
          <div className="sentinel-map-toolbar">
            <div className="sentinel-filter-set" aria-label="Camera filters">
              {[
                ['all', 'All endpoints'],
                ['healthy', 'Healthy'],
                ['attention', 'Attention'],
              ].map(([key, label]) => (
                <button
                  type="button"
                  aria-pressed={filter === key}
                  className={filter === key ? 'is-active' : ''}
                  key={key}
                  onClick={() => setFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="sentinel-map-tools">
              <button
                type="button"
                aria-pressed={minimal}
                onClick={() => setMinimal((value) => !value)}
              >
                <Icon name="layers" />
                {minimal ? 'High contrast' : 'Contrast'}
              </button>
            </div>
          </div>
          <div className="sentinel-map-canvas">
            <MapContainer
              center={CENTRE}
              zoom={12}
              minZoom={3}
              className={`sentinel-leaflet-map ${minimal ? 'map-high-contrast' : ''}`}
              zoomControl={false}
            >
              <TileLayer
                url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
              />
              <MapControls points={points} focus={focus} reset={recenter} />
              {filtered.filter(pointFor).map((camera) => {
                const health = healthFor(camera)
                return (
                  <CircleMarker
                    key={camera.camera_id}
                    center={pointFor(camera)}
                    radius={camera.camera_id === selectedId ? 9 : 6}
                    pathOptions={{
                      className: `sentinel-beacon-path sentinel-beacon-${health.key} ${camera.camera_id === selectedId ? 'is-selected' : ''}`,
                      fillOpacity: 0.9,
                    }}
                    eventHandlers={{ click: () => choose(camera) }}
                  >
                    <Tooltip direction="top" offset={[0, -9]} opacity={1}>
                      <strong>{camera.name || camera.camera_id}</strong>
                      <span>
                        {health.label} · {camera.camera_id}
                      </span>
                    </Tooltip>
                  </CircleMarker>
                )
              })}
            </MapContainer>
            <div className="sentinel-map-grid" />
            <div className="map-sector-label">
              <span>OPERATIONAL GRID</span>
              <strong>Gujarat</strong>
              <small>REGIONAL CAMERA NETWORK</small>
            </div>
            <div className="sentinel-map-caption">
              <Icon name="target" />
              {filtered.filter(pointFor).length} endpoints in view
            </div>
            {!loading && !cameras.length && (
              <div className="sentinel-empty-map">
                <Icon name="camera" size={26} />
                <strong>{error ? 'Waiting for the network' : 'Your network starts here'}</strong>
                <span>
                  {error
                    ? 'Reconnect to load the camera grid.'
                    : 'Onboard a camera to add it to the operational map.'}
                </span>
                <button onClick={error ? fetchCameras : () => setShowOnboard(true)}>
                  {error ? 'Reconnect' : 'Onboard camera'}
                </button>
              </div>
            )}
          </div>
          <footer className="sentinel-map-footer">
            <span>
              <i className="ok" />
              Healthy
            </span>
            <span>
              <i className="degraded" />
              Attention
            </span>
            <span>
              <i className="unknown" />
              Health pending
            </span>
            <button
              type="button"
              onClick={() => {
                setFocus(null)
                setRecenter((value) => value + 1)
              }}
            >
              <Icon name="target" />
              Recenter
            </button>
          </footer>
        </div>
        <aside className="sentinel-network-rail">
          <div className="sentinel-rail-head">
            <div>
              <span>Camera network</span>
              <strong>
                {filtered.length.toString().padStart(2, '0')} <small>endpoints</small>
              </strong>
            </div>
            <label className="sentinel-search">
              <Icon name="search" />
              <input
                aria-label="Search camera or ID"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search camera or ID"
              />
            </label>
          </div>
          <div className="sentinel-camera-list">
            {filtered.map((camera) => {
              const health = healthFor(camera)
              return (
                <button
                  type="button"
                  key={camera.camera_id}
                  aria-pressed={selectedId === camera.camera_id}
                  className={`sentinel-camera-row ${selectedId === camera.camera_id ? 'is-selected' : ''}`}
                  onClick={() => choose(camera)}
                >
                  <i className={health.key} />
                  <span>
                    <strong>{camera.name || camera.camera_id}</strong>
                    <small>
                      {camera.camera_id} · {camera.department_code || 'Unassigned'}
                    </small>
                  </span>
                  <Icon name="arrow" size={12} />
                </button>
              )
            })}
            {!filtered.length && (
              <div className="sentinel-list-empty">
                {loading ? 'Loading cameras…' : 'No cameras match this view.'}
              </div>
            )}
          </div>
          {selected && <CameraDetail camera={selected} />}
        </aside>
      </section>
      <section className="sentinel-signal-summary">
        <div>
          <span>01 / Spatial coverage</span>
          <strong>
            {points.length}
            <small> mapped endpoints</small>
          </strong>
          <div className="network-meter">
            <i
              style={{
                width: `${cameras.length ? (points.length / cameras.length) * 100 : 0}%`,
              }}
            />
          </div>
          <p>Camera positions across the operational grid.</p>
        </div>
        <div>
          <span>02 / Capability coverage</span>
          <strong>
            {surveyed}
            <small> surveyed cameras</small>
          </strong>
          <div className="network-meter">
            <i
              style={{
                width: `${cameras.length ? (surveyed / cameras.length) * 100 : 0}%`,
              }}
            />
          </div>
          <p>Profiles define available detection capabilities.</p>
        </div>
        <div>
          <span>03 / Operator attention</span>
          <strong>{attention ? `${attention} endpoints` : 'No reported issues'}</strong>
          <p>
            {cameras.some((camera) => !camera.verdict)
              ? 'Health checks are pending for part of the network.'
              : 'Review disabled, degraded, silent, or unsurveyed cameras.'}
          </p>
          <a href="/admin">
            Open network control <Icon name="arrow" size={12} />
          </a>
        </div>
      </section>
      <OnboardCameraModal
        isOpen={showOnboard}
        onClose={() => setShowOnboard(false)}
        onSuccess={fetchCameras}
      />
    </div>
  )
}
