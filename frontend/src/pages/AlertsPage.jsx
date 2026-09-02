import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function AlertsPage() {
  const [alerts, setAlerts] = useState([])
  const [error, setError] = useState(null)

  const load = () => api.alerts().then(setAlerts).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  async function decide(id, status) {
    await api.decideAlert(id, { status })
    load()
  }

  return (
    <div className="panel">
      {error && <p className="warn">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Tier</th><th>Score</th><th>Competing</th><th>Watchlist</th>
            <th>Camera</th><th>Seen</th><th>Vehicle</th><th>Plate</th><th />
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id}>
              <td><span className={`tier ${a.tier}`}>{a.tier}</span></td>
              <td>{a.score?.toFixed(3)}</td>
              {/* Travels with the score, always. A strong score that is one
                  of forty equally strong scores is a shortlist. */}
              <td>{a.competing_count ?? '—'}</td>
              <td>{a.watchlist_label || '—'}</td>
              <td>{a.camera_id}</td>
              <td className="muted">
                {a.seen_at && new Date(a.seen_at).toLocaleString()}
              </td>
              <td>{[a.colour, a.make, a.model, a.vtype].filter(Boolean).join(' ')}</td>
              <td>{a.plate_text || '—'}</td>
              <td className="row">
                <button onClick={() => decide(a.id, 'approved')}>Approve</button>
                <button onClick={() => decide(a.id, 'rejected')}>Reject</button>
                <button onClick={() => decide(a.id, 'escalated')}>Escalate</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {alerts.length === 0 && !error && <p className="muted">No new alerts.</p>}
    </div>
  )
}
