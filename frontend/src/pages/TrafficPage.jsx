import { useEffect, useState } from 'react'
import { api } from '../api.js'
import './workspace.css'

export default function TrafficPage() {
  const [rows, setRows] = useState([])
  const [showUnusable, setShowUnusable] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true)
    setError(null)
    api
      .trafficState(`?usable_only=${!showUnusable}`)
      .then(setRows)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
  }, [showUnusable])
  const lowCoverage = rows.filter((row) => row.coverage < 0.6).length

  return (
    <section className="workspace-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">05 / Network movement</div>
          <h1>
            Traffic counts,
            <br />
            qualified by coverage.
          </h1>
          <p>
            Operational traffic state by camera and time bucket. Observation coverage decides
            whether a count is comparable.
          </p>
        </div>
        <div className="header-aside">
          <strong>{loading ? 'SYNCING' : `${rows.length} BUCKETS`}</strong>
          <br />
          latest available state
        </div>
      </header>
      <div className="workspace-grid stats">
        <section className="panel stat-panel">
          <span className="data-label">Visible buckets</span>
          <strong>{rows.length}</strong>
          <span className="stat-copy">in the current view</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Coverage filter</span>
          <strong>{showUnusable ? 'OPEN' : '60%'}</strong>
          <span className="stat-copy">minimum observation rule</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Low coverage</span>
          <strong>{lowCoverage}</strong>
          <span className="stat-copy">included buckets below threshold</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Measurement</span>
          <strong>LOS</strong>
          <span className="stat-copy">level of service where supplied</span>
        </section>
      </div>
      <section className="panel workspace-card">
        <div className="tool-row">
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={showUnusable}
              onChange={(e) => setShowUnusable(e.target.checked)}
            />{' '}
            Include buckets below 60% observation coverage
          </label>
          <button onClick={load} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh state'}
          </button>
          <p className="admin-note">
            Low-coverage buckets are hidden by default: a dead camera and a quiet road look
            identical without coverage.
          </p>
        </div>
      </section>
      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">Traffic state</div>
            <h2>Camera observation buckets</h2>
            <p>
              Density remains blank where lane geometry has not been measured; flow and density are
              not interchangeable.
            </p>
          </div>
        </div>
        {error ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">!</div>
              <h2>Traffic state is unavailable</h2>
              <p>
                The live traffic service did not respond. Refresh to retrieve the current camera
                buckets.
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
              <h2>Loading traffic state</h2>
              <p>Collecting camera observation buckets.</p>
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">∅</div>
              <h2>No usable traffic buckets</h2>
              <p>Try including low-coverage buckets, or check that camera ingest is operating.</p>
            </div>
          </div>
        ) : (
          <div className="workspace-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Camera</th>
                  <th>Bucket</th>
                  <th>Coverage</th>
                  <th>Mean concurrent</th>
                  <th>Occupancy</th>
                  <th>Density estimate</th>
                  <th>Level of service</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.camera_id}-${row.bucket_start}`}>
                    <td>{row.camera_id}</td>
                    <td className="muted">{new Date(row.bucket_start).toLocaleString()}</td>
                    <td>
                      {row.coverage < 0.6 ? (
                        <span className="signal-badge caution">
                          {(row.coverage * 100).toFixed(0)}%
                        </span>
                      ) : (
                        `${(row.coverage * 100).toFixed(0)}%`
                      )}
                    </td>
                    <td>{row.mean_concurrent?.toFixed(1)}</td>
                    <td>
                      {row.mean_occupancy != null
                        ? `${(row.mean_occupancy * 100).toFixed(0)}%`
                        : '—'}
                    </td>
                    <td>
                      {row.density_vpkm != null ? (
                        `${row.density_vpkm.toFixed(1)} veh/km`
                      ) : (
                        <span className="muted">not measurable</span>
                      )}
                    </td>
                    <td>{row.los || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  )
}
