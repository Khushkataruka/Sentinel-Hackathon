import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import Icon from '../components/Icon.jsx'
import EvidenceImage from '../components/EvidenceImage.jsx'
import './workspace.css'
import './violations.css'

const STATUSES = [
  ['pending_review', 'Pending review'],
  ['confirmed', 'Confirmed'],
  ['rejected', 'Rejected'],
]
const percentage = (value) => (value == null ? '—' : `${Math.round(Number(value) * 100)}%`)

function ReviewCard({ violation, index, busy, onReview }) {
  const vehicle =
    [violation.colour, violation.make, violation.model].filter(Boolean).join(' ') ||
    violation.class ||
    'Unclassified vehicle'
  return (
    <article className="violation-card" style={{ '--card-index': Math.min(index, 7) }}>
      <div className="violation-visual">
        <EvidenceImage
          src={api.mediaUrl(violation.evidence_ref || violation.crop_ref)}
          alt={`${violation.label || violation.violation_type} · ${violation.camera_id}`}
          caption={`${vehicle} · ${new Date(violation.seen_at).toLocaleString()}`}
          badge={`EVIDENCE / ${String(violation.id).padStart(5, '0')}`}
        />
      </div>
      <div className="violation-card-content">
        <div className="violation-card-kicker">
          <span>{violation.camera_id}</span>
          <span>
            {new Date(violation.seen_at).toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        </div>
        <h2>{violation.label || violation.violation_type?.replaceAll('_', ' ')}</h2>
        <p className="violation-vehicle">
          {vehicle}
          {violation.plate_text && <span className="violation-plate">{violation.plate_text}</span>}
        </p>
        <div className="violation-confidence">
          <div>
            <span>Detection confidence</span>
            <strong>{percentage(violation.confidence)}</strong>
            <i>
              <b
                style={{
                  width: `${Math.max(0, Math.min(100, Number(violation.confidence || 0) * 100))}%`,
                }}
              />
            </i>
          </div>
          <div>
            <span>Camera trust</span>
            <strong>{percentage(violation.trust_level)}</strong>
          </div>
        </div>
        <div className="violation-card-context">
          <span>
            {new Date(violation.seen_at).toLocaleDateString([], {
              day: '2-digit',
              month: 'short',
              year: 'numeric',
            })}
          </span>
          {violation.needs_glass_penetration && (
            <span className="violation-caution">
              <Icon name="alert" size={11} />
              Through-glass evidence
            </span>
          )}
        </div>
        {violation.review_status === 'pending_review' ? (
          <div className="violation-decisions">
            <button
              type="button"
              className="confirm-violation"
              disabled={busy}
              onClick={() => onReview(violation.id, 'confirmed')}
            >
              <Icon name="check" size={15} />
              Confirm
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onReview(violation.id, 'rejected')}
            >
              <Icon name="close" size={14} />
              Reject
            </button>
          </div>
        ) : (
          <div className={`violation-reviewed ${violation.review_status}`}>
            <Icon name={violation.review_status === 'confirmed' ? 'check' : 'close'} size={14} />
            {violation.review_status} observation
          </div>
        )}
      </div>
    </article>
  )
}

