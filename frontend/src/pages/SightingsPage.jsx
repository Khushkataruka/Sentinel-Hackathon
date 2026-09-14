import { useEffect, useState } from 'react'
import { api } from '../api.js'
import './workspace.css'
import EvidenceImage from '../components/EvidenceImage.jsx'

export default function SightingsPage() {
  const [sightings, setSightings] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true)
    setError(null)
    api
      .sightings('?limit=60')
      .then(setSightings)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
  }, [])

  return (
    <section className="workspace-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">07 / Observation stream</div>
          <h1>
            Recent movement,
            <br />
            ready for review.
          </h1>
          <p>
            The last 60 vehicle detections across the camera network. Plate reads are labeled only
            when the capture produced one.
          </p>
        </div>
        <div className="header-aside">
          <strong>{loading ? 'SYNCING' : `${sightings.length} READS`}</strong>
          <br />
          most recent detections
        </div>
      </header>
      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">Live feed</div>
            <h2>Vehicle sightings</h2>
            <p>Refresh to request the current observation window from the live service.</p>
          </div>
          <button className="primary" onClick={load} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh feed'}
          </button>
        </div>
        {error ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">!</div>
              <h2>Live feed is unavailable</h2>
              <p>
                The sightings service did not respond. Refresh when the network connection has
                recovered.
              </p>
              <button className="primary" onClick={load}>
                Try again
              </button>
              <p className="error-detail">{error}</p>
            </div>
          </div>
        ) : loading ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">···</div>
              <h2>Scanning recent detections</h2>
              <p>Collecting the latest 60 observations across the network.</p>
            </div>
          </div>
        ) : sightings.length === 0 ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">⌁</div>
              <h2>No sightings recorded yet</h2>
              <p>Check that cameras are surveyed and ingest is running, then refresh the feed.</p>
            </div>
          </div>
        ) : (
          <div className="sightings-grid sightings-feed">
            {sightings.map((sighting) => (
              <article className="sighting-card" key={sighting.read_id}>
                <EvidenceImage
                  src={api.mediaUrl(sighting.crop_ref)}
                  alt={`Vehicle sighting from ${sighting.camera_name || sighting.camera_id}`}
                  caption={sighting.seen_at && new Date(sighting.seen_at).toLocaleString()}
                  className="sighting-image"
                />
                <div className="sighting-info">
                  <div className="sighting-title">
                    <span>
                      {sighting.best_plate || sighting.make || sighting.type || [sighting.colour, sighting.type, sighting.make, sighting.model]
                        .filter(Boolean)
                        .join(' ') ||  'Unknown vehicle'}
                    </span>
                    {sighting.best_plate && <span className="badge ok">Plate read</span>}
                  </div>
                  <div className="sighting-meta">
                    <span>
                      <strong>Attributes</strong>{' '}
                      {[sighting.colour, sighting.type, sighting.make, sighting.model]
                        .filter(Boolean)
                        .join(' ') || 'None detected'}
                    </span>
                    <span>
                      <strong>Camera</strong> {sighting.camera_name || sighting.camera_id}
                    </span>
                    <span>
                      <strong>Observed</strong>{' '}
                      {sighting.seen_at && new Date(sighting.seen_at).toLocaleString()}
                    </span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </section>
  )
}
