import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { MapContainer, TileLayer, Polyline, CircleMarker } from 'react-leaflet'
import { api } from '../api.js'

export default function RoutePage() {
  const { routeId } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.route(routeId).then(setData).catch((e) => setError(e.message))
  }, [routeId])

  if (error) return <div className="panel warn">{error}</div>
  if (!data) return <div className="panel muted">Loading…</div>

  const points = data.legs.flatMap((l) => [
    [l.from_lat, l.from_lon],
    [l.to_lat, l.to_lon]
  ])

  const uniqueSightings = []
  if (data.legs) {
    data.legs.forEach(l => {
      if (!uniqueSightings.find(s => s.read_id === l.from_read_id)) {
        uniqueSightings.push({ read_id: l.from_read_id, camera: l.from_camera, time: l.from_seen_at, crop_ref: l.from_crop_ref })
      }
      if (!uniqueSightings.find(s => s.read_id === l.to_read_id)) {
        uniqueSightings.push({ read_id: l.to_read_id, camera: l.to_camera, time: l.to_seen_at, crop_ref: l.to_crop_ref })
      }
    })
  }

  return (
    <>
      <div className="panel row">
        <span>score <strong>{data.route.score?.toFixed(3)}</strong></span>
        <span>competing routes <strong>{data.route.competing_count}</strong></span>
        <span>weakest camera <strong>{data.route.min_trust?.toFixed(2)}</strong></span>
        <span>plate anchored <strong>{data.route.plate_anchored ? 'yes' : 'no'}</strong></span>
      </div>

      {data.gaps.length > 0 && (
        <div className="panel">
          {/* Where no camera covers a stretch, say so. Silence is not
              evidence of absence. */}
          <strong className="warn">Uncovered stretches</strong>
          <ul className="muted">
            {data.gaps.map((g) => (
              <li key={g.seq}>
                {g.from_camera} → {g.to_camera}: {Math.round(g.gap_s)}s with no camera coverage
              </li>
            ))}
          </ul>
        </div>
      )}

      {points.length > 0 && (
        <MapContainer center={points[0]} zoom={10} className="map" style={{ height: 340 }}>
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <Polyline positions={points} pathOptions={{ color: '#4f9d69' }} />
          {points.map((p, i) => (
            <CircleMarker key={i} center={p} radius={5} />
          ))}
        </MapContainer>
      )}

      {uniqueSightings.length > 0 && (
        <div className="panel">
          <strong>Vehicle Sightings</strong>
          <div style={{ display: 'flex', overflowX: 'auto', gap: '10px', marginTop: '10px' }}>
            {uniqueSightings.map(s => (
              <div key={s.read_id} style={{ textAlign: 'center', minWidth: '150px' }}>
                <img 
                  src={`/media/${s.crop_ref}`} 
                  alt={`Sighting at ${s.camera}`} 
                  style={{ maxHeight: '150px', border: '1px solid #ccc', borderRadius: '4px', objectFit: 'contain' }} 
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
                <div className="muted" style={{ fontSize: '0.8em', marginTop: '4px' }}>{s.camera}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Leg</th><th>From</th><th>To</th>
              <th>Distance</th><th>Elapsed</th><th>Implied speed</th><th>Note</th>
            </tr>
          </thead>
          <tbody>
            {data.legs.map((l) => (
              <tr key={l.id}>
                <td>{l.seq}</td>
                <td>{l.from_camera}</td>
                <td>{l.to_camera}</td>
                <td>{l.distance_km?.toFixed(2)} km</td>
                <td>{Math.round(l.elapsed_s)} s</td>
                <td>{l.required_speed_kmh?.toFixed(0)} km/h</td>
                <td className="muted">{l.drop_reason || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.rejected_legs.length > 0 && (
        <div className="panel">
          {/* Kept deliberately. "Why was this link dropped" is an audit
              answer, and throwing it away makes it unanswerable. */}
          <strong>Links we rejected</strong>
          <table>
            <thead>
              <tr><th>From</th><th>To</th><th>Implied speed</th><th>Reason</th></tr>
            </thead>
            <tbody>
              {data.rejected_legs.map((l) => (
                <tr key={l.id}>
                  <td>{l.from_camera}</td>
                  <td>{l.to_camera}</td>
                  <td>{l.required_speed_kmh?.toFixed(0)} km/h</td>
                  <td className="muted">{l.drop_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
