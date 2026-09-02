import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function TrafficPage() {
  const [rows, setRows] = useState([])
  const [showUnusable, setShowUnusable] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api
      .trafficState(`?usable_only=${!showUnusable}`)
      .then(setRows)
      .catch((e) => setError(e.message))
  }, [showUnusable])

  return (
    <>
      <div className="panel row">
        <label>
          <input
            type="checkbox"
            checked={showUnusable}
            onChange={(e) => setShowUnusable(e.target.checked)}
          />{' '}
          include buckets below 60% observation coverage
        </label>
        {/* Below 60% observed, the numbers are not comparable to anything.
            The dashboard says so rather than drawing a reassuring line
            through a dead camera. */}
        <span className="muted">
          Low-coverage buckets are hidden by default: a dead camera and a quiet
          road look identical without their coverage.
        </span>
      </div>

      <div className="panel">
        {error && <p className="warn">{error}</p>}
        <table>
          <thead>
            <tr>
              <th>Camera</th><th>Bucket</th><th>Coverage</th>
              <th>Mean concurrent</th><th>Occupancy</th>
              <th>Density (est.)</th><th>Level of service</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.camera_id}-${r.bucket_start}`}>
                <td>{r.camera_id}</td>
                <td className="muted">{new Date(r.bucket_start).toLocaleString()}</td>
                <td className={r.coverage < 0.6 ? 'warn' : ''}>
                  {(r.coverage * 100).toFixed(0)}%
                </td>
                <td>{r.mean_concurrent?.toFixed(1)}</td>
                <td>{r.mean_occupancy != null ? `${(r.mean_occupancy * 100).toFixed(0)}%` : '—'}</td>
                {/* Empty where the lane geometry was never measured. Flow is
                    available everywhere; density is not, and calling one the
                    other would be wrong. */}
                <td>{r.density_vpkm != null ? `${r.density_vpkm.toFixed(1)} veh/km` : <span className="muted">not measurable</span>}</td>
                <td>{r.los || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
