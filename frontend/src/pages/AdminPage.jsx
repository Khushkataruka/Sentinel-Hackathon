import { useEffect, useState } from 'react'
import { api } from '../api.js'
import OnboardCameraModal from '../components/OnboardCameraModal.jsx'
import './workspace.css'

export default function AdminPage() {
  const [adapters, setAdapters] = useState([])
  const [queues, setQueues] = useState([])
  const [needSurvey, setNeedSurvey] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const reloadData = () => {
    setLoading(true)
    setError(null)
    Promise.all([api.adapters(), api.queueDepth(), api.camerasNeedingSurvey()])
      .then(([adapterRows, queueRows, surveyRows]) => {
        setAdapters(adapterRows)
        setQueues(queueRows)
        setNeedSurvey(surveyRows)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    reloadData()
  }, [])

  return (
    <section className="workspace-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">06 / System control</div>
          <h1>
            Keep the network
            <br />
            ready to observe.
          </h1>
          <p>
            Adapter health, crop queues, and camera capability coverage in one operational view.
          </p>
        </div>
        <div className="header-aside">
          <strong>{loading ? 'SYNCING' : `${adapters.length} ADAPTERS`}</strong>
          <br />
          registry status
        </div>
      </header>
      <div className="workspace-grid stats">
        <section className="panel stat-panel">
          <span className="data-label">Adapters</span>
          <strong>{adapters.length}</strong>
          <span className="stat-copy">registered capture interfaces</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Failed adapters</span>
          <strong>{adapters.filter((adapter) => adapter.status === 'failed').length}</strong>
          <span className="stat-copy">need attention</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Queued crops</span>
          <strong>{queues.reduce((sum, queue) => sum + (queue.waiting || 0), 0)}</strong>
          <span className="stat-copy">waiting for processing</span>
        </section>
        <section className="panel stat-panel">
          <span className="data-label">Missing surveys</span>
          <strong>{needSurvey.length}</strong>
          <span className="stat-copy">cameras unable to claim</span>
        </section>
      </div>
      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">Registry actions</div>
            <h2>Camera network controls</h2>
            <p>
              Onboard a camera to begin its registry workflow, then add a capability profile before
              it can produce sightings.
            </p>
          </div>
          <div className="row">
            <button onClick={reloadData} disabled={loading}>
              {loading ? 'Syncing…' : 'Refresh system'}
            </button>
            <button className="primary" onClick={() => setModalOpen(true)}>
              Onboard camera
            </button>
          </div>
        </div>
        {error && (
          <div className="admin-note">
            System data could not be refreshed. Existing values may be stale.{' '}
            <button onClick={reloadData}>Retry now</button>
            <p className="error-detail">{error}</p>
          </div>
        )}
      </section>
      {!error && loading ? (
        <section className="panel state-panel">
          <div className="state-inner">
            <div className="state-icon">···</div>
            <h2>Reading system state</h2>
            <p>Checking adapters, queues, and survey coverage.</p>
          </div>
        </section>
      ) : (
        <div className="workspace-grid admin">
          <section className="panel workspace-card">
            <div className="section-heading">
              <div>
                <div className="section-kicker">Capture interfaces</div>
                <h2>Adapter health</h2>
                <p>
                  A failed adapter remains visible with its error message; failures should be
                  diagnosable, not silent.
                </p>
              </div>
              <span
                className={`signal-badge ${adapters.some((adapter) => adapter.status === 'failed') ? 'alert' : ''}`}
              >
                {adapters.filter((adapter) => adapter.status === 'failed').length
                  ? 'attention needed'
                  : 'nominal'}
              </span>
            </div>
            {adapters.length ? (
              <div className="workspace-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Driver</th>
                      <th>Status</th>
                      <th>Last error</th>
                      <th>Tested</th>
                    </tr>
                  </thead>
                  <tbody>
                    {adapters.map((adapter) => (
                      <tr key={adapter.name}>
                        <td>{adapter.name}</td>
                        <td className="muted">{adapter.driver}</td>
                        <td>
                          <span
                            className={`signal-badge ${adapter.status === 'failed' ? 'alert' : ''}`}
                          >
                            {adapter.status}
                          </span>
                        </td>
                        <td className="muted">{adapter.last_error || '—'}</td>
                        <td className="muted">
                          {adapter.tested_at && new Date(adapter.tested_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="state-panel">
                <div className="state-inner">
                  <div className="state-icon">∅</div>
                  <h2>No adapters registered</h2>
                  <p>Onboard a camera to establish a capture interface.</p>
                </div>
              </div>
            )}
          </section>
          <aside>
            <section className="panel workspace-card">
              <div className="section-heading">
                <div>
                  <div className="section-kicker">Processing</div>
                  <h2>Crop queue depth</h2>
                  <p>Oldest waiting time shows whether a queue is draining.</p>
                </div>
              </div>
              {queues.length ? (
                <div className="workspace-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Pipeline</th>
                        <th>Waiting</th>
                        <th>In flight</th>
                        <th>Oldest waiting</th>
                      </tr>
                    </thead>
                    <tbody>
                      {queues.map((queue) => (
                        <tr key={queue.pipeline}>
                          <td>{queue.pipeline}</td>
                          <td>{queue.waiting}</td>
                          <td>{queue.in_flight}</td>
                          <td className="muted">
                            {queue.oldest_waiting_at &&
                              new Date(queue.oldest_waiting_at).toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="admin-note">Queues are empty. No crops are waiting for processing.</p>
              )}
            </section>
            <section className="panel workspace-card">
              <div className="section-heading">
                <div>
                  <div className="section-kicker">Capability coverage</div>
                  <h2>Awaiting survey</h2>
                  <p>
                    These cameras are onboarded but cannot produce sightings until a profile is
                    recorded.
                  </p>
                </div>
                <span className={`signal-badge ${needSurvey.length ? 'caution' : ''}`}>
                  {needSurvey.length || '0'} cameras
                </span>
              </div>
              {needSurvey.length ? (
                <div className="survey-list">
                  {needSurvey.map((camera) => (
                    <div className="survey-item" key={camera}>
                      {camera}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="admin-note">Every onboarded camera has a capability profile.</p>
              )}
            </section>
          </aside>
        </div>
      )}
      <OnboardCameraModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={reloadData}
      />
    </section>
  )
}
