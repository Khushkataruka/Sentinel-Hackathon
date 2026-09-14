import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { COLOURS, MAKES, OptionSelect, VTYPES } from '../components/VehicleFields.jsx'
import './workspace.css'

const TARGETS = ['registration_no', 'colour', 'vtype', 'make', 'model']
const EMPTY_ENTRY = {
  label: '',
  reason: '',
  registration_no: '',
  colour: '',
  vtype: '',
  make: '',
  model: '',
  priority: 'review',
}

export default function WatchlistPage() {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState(EMPTY_ENTRY)
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState(null)
  const [removing, setRemoving] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = () => {
    setLoading(true)
    setError(null)
    api
      .watchlist()
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
  }, [])

  const edit = (key) => (e) => setDraft((d) => ({ ...d, [key]: e.target.value }))
  const hasTarget = TARGETS.some((k) => draft[k].trim())
  const canAdd = !adding && draft.label.trim() && draft.reason.trim() && hasTarget

  async function add(e) {
    e.preventDefault()
    setAdding(true)
    setError(null)
    setNotice(null)
    // Blank optional fields go as null, not as empty-string match criteria.
    const body = Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v.trim() || null]))
    try {
      const { backfill } = await api.addWatchlist(body)
      setNotice(
        `Added "${body.label}". History check found ${backfill.route_count} route(s) over ` +
          `${backfill.lookback_days} days and raised ${backfill.alerts_raised} alert(s).`,
      )
      setDraft(EMPTY_ENTRY)
      load()
    } catch (e) {
      setError(e.message)
    } finally {
      setAdding(false)
    }
  }

  async function remove(entry) {
    // Removal stops live alerting, so it takes a second click.
    if (confirming !== entry.id) return setConfirming(entry.id)
    setConfirming(null)
    setRemoving(entry.id)
    setError(null)
    setNotice(null)
    try {
      await api.removeWatchlist(entry.id)
      setEntries((list) => list.filter((w) => w.id !== entry.id))
      setNotice(`Removed "${entry.label}" from the watchlist.`)
    } catch (e) {
      setError(e.message)
    } finally {
      setRemoving(null)
    }
  }

  return (
    <section className="workspace-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">04 / Standing watch</div>
          <h1>
            Watch for it,
            <br />
            before it passes.
          </h1>
          <p>
            Every new detection is checked against active entries. Adding an entry also searches
            recorded history and raises alerts for what it finds.
          </p>
        </div>
        <div className="header-aside">
          <strong>{loading ? 'SYNCING' : `${entries.length} ACTIVE`}</strong>
          <br />
          watchlist entries
        </div>
      </header>

      {(error || notice) && (
        <p className={`watchlist-message ${error ? 'is-error' : ''}`} role="status">
          {error || notice}
        </p>
      )}

      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">New entry</div>
            <h2>Add a vehicle</h2>
            <p>
              Give a plate or at least one vehicle attribute. The history check can take up to a
              minute.
            </p>
          </div>
        </div>
        <form onSubmit={add} className="watchlist-form">
          <div className="search-filters-grid">
            <label className="field">
              <span className="data-label">Label</span>
              <input
                required
                placeholder="e.g. Stolen white Swift"
                value={draft.label}
                onChange={edit('label')}
              />
            </label>
            <label className="field">
              <span className="data-label">Reason</span>
              <input
                required
                placeholder="e.g. FIR 214/2026"
                value={draft.reason}
                onChange={edit('reason')}
              />
            </label>
            <label className="field">
              <span className="data-label">Registration number</span>
              <input
                placeholder="e.g. GJ 01 AB 1234"
                value={draft.registration_no}
                onChange={edit('registration_no')}
              />
            </label>
            <OptionSelect
              label="Colour"
              anyLabel="Any colour"
              options={COLOURS}
              value={draft.colour}
              onChange={edit('colour')}
            />
            <OptionSelect
              label="Vehicle type"
              anyLabel="Any type"
              options={VTYPES}
              value={draft.vtype}
              onChange={edit('vtype')}
            />
            <OptionSelect
              label="Make"
              anyLabel="Any make"
              options={MAKES}
              value={draft.make}
              onChange={edit('make')}
            />
            <label className="field">
              <span className="data-label">Model</span>
              <input placeholder="e.g. swift" value={draft.model} onChange={edit('model')} />
            </label>
            <label className="field">
              <span className="data-label">Priority</span>
              <select value={draft.priority} onChange={edit('priority')}>
                <option value="review">Review</option>
                <option value="priority">Priority</option>
              </select>
            </label>
          </div>
          <div className="search-actions">
            <button type="button" onClick={() => setDraft(EMPTY_ENTRY)} disabled={adding}>
              Clear
            </button>
            <button className="primary" disabled={!canAdd}>
              {adding ? 'Checking history…' : 'Add to watchlist'}
            </button>
          </div>
        </form>
      </section>

      <section className="panel workspace-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">Active entries</div>
            <h2>Currently watched</h2>
            <p>Removing an entry stops live alerts for it. Past alerts stay in the record.</p>
          </div>
          <button onClick={load} disabled={loading}>
            {loading ? 'Syncing…' : 'Refresh'}
          </button>
        </div>
        {!loading && entries.length === 0 ? (
          <div className="state-panel">
            <div className="state-inner">
              <div className="state-icon">○</div>
              <h2>Nothing is being watched</h2>
              <p>Add a vehicle above to start alerting on new detections.</p>
            </div>
          </div>
        ) : (
          <div className="workspace-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Target</th>
                  <th>Priority</th>
                  <th>Reason</th>
                  <th>Added</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((w) => (
                  <tr key={w.id}>
                    <td>{w.label}</td>
                    <td>
                      {[w.registration_no, w.colour, w.make, w.model, w.vtype]
                        .filter(Boolean)
                        .join(' · ')}
                    </td>
                    <td>
                      <span className={`tier ${w.priority}`}>{w.priority}</span>
                    </td>
                    <td className="muted">{w.reason}</td>
                    <td className="muted">
                      {w.created_by_name} · {new Date(w.created_at).toLocaleString()}
                    </td>
                    <td>
                      <button
                        className="danger-action"
                        disabled={removing === w.id}
                        onClick={() => remove(w)}
                        onBlur={() => setConfirming(null)}
                      >
                        {removing === w.id
                          ? 'Removing…'
                          : confirming === w.id
                            ? 'Confirm remove'
                            : 'Remove'}
                      </button>
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
