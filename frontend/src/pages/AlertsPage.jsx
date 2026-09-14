import { useEffect, useState } from 'react'
import { api } from '../api.js'
import './workspace.css'

export default function AlertsPage() {
  const [alerts, setAlerts] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [deciding, setDeciding] = useState(null)
  const load = () => {
    setLoading(true)
    setError(null)
    api
      .alerts()
      .then(setAlerts)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
  }, [])
  async function decide(id, status) {
    setDeciding(id)
    setError(null)
    try {
      await api.decideAlert(id, { status })
      load()
    } catch (e) {
      setError(e.message)
      setDeciding(null)
    }
  }

  return (
    <section className="workspace-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">03 / Decision queue</div>
          <h1>
            Alerts need
            <br />
            an accountable call.
          </h1>
          <p>
            Prioritized matches requiring an operator decision. Approve, reject, or escalate from
            the evidence record.
          </p>
        </div>
        <div className="header-aside">
          <strong>{loading ? 'SYNCING' : `${alerts.length} OPEN`}</strong>
          <br />
          operator review queue
        </div>
      </header>
      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">Live worklist</div>
            <h2>Detection alerts</h2>
            <p>
              Score and competing matches appear together to show how much separation an alert
              actually has.
            </p>
          </div>
          <button onClick={load} disabled={loading}>
            {loading ? 'Syncing…' : 'Refresh queue'}
          </button>
        </div>
        {error ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">!</div>
              <h2>Alert queue is unavailable</h2>
              <p>
                Reconnect to the service, then refresh the queue. No alert decision was recorded.
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
              <h2>Loading active alerts</h2>
              <p>Synchronizing the current decision queue.</p>
            </div>
          </div>
        ) : alerts.length === 0 ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">✓</div>
              <h2>Queue is clear</h2>
              <p>There are no new alerts awaiting an operator decision.</p>
            </div>
          </div>
        ) : (
          <div className="workspace-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tier</th>
                  <th>Score</th>
                  <th>Competing</th>
                  <th>Watchlist</th>
                  <th>Camera</th>
                  <th>Seen</th>
                  <th>Vehicle</th>
                  <th>Plate</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <span className={`tier ${a.tier}`}>{a.tier}</span>
                    </td>
                    <td>{a.score?.toFixed(3)}</td>
                    <td>{a.competing_count ?? '—'}</td>
                    <td>{a.watchlist_label || '—'}</td>
                    <td>{a.camera_id}</td>
                    <td className="muted">{a.seen_at && new Date(a.seen_at).toLocaleString()}</td>
                    <td>
                      {[a.colour, a.make, a.model, a.vtype].filter(Boolean).join(' ') ||
                        'Unknown vehicle'}
                    </td>
                    <td>{a.plate_text || '—'}</td>
                    <td>
                      <div className="row">
                        <button
                          disabled={deciding === a.id}
                          onClick={() => decide(a.id, 'approved')}
                        >
                          Approve
                        </button>
                        <button
                          className="danger-action"
                          disabled={deciding === a.id}
                          onClick={() => decide(a.id, 'rejected')}
                        >
                          Reject
                        </button>
                        <button
                          disabled={deciding === a.id}
                          onClick={() => decide(a.id, 'escalated')}
                        >
                          Escalate
                        </button>
                      </div>
                    </td>
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
