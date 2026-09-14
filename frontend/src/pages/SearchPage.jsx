import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import './workspace.css'

export default function SearchPage() {
  const [registration, setRegistration] = useState('')
  const [caption, setCaption] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function run(fn) {
    setBusy(true)
    setError(null)
    try {
      setResult(await fn())
    } catch (e) {
      setError(e.message)
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="workspace-page search-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">01 / Query desk</div>
          <h1>
            Find the vehicle,
            <br />
            keep the evidence.
          </h1>
          <p>
            Start with a plate when you have one. Use description search to build a defensible route
            shortlist from the camera network.
          </p>
        </div>
        <div className="header-aside">
          <strong>Search protocol</strong>
          <br />
          plate or visual descriptor
        </div>
      </header>

      <div className="workspace-grid two">
        <section className="panel search-mode">
          <div className="mode-strip" />
          <header>
            <div className="section-kicker">A / Registration</div>
            <h2>Plate-led trace</h2>
            <p className="muted">Use the registration exactly as it was read or reported.</p>
          </header>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              run(() => api.searchRegistration({ registration_no: registration }))
            }}
          >
            <label className="field grow">
              <span className="data-label">Registration number</span>
              <input
                placeholder="e.g. KA 01 AB 1234"
                value={registration}
                onChange={(e) => setRegistration(e.target.value)}
              />
            </label>
            <button className="primary" disabled={busy || !registration.trim()}>
              {busy ? 'Querying' : 'Run trace'}
            </button>
          </form>
        </section>
        <section className="panel search-mode">
          <div className="mode-strip" />
          <header>
            <div className="section-kicker">B / Description</div>
            <h2>Visual shortlist</h2>
            <p className="muted">
              Describe distinctive colour, body type, make, or visible equipment.
            </p>
          </header>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              run(() => api.searchDescription({ caption_query: caption }))
            }}
          >
            <label className="field grow">
              <span className="data-label">Vehicle description</span>
              <input
                placeholder="White hatchback with roof carrier"
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
              />
            </label>
            <button className="primary" disabled={busy || !caption.trim()}>
              {busy ? 'Querying' : 'Find routes'}
            </button>
          </form>
        </section>
      </div>

      {error && (
        <section className="panel state-panel">
          <div className="state-inner">
            <div className="state-icon">!</div>
            <h2>Search node did not respond</h2>
            <p>
              Check the query and try again. The search was not completed, so no route result is
              being shown.
            </p>
            <button onClick={() => setError(null)}>Clear message</button>
            <p className="error-detail">{error}</p>
          </div>
        </section>
      )}

      {result && (
        <>
          <div className="workspace-grid stats search-results-summary">
            <section className="panel stat-panel">
              <span className="data-label">Registered matches</span>
              <strong>{result.rarity_count?.toLocaleString() ?? '—'}</strong>
              <span className="stat-copy">matching vehicle records</span>
            </section>
            <section className="panel stat-panel">
              <span className="data-label">Candidate sightings</span>
              <strong>{result.candidate_count ?? '—'}</strong>
              <span className="stat-copy">observations considered</span>
            </section>
            <section className="panel stat-panel">
              <span className="data-label">Possible routes</span>
              <strong>{result.route_count ?? '—'}</strong>
              <span className="stat-copy">physically plausible chains</span>
            </section>
            <section className="panel stat-panel">
              <span className="data-label">Rejected links</span>
              <strong>{result.rejected_leg_count ?? '—'}</strong>
              <span className="stat-copy">discarded on speed evidence</span>
            </section>
          </div>
          {result.rarity_count > 10000 && (
            <p className="admin-note">
              This description is broad. The result is a category, not an identification; inspect
              the route evidence before acting.
            </p>
          )}
          <section className="panel workspace-card" style={{ marginTop: 16 }}>
            <div className="section-heading">
              <div>
                <div className="section-kicker">Route candidates</div>
                <h2>Evidence-ranked route chain</h2>
                <p>
                  Competing route count stays visible beside score so confidence is never mistaken
                  for certainty.
                </p>
              </div>
              <span className="signal-badge">{result.routes?.length ?? 0} returned</span>
            </div>
            <div className="workspace-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Score</th>
                    <th>Competing</th>
                    <th>Plate anchored</th>
                    <th>Weakest camera</th>
                    <th>Camera chain</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {result.routes?.map((route) => (
                    <tr key={route.route_id}>
                      <td>#{route.rank}</td>
                      <td>{route.score?.toFixed(3)}</td>
                      <td>{route.competing_count}</td>
                      <td>
                        <span className={`signal-badge ${route.plate_anchored ? '' : 'caution'}`}>
                          {route.plate_anchored ? 'anchored' : 'visual only'}
                        </span>
                      </td>
                      <td>{route.min_trust?.toFixed(2) ?? '—'}</td>
                      <td className="muted">{route.cameras?.join(' → ')}</td>
                      <td>
                        <Link to={`/routes/${route.route_id}`}>Inspect route</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result.routes?.length === 0 && (
              <div className="state-panel">
                <div className="state-inner">
                  <div className="state-icon">⌕</div>
                  <h2>No route chain cleared validation</h2>
                  <p>
                    The candidate sightings did not form a physically possible route for this query.
                  </p>
                </div>
              </div>
            )}
          </section>
        </>
      )}
    </section>
  )
}
