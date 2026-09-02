import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ViolationsPage() {
  const [types, setTypes] = useState([])
  const [rows, setRows] = useState([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState(null)

  const load = () =>
    api
      .violations(filter ? `?violation_type=${filter}` : '')
      .then(setRows)
      .catch((e) => setError(e.message))

  useEffect(() => { api.violationTypes().then(setTypes).catch(() => {}) }, [])
  useEffect(() => { load() }, [filter])

  async function review(id, status) {
    await api.reviewViolation(id, { review_status: status })
    load()
  }

  return (
    <>
      <div className="panel row">
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t.code} value={t.code}>{t.label}</option>
          ))}
        </select>
        {/* We report violations. We do not issue challans: automatic fining
            needs an identified owner, and plates are not readable on most of
            this estate. */}
        <span className="muted">
          Review queue. Confirming a violation records it; it does not issue a challan.
        </span>
      </div>

      <div className="panel">
        {error && <p className="warn">{error}</p>}
        <table>
          <thead>
            <tr>
              <th>Type</th><th>Camera</th><th>Seen</th><th>Confidence</th>
              <th>Trust</th><th>Vehicle</th><th>Through glass</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.id}>
                <td>{v.label}</td>
                <td>{v.camera_id}</td>
                <td className="muted">{new Date(v.seen_at).toLocaleString()}</td>
                <td>{v.confidence?.toFixed(2)}</td>
                <td>{v.trust_level?.toFixed(2)}</td>
                <td>{[v.colour, v.make, v.model].filter(Boolean).join(' ') || v.class}</td>
                {/* Seatbelt and phone use need to resolve a small object
                    through a windscreen at pole distance. Flagged, because a
                    reviewer should know before they judge the image. */}
                <td>{v.needs_glass_penetration ? <span className="warn">yes</span> : 'no'}</td>
                <td className="row">
                  <button onClick={() => review(v.id, 'confirmed')}>Confirm</button>
                  <button onClick={() => review(v.id, 'rejected')}>Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && !error && <p className="muted">Nothing pending review.</p>}
      </div>
    </>
  )
}
