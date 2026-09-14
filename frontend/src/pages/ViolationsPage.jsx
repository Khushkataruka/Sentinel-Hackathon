import { useEffect, useState } from 'react'
import { api } from '../api.js'

function VehicleCutoutThumb({ violation, onOpenModal }) {
  const [imgError, setImgError] = useState(false)
  const imgUrl = api.mediaUrl(violation.crop_ref || violation.evidence_ref)

  if (!imgUrl || imgError) {
    return (
      <div
        className="vehicle-cutout-placeholder"
        onClick={() => onOpenModal(violation)}
        title={imgError ? 'Image failed to load — click for details' : 'No crop stored — click for details'}
      >
        <span>{imgError ? 'Failed' : 'No crop'}</span>
      </div>
    )
  }

  return (
    <div className="vehicle-cutout-wrapper" onClick={() => onOpenModal(violation)}>
      <img
        src={imgUrl}
        alt={`Vehicle ${violation.class || 'crop'}`}
        className="vehicle-cutout-thumb"
        loading="lazy"
        onError={() => setImgError(true)}
        title="Click to view full cutout and details"
      />
    </div>
  )
}

function ViolationModal({ violation, onClose, onReview }) {
  const [imgError, setImgError] = useState(false)

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  if (!violation) return null

  const imgUrl = api.mediaUrl(violation.crop_ref || violation.evidence_ref)
  const vehicleDesc =
    [violation.colour, violation.make, violation.model].filter(Boolean).join(' ') ||
    violation.class ||
    'Unknown vehicle'

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-content violation-modal" role="dialog" aria-modal="true">
        <div className="modal-header">
          <div className="row" style={{ gap: '10px' }}>
            <span className="badge priority">{violation.label}</span>
            {violation.mv_act_section && (
              <span className="badge dismiss">Sec. {violation.mv_act_section}</span>
            )}
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            &times;
          </button>
        </div>

        <div className="violation-modal-body">
          <div className="violation-modal-image-container">
            {imgUrl && !imgError ? (
              <img
                src={imgUrl}
                alt={`Vehicle cutout for ${vehicleDesc}`}
                className="violation-modal-image"
                onError={() => setImgError(true)}
              />
            ) : (
              <div className="violation-modal-image-placeholder">
                <span style={{ fontSize: '2rem', marginBottom: '8px' }}>🚗</span>
                <span>
                  {imgError
                    ? 'Cutout image could not be loaded from storage'
                    : 'No cutout image recorded for this sighting'}
                </span>
                <span className="muted" style={{ fontSize: '0.75rem', marginTop: '4px' }}>
                  {violation.crop_ref || violation.evidence_ref || 'Reference: none'}
                </span>
              </div>
            )}
          </div>

          <div className="violation-modal-details">
            <h3 style={{ marginBottom: '12px', fontSize: '1.1rem' }}>{vehicleDesc}</h3>

            <div className="details-grid">
              <div className="detail-item">
                <span className="detail-label">Plate Read</span>
                <span className="detail-value plate-text">
                  {violation.plate_text || 'None / Not readable'}
                </span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Camera</span>
                <span className="detail-value">{violation.camera_id}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Timestamp</span>
                <span className="detail-value">
                  {new Date(violation.seen_at).toLocaleString()}
                </span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Confidence</span>
                <span className="detail-value">
                  {violation.confidence != null
                    ? `${(violation.confidence * 100).toFixed(1)}%`
                    : 'N/A'}
                </span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Camera Trust</span>
                <span className="detail-value">
                  {violation.trust_level != null
                    ? `${(violation.trust_level * 100).toFixed(0)}%`
                    : 'N/A'}
                </span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Windscreen Check</span>
                <span className="detail-value">
                  {violation.needs_glass_penetration ? (
                    <span className="warn">Required (through glass)</span>
                  ) : (
                    'Not required (exterior)'
                  )}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <div className="row" style={{ gap: '10px' }}>
            <button
              className="primary"
              onClick={() => {
                onReview(violation.id, 'confirmed')
                onClose()
              }}
            >
              Confirm Violation
            </button>
            <button
              onClick={() => {
                onReview(violation.id, 'rejected')
                onClose()
              }}
            >
              Reject Violation
            </button>
          </div>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

export default function ViolationsPage() {
  const [types, setTypes] = useState([])
  const [rows, setRows] = useState([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState(null)
  const [selectedViolation, setSelectedViolation] = useState(null)

  const load = () =>
    api
      .violations(filter ? `?violation_type=${filter}` : '')
      .then(setRows)
      .catch((e) => setError(e.message))

  useEffect(() => {
    api.violationTypes().then(setTypes).catch(() => {})
  }, [])

  useEffect(() => {
    load()
  }, [filter])

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
            <option key={t.code} value={t.code}>
              {t.label}
            </option>
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
              <th>Type</th>
              <th>Camera</th>
              <th>Seen</th>
              <th>Cutout</th>
              <th>Vehicle</th>
              <th>Confidence</th>
              <th>Trust</th>
              <th>Through glass</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.id}>
                <td>{v.label}</td>
                <td>{v.camera_id}</td>
                <td className="muted">{new Date(v.seen_at).toLocaleString()}</td>
                <td>
                  <VehicleCutoutThumb violation={v} onOpenModal={setSelectedViolation} />
                </td>
                <td>{[v.colour, v.make, v.model].filter(Boolean).join(' ') || v.class}</td>
                <td>{v.confidence?.toFixed(2)}</td>
                <td>{v.trust_level?.toFixed(2)}</td>
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

      {selectedViolation && (
        <ViolationModal
          violation={selectedViolation}
          onClose={() => setSelectedViolation(null)}
          onReview={review}
        />
      )}
    </>
  )
}
