import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { MapContainer, TileLayer, Polyline, CircleMarker } from 'react-leaflet'
import { api } from '../api.js'
import './workspace.css'
import EvidenceImage from '../components/EvidenceImage.jsx'

export default function RoutePage() {
  const { routeId } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true)
    setError(null)
    api
      .route(routeId)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
  }, [routeId])
  if (loading)
    return (
      <section className="workspace-page">
        <div className="panel state-panel">
          <div className="state-inner">
            <div className="state-icon">···</div>
            <h2>Reconstructing route</h2>
            <p>Loading observations and validating the travel chain.</p>
          </div>
        </div>
      </section>
    )
  if (error)
    return (
      <section className="workspace-page">
        <div className="panel state-panel">
          <div className="state-inner">
            <div className="state-icon">!</div>
            <h2>Route evidence is unavailable</h2>
            <p>The route could not be loaded. Refresh the evidence record to try again.</p>
            <button className="primary" onClick={load}>
              Reload route
            </button>
            <p className="error-detail">{error}</p>
          </div>
        </div>
      </section>
    )

  const legs = data.legs || []
  const points = legs.flatMap((leg) => [
    [leg.from_lat, leg.from_lon],
    [leg.to_lat, leg.to_lon],
  ])
  const uniqueSightings = []
  legs.forEach((leg) => {
    if (!uniqueSightings.find((s) => s.read_id === leg.from_read_id))
      uniqueSightings.push({
        read_id: leg.from_read_id,
        camera: leg.from_camera,
        time: leg.from_seen_at,
        crop_ref: leg.from_crop_ref,
      })
    if (!uniqueSightings.find((s) => s.read_id === leg.to_read_id))
      uniqueSightings.push({
        read_id: leg.to_read_id,
        camera: leg.to_camera,
        time: leg.to_seen_at,
        crop_ref: leg.to_crop_ref,
      })
  })

  return (
    <section className="workspace-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">02 / Route reconstruction</div>
          <h1>
            Observed path,
            <br />
            with its limits.
          </h1>
          <p>
            Route {routeId}. The chain below separates observed travel from gaps and physically
            impossible links.
          </p>
        </div>
        <div className="header-aside">
          <strong>ROUTE SCORE {data.route?.score?.toFixed(3) ?? '—'}</strong>
          <br />
          evidence record
        </div>
      </header>
      <div className="workspace-grid stats">
        <section className="panel stat-panel">
          <span className="data-label">Route score</span>
          <strong>{data.route?.score?.toFixed(3) ?? '—'}</strong>
          <span className="stat-copy">ranking signal</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Competing routes</span>
          <strong>{data.route?.competing_count ?? '—'}</strong>
          <span className="stat-copy">alternate chains</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Weakest camera</span>
          <strong>{data.route?.min_trust?.toFixed(2) ?? '—'}</strong>
          <span className="stat-copy">minimum trust score</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Plate evidence</span>
          <strong>{data.route?.plate_anchored ? 'YES' : 'NO'}</strong>
          <span className="stat-copy">route anchored by plate</span>
        </section>
      </div>
      {data.gaps?.length > 0 && (
        <section className="panel workspace-card">
          <div className="section-heading">
            <div>
              <div className="section-kicker">Coverage exceptions</div>
              <h2>Unobserved stretches</h2>
              <p>
                Camera silence is not evidence of absence. These intervals are retained in the route
                record.
              </p>
            </div>
            <span className="signal-badge caution">{data.gaps.length} gaps</span>
          </div>
          <ul className="gap-list">
            {data.gaps.map((gap) => (
              <li key={gap.seq}>
                <strong>
                  {gap.from_camera} → {gap.to_camera}
                </strong>{' '}
                · {Math.round(gap.gap_s)} seconds without camera coverage
              </li>
            ))}
          </ul>
        </section>
      )}
      {points.length > 0 && (
        <section className="panel workspace-card">
          <div className="section-heading">
            <div>
              <div className="section-kicker">Observed geography</div>
              <h2>Camera-to-camera trace</h2>
            </div>
            <span className="signal-badge">{uniqueSightings.length} sightings</span>
          </div>
          <MapContainer center={points[0]} zoom={10} className="route-map">
            <TileLayer
              url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution="&copy; OpenStreetMap contributors"
            />
            <Polyline positions={points} pathOptions={{ color: '#b7ff45', weight: 4 }} />
            {points.map((point, index) => (
              <CircleMarker
                key={index}
                center={point}
                radius={5}
                pathOptions={{
                  color: '#080b09',
                  fillColor: '#b7ff45',
                  fillOpacity: 1,
                }}
              />
            ))}
          </MapContainer>
        </section>
      )}
      {uniqueSightings.length > 0 && (
        <section className="panel workspace-card">
          <div className="section-heading">
            <div>
              <div className="section-kicker">Visual evidence</div>
              <h2>Vehicle sightings along this chain</h2>
            </div>
          </div>
          <div className="sighting-strip">
            {uniqueSightings.map((sighting) => (
              <figure key={sighting.read_id}>
                {sighting.crop_ref ? (
                  <EvidenceImage
                    src={api.mediaUrl(sighting.crop_ref)}
                    alt={`Vehicle at ${sighting.camera}`}
                    caption={sighting.time && new Date(sighting.time).toLocaleString()}
                  />
                ) : (
                  <div className="state-icon">∅</div>
                )}
                <figcaption>
                  <strong>{sighting.camera}</strong>
                  <br />
                  {sighting.time && new Date(sighting.time).toLocaleString()}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}
      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">Leg audit</div>
            <h2>Accepted travel links</h2>
            <p>
              Distance, elapsed time, and implied speed stay in the record so the route can be
              reviewed.
            </p>
          </div>
        </div>
        <div className="workspace-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Leg</th>
                <th>From</th>
                <th>To</th>
                <th>Distance</th>
                <th>Elapsed</th>
                <th>Implied speed</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {legs.map((leg) => (
                <tr key={leg.id}>
                  <td>#{leg.seq}</td>
                  <td>{leg.from_camera}</td>
                  <td>{leg.to_camera}</td>
                  <td>{leg.distance_km?.toFixed(2)} km</td>
                  <td>{Math.round(leg.elapsed_s)} s</td>
                  <td>{leg.required_speed_kmh?.toFixed(0)} km/h</td>
                  <td className="muted">{leg.drop_reason || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {data.rejected_legs?.length > 0 && (
        <section className="panel workspace-card">
          <div className="section-heading">
            <div>
              <div className="section-kicker">Excluded evidence</div>
              <h2>Links rejected by validation</h2>
              <p>
                Retained for audit: these sightings could not be connected at a plausible speed.
              </p>
            </div>
            <span className="signal-badge alert">{data.rejected_legs.length} rejected</span>
          </div>
          <div className="workspace-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>From</th>
                  <th>To</th>
                  <th>Implied speed</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {data.rejected_legs.map((leg) => (
                  <tr key={leg.id}>
                    <td>{leg.from_camera}</td>
                    <td>{leg.to_camera}</td>
                    <td>{leg.required_speed_kmh?.toFixed(0)} km/h</td>
                    <td className="muted">{leg.drop_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </section>
  )
}