export default function ViolationsPage() {
  const [types, setTypes] = useState([])
  const [rows, setRows] = useState([])
  const [filter, setFilter] = useState('')
  const [status, setStatus] = useState('pending_review')
  const [query, setQuery] = useState('')
  const [layout, setLayout] = useState('grid')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reviewing, setReviewing] = useState(null)
  const [notice, setNotice] = useState('')
  const generation = useRef(0)
  const load = useCallback(async () => {
    const id = ++generation.current
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        review_status: status,
        limit: '100',
      })
      if (filter) params.set('violation_type', filter)
      const result = await api.violations(`?${params}`)
      if (id === generation.current) setRows(result)
    } catch (err) {
      if (id === generation.current) setError(err.message)
    } finally {
      if (id === generation.current) setLoading(false)
    }
  }, [status, filter])
  useEffect(() => {
    api
      .violationTypes()
      .then(setTypes)
      .catch(() => {})
  }, [])
  useEffect(() => {
    setRows([])
    load()
    return () => {
      generation.current++
    }
  }, [load])
  const visible = useMemo(
    () =>
      rows.filter((row) =>
        `${row.camera_id} ${row.label} ${row.plate_text || ''} ${row.colour || ''} ${row.make || ''}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
      ),
    [rows, query],
  )
  async function review(id, decision) {
    setReviewing(id)
    setNotice('')
    try {
      await api.reviewViolation(id, { review_status: decision })
      setNotice(`Observation #${id} ${decision}. Review decision recorded.`)
      await load()
    } catch (err) {
      setNotice(`Review could not be saved. ${err.message}`)
    } finally {
      setReviewing(null)
    }
  }
  const summary = [
    ['Observations', rows.length, 'in this worklist', 'scan'],
    [
      'Camera sources',
      new Set(rows.map((row) => row.camera_id)).size,
      'distinct viewpoints',
      'camera',
    ],
    [
      'Through-glass',
      rows.filter((row) => row.needs_glass_penetration).length,
      'require closer inspection',
      'target',
    ],
  ]

  return (
    <section className="workspace-page violations-page">
      <header className="violation-page-header">
        <div>
          <div className="workspace-kicker">
            <span className="violation-header-dot" />
            INTELLIGENCE / EVIDENCE REVIEW
          </div>
          <h1>
            Evidence before <em>action.</em>
          </h1>
          <p>Inspect the moment. Make an informed call.</p>
        </div>
        <div className="violation-header-seal" aria-hidden="true">
          <Icon name="shield" size={30} />
          <span>
            HUMAN
            <br />
            VERIFIED
          </span>
        </div>
      </header>
      <div className="violation-overview">
        {summary.map(([label, value, note, icon]) => (
          <div key={label}>
            <Icon name={icon} size={19} />
            <div>
              <span>{label}</span>
              <strong>{loading ? '—' : value.toString().padStart(2, '0')}</strong>
              <small>{note}</small>
            </div>
          </div>
        ))}
        <div className="violation-review-note">
          <Icon name="shield" size={20} />
          <p>
            Every decision stays in the record.
            <span>
              Confirming an observation records a review decision; it does not issue a fine.
            </span>
          </p>
        </div>
      </div>
      <div className="violation-workbench">
        <div className="violation-tabs" role="group" aria-label="Review status">
          {STATUSES.map(([key, label]) => (
            <button
              type="button"
              key={key}
              aria-pressed={key === status}
              className={key === status ? 'active' : ''}
              disabled={reviewing != null}
              onClick={() => {
                setStatus(key)
                setNotice('')
              }}
            >
              {label}
              {key === status && !loading && <span>{rows.length}</span>}
            </button>
          ))}
          <button
            type="button"
            className="violation-refresh"
            aria-label="Refresh violations"
            disabled={loading || reviewing != null}
            onClick={load}
          >
            <Icon name="refresh" size={15} />
          </button>
        </div>
        <div className="violation-toolbar">
          <label className="violation-search">
            <Icon name="search" size={16} />
            <input
              aria-label="Search violation evidence"
              placeholder="Search camera, vehicle or plate…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label className="violation-type">
            <span>TYPE</span>
            <select
              aria-label="Violation type"
              value={filter}
              disabled={reviewing != null}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="">All violations</option>
              {types.map((type) => (
                <option key={type.code} value={type.code}>
                  {type.label}
                </option>
              ))}
            </select>
          </label>
          <div className="violation-layout" role="group" aria-label="Evidence layout">
            <button
              type="button"
              aria-label="Grid layout"
              aria-pressed={layout === 'grid'}
              onClick={() => setLayout('grid')}
            >
              <Icon name="grid" size={16} />
            </button>
            <button
              type="button"
              aria-label="Compact layout"
              aria-pressed={layout === 'compact'}
              onClick={() => setLayout('compact')}
            >
              <Icon name="list" size={16} />
            </button>
          </div>
        </div>
        {notice && (
          <div className="violation-notice" role="status">
            <Icon name="pulse" size={15} />
            <span>{notice}</span>
            <button aria-label="Dismiss review message" onClick={() => setNotice('')}>
              <Icon name="close" size={14} />
            </button>
          </div>
        )}
        <div className="violation-results-heading">
          <span>
            {loading
              ? 'RETRIEVING OBSERVATIONS'
              : `${visible.length.toString().padStart(2, '0')} OBSERVATIONS / ${STATUSES.find(([key]) => key === status)[1].toUpperCase()}`}
          </span>
          <span>Latest 100 · newest first</span>
        </div>
        {error ? (
          <div className="violation-empty">
            <div className="violation-empty-orbit">
              <Icon name="alert" size={32} />
            </div>
            <span className="workspace-kicker">CONNECTION INTERRUPTED</span>
            <h2>The evidence queue is unavailable.</h2>
            <p>{error}</p>
            <button className="primary" onClick={load}>
              Retry connection
            </button>
          </div>
        ) : loading ? (
          <div className="violation-skeletons" aria-label="Loading observations" role="status">
            {[0, 1, 2].map((index) => (
              <div key={index}>
                <i />
                <span />
                <span />
              </div>
            ))}
          </div>
        ) : visible.length ? (
          <div className={`violation-grid ${layout === 'compact' ? 'is-compact' : ''}`}>
            {visible.map((row, index) => (
              <ReviewCard
                key={row.id}
                violation={row}
                index={index}
                busy={reviewing != null}
                onReview={review}
              />
            ))}
          </div>
        ) : (
          <div className="violation-empty">
            <div className="violation-empty-orbit">
              <Icon name={query || filter ? 'search' : 'shield'} size={36} />
            </div>
            <span className="workspace-kicker">
              {query || filter ? 'FILTERED WORKLIST' : 'REVIEW WORKSPACE READY'}
            </span>
            <h2>
              {query || filter
                ? 'No observations match.'
                : status === 'pending_review'
                  ? 'A clear queue. A sharper focus.'
                  : `No ${status} observations yet.`}
            </h2>
            <p>
              {query || filter
                ? 'Try another camera, vehicle description, or violation type.'
                : 'Captured evidence will appear here with its source, confidence, and review controls.'}
            </p>
            {query || filter ? (
              <button
                onClick={() => {
                  setQuery('')
                  setFilter('')
                }}
              >
                Clear filters <Icon name="refresh" size={13} />
              </button>
            ) : (
              <Link to="/sightings">
                Explore vehicle sightings <Icon name="arrow" size={14} />
              </Link>
            )}
            <div className="violation-process">
              <span>
                <b>01</b> Capture
              </span>
              <i />
              <span>
                <b>02</b> Inspect
              </span>
              <i />
              <span>
                <b>03</b> Review
              </span>
            </div>
          </div>
        )}
        <footer className="violation-workbench-footer">
          <span>
            <Icon name="expand" size={12} />
            Click any evidence image to inspect at full size
          </span>
          <span>OPERATOR REVIEW REQUIRED</span>
        </footer>
      </div>
    </section>
  )
}
