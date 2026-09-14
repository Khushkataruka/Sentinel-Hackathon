import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function SightingsPage() {
  const [sightings, setSightings] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    api
      .sightings('?limit=60')
      .then((data) => setSightings(data))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  return (
    <>
      <div className="panel row" style={{ justifyContent: 'space-between' }}>
        <div>
          <h2 style={{ marginBottom: 4 }}>Live Sightings Feed</h2>
          <p className="muted" style={{ margin: 0 }}>Showing the 60 most recent vehicle detections across the network.</p>
        </div>
        <button onClick={load} className="primary" disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh Feed'}
        </button>
      </div>

      {error && <div className="panel warn">{error}</div>}

      <div className="sightings-grid">
        {sightings.map((s) => (
          <div className="sighting-card" key={s.read_id}>
            <img 
              src={api.mediaUrl(s.crop_ref)}
              alt="Vehicle crop" 
              className="sighting-image"
            />
            <div className="sighting-info">
              <div className="sighting-title">
                <span>{s.best_plate || s.make || s.type || 'Unknown Vehicle'}</span>
                {s.best_plate && <span className="badge ok">Plate Read</span>}
              </div>
              <div className="sighting-meta">
                <span>
                  <strong>Attributes:</strong> {[s.colour, s.type, s.make, s.model].filter(Boolean).join(' ') || 'None detected'}
                </span>
                <span>
                  <strong>Camera:</strong> {s.camera_name || s.camera_id}
                </span>
                <span>
                  <strong>Time:</strong> {new Date(s.seen_at).toLocaleString()}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {!loading && sightings.length === 0 && !error && (
        <div className="panel muted" style={{ textAlign: 'center', padding: '40px' }}>
          No sightings recorded yet. Make sure cameras are surveyed and ingest is running.
        </div>
      )}
    </>
  )
}
