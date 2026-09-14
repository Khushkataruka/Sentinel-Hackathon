import { useEffect, useState } from 'react'
import { api } from '../api.js'
import OnboardCameraModal from '../components/OnboardCameraModal.jsx'

export default function AdminPage() {
  const [adapters, setAdapters] = useState([])
  const [queues, setQueues] = useState([])
  const [needSurvey, setNeedSurvey] = useState([])
  const [error, setError] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)

  const reloadData = () => {
    Promise.all([api.adapters(), api.queueDepth(), api.camerasNeedingSurvey()])
      .then(([a, q, s]) => { setAdapters(a); setQueues(q); setNeedSurvey(s) })
      .catch((e) => setError(e.message))
  }

  useEffect(() => {
    reloadData()
  }, [])

  return (
    <>
      <div className="panel row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <strong>System Administration</strong> — Adapters, Queues & Camera Onboarding
        </div>
        <button
          onClick={() => setModalOpen(true)}
          style={{
            padding: '8px 16px',
            backgroundColor: '#0284c7',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            fontWeight: '600',
            cursor: 'pointer',
            fontSize: '13px',
          }}
        >
          + Onboard New Camera
        </button>
      </div>

      {error && <div className="panel warn">{error}</div>}

      <div className="panel">
        <strong>Adapters</strong>
        {/* A failed adapter is a row with a readable error, not a crashed
            process. That is the whole promise of the plug-in model. */}
        <table>
          <thead>
            <tr><th>Name</th><th>Driver</th><th>Status</th><th>Last error</th><th>Tested</th></tr>
          </thead>
          <tbody>
            {adapters.map((a) => (
              <tr key={a.name}>
                <td>{a.name}</td>
                <td className="muted">{a.driver}</td>
                <td className={a.status === 'failed' ? 'warn' : ''}>{a.status}</td>
                <td className="muted">{a.last_error || ''}</td>
                <td className="muted">
                  {a.tested_at && new Date(a.tested_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <strong>Crop queue depth</strong>
        {/* oldest_waiting_at is the number that matters: a deep queue that is
            draining is fine, a shallow one that has not moved is not. */}
        <table>
          <thead>
            <tr><th>Pipeline</th><th>Waiting</th><th>In flight</th><th>Oldest waiting</th></tr>
          </thead>
          <tbody>
            {queues.map((q) => (
              <tr key={q.pipeline}>
                <td>{q.pipeline}</td>
                <td>{q.waiting}</td>
                <td>{q.in_flight}</td>
                <td className="muted">
                  {q.oldest_waiting_at && new Date(q.oldest_waiting_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {queues.length === 0 && <p className="muted">Queues empty.</p>}
      </div>

      <div className="panel">
        <strong>Cameras awaiting survey</strong>
        <p className="muted">
          These are onboarded but have no capability profile, so they are
          permitted to claim nothing and produce no sightings.
        </p>
        <p>{needSurvey.length ? needSurvey.join(', ') : 'None — every camera is surveyed.'}</p>
      </div>

      <OnboardCameraModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={reloadData}
      />
    </>
  )
}

