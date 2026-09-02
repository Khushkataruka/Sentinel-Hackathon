import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

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
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="panel">
        <div className="row">
          <input
            placeholder="Registration number"
            value={registration}
            onChange={(e) => setRegistration(e.target.value)}
          />
          <button
            disabled={busy || !registration}
            onClick={() => run(() => api.searchRegistration({ registration_no: registration }))}
          >
            Search
          </button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <input
            style={{ minWidth: 340 }}
            placeholder="Description, e.g. white hatchback with a roof carrier"
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
          />
          <button
            disabled={busy || !caption}
            onClick={() => run(() => api.searchDescription({ caption_query: caption }))}
          >
            Search
          </button>
        </div>
        {error && <p className="warn">{error}</p>}
      </div>

      {result && (
        <>
          <div className="panel">
            {/* The count that turns "white Swift" into a number instead of
                a shrug. It is shown before the results, not after. */}
            <p>
              <strong>{result.rarity_count?.toLocaleString()}</strong> registered
              vehicles match this description.{' '}
              {result.rarity_count > 10000 && (
                <span className="warn">
                  That is a category, not an identification.
                </span>
              )}
            </p>
            <p className="muted">
              {result.candidate_count} candidate sightings ·{' '}
              {result.route_count} physically possible routes ·{' '}
              {result.rejected_leg_count} links rejected on speed
            </p>
          </div>

          <div className="panel">
            <table>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Score</th>
                  <th>Competing</th>
                  <th>Plate anchored</th>
                  <th>Weakest camera</th>
                  <th>Cameras</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {result.routes.map((r) => (
                  <tr key={r.route_id}>
                    <td>{r.rank}</td>
                    <td>{r.score.toFixed(3)}</td>
                    {/* Always shown, never hidden. It is a direct measure of
                        how uncertain we are. */}
                    <td>{r.competing_count}</td>
                    <td>{r.plate_anchored ? 'yes' : 'no'}</td>
                    <td>{r.min_trust?.toFixed(2)}</td>
                    <td className="muted">{r.cameras.join(' → ')}</td>
                    <td>
                      <Link to={`/routes/${r.route_id}`}>open</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}
