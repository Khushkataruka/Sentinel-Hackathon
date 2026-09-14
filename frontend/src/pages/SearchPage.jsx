import { useState, useEffect } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import './workspace.css'

const COLOURS = ['white', 'black', 'grey', 'silver', 'red', 'blue', 'green', 'yellow', 'brown', 'orange', 'maroon']
const VTYPES = ['hatchback', 'sedan', 'suv', 'muv', 'pickup', 'van', 'bus', 'truck', 'motorcycle', 'bicycle']
const MAKES = ['maruti', 'hyundai', 'tata', 'mahindra', 'toyota', 'honda', 'kia']
const WATCH_TARGETS = ['registration_no', 'colour', 'vtype', 'make', 'model']
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

function OptionSelect({ label, anyLabel, options, value, onChange }) {
  return (
    <label className="field">
      <span className="data-label">{label}</span>
      <select value={value} onChange={onChange}>
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o.charAt(0).toUpperCase() + o.slice(1)}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function SearchPage() {
  const [searchParams] = useSearchParams()
  const sightingId = searchParams.get('sighting')

  const [registration, setRegistration] = useState('')
  const [caption, setCaption] = useState('')
  const [colour, setColour] = useState('')
  const [vtype, setVtype] = useState('')
  const [make, setMake] = useState('')
  const [model, setModel] = useState('')
  const [topK, setTopK] = useState(20)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('sightings') // 'sightings' or 'routes'

  useEffect(() => {
    if (sightingId) {
      // By default, restrict to ±24 hours from the sighting timestamp.
      // We pass an empty body and let the backend default to no time restriction,
      // or we can pass a time window if we had the sighting's timestamp.
      // Since we don't have it on the frontend, let the backend handle the full search
      // or we could fetch the sighting first. For now, empty body.
      run(() => api.searchSighting(sightingId))
    }
  }, [sightingId])

  async function run(fn) {
    setBusy(true)
    setError(null)
    try {
      setResult(await fn())
      setActiveTab('sightings')
    } catch (e) {
      setError(e.message)
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  function runDescriptionSearch(e) {
    e.preventDefault()
    const body = {}
    if (caption.trim()) body.caption_query = caption.trim()
    if (colour) body.colour = colour
    if (vtype) body.vtype = vtype
    if (make) body.make = make
    if (model.trim()) body.model = model.trim()
    if (Object.keys(body).length === 0) return
    run(() => api.searchDescription(body))
  }

  function clearFilters() {
    setCaption('')
    setColour('')
    setVtype('')
    setMake('')
    setModel('')
  }

  const hasDescFilters = caption.trim() || colour || vtype || make || model.trim()
  const sightings = result?.sightings?.slice(0, topK) || []

  return (
    <section className="workspace-page search-page">
      <header className="workspace-header">
        <div>
          <div className="workspace-kicker">01 / Query desk</div>
          <h1>
            Find the vehicle,
            <br />
            keep the evidence.
          </h1>
          <p>
            Search by plate, structured attributes, or natural language description.
            Results show top-K matching vehicles with images and possible routes.
          </p>
        </div>
        <div className="header-aside">
          <strong>Multi-signal search</strong>
          <br />
          attribute + fuzzy text + embedding
        </div>
      </header>

      {/* === Registration Search === */}
      <section className="panel search-mode" style={{ marginBottom: 16 }}>
        <div className="mode-strip" />
        <header>
          <div className="section-kicker">A / Registration</div>
          <h2>Plate-led trace</h2>
          <p className="muted">Use the registration exactly as it was read or reported.</p>
        </header>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            run(() => api.searchRegistration({ registration_no: registration }))
          }}
        >
          <label className="field grow">
            <span className="data-label">Registration number</span>
            <input
              placeholder="e.g. KA 01 AB 1234"
              value={registration}
              onChange={(e) => setRegistration(e.target.value)}
            />
          </label>
          <button className="primary" disabled={busy || !registration.trim()}>
            {busy ? 'Querying' : 'Run trace'}
          </button>
        </form>
      </section>

      {/* === Description Search with Filters === */}
      <section className="panel search-mode" style={{ marginBottom: 16 }}>
        <div className="mode-strip" />
        <header>
          <div className="section-kicker">B / Description + Filters</div>
          <h2>Visual shortlist</h2>
          <p className="muted">
            Combine structured filters with free-text description for powerful multi-signal search.
          </p>
        </header>
        <form onSubmit={runDescriptionSearch} className="search-filters-form">
          <div className="search-filters-grid">
            <label className="field">
              <span className="data-label">Free-text description</span>
              <input
                placeholder="e.g. white SUV with roof rack"
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
              />
            </label>
            <OptionSelect
              label="Colour"
              anyLabel="Any colour"
              options={COLOURS}
              value={colour}
              onChange={(e) => setColour(e.target.value)}
            />
            <OptionSelect
              label="Vehicle type"
              anyLabel="Any type"
              options={VTYPES}
              value={vtype}
              onChange={(e) => setVtype(e.target.value)}
            />
            <OptionSelect
              label="Make"
              anyLabel="Any make"
              options={MAKES}
              value={make}
              onChange={(e) => setMake(e.target.value)}
            />
            <label className="field">
              <span className="data-label">Model</span>
              <input
                placeholder="e.g. swift, creta"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="data-label">Top K results</span>
              <select value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </label>
          </div>
          <div className="search-actions">
            <button type="button" onClick={clearFilters} disabled={!hasDescFilters}>
              Clear
            </button>
            <button className="primary" disabled={busy || !hasDescFilters}>
              {busy ? 'Searching…' : 'Search vehicles'}
            </button>
          </div>
        </form>
      </section>

      {/* === Error === */}
      {error && (
        <section className="panel state-panel">
          <div className="state-inner">
            <div className="state-icon">!</div>
            <h2>Search node did not respond</h2>
            <p>
              Check the query and try again. The search was not completed, so no route result is
              being shown.
            </p>
            <button onClick={() => setError(null)}>Clear message</button>
            <p className="error-detail">{error}</p>
          </div>
        </section>
      )}

      {/* === Results === */}
      {result && (
        <>
          {/* Stats */}
          <div className="workspace-grid stats search-results-summary">
            <section className="panel stat-panel">
              <span className="data-label">Registered matches</span>
              <strong>{result.rarity_count?.toLocaleString() ?? '—'}</strong>
              <span className="stat-copy">matching vehicle records</span>
            </section>
            <section className="panel stat-panel">
              <span className="data-label">Candidate sightings</span>
              <strong>{result.candidate_count ?? '—'}</strong>
              <span className="stat-copy">observations considered</span>
            </section>
            <section className="panel stat-panel">
              <span className="data-label">Possible routes</span>
              <strong>{result.route_count ?? '—'}</strong>
              <span className="stat-copy">physically plausible chains</span>
            </section>
            <section className="panel stat-panel">
              <span className="data-label">Rejected links</span>
              <strong>{result.rejected_leg_count ?? '—'}</strong>
              <span className="stat-copy">discarded on speed evidence</span>
            </section>
          </div>

          {result.rarity_count > 10000 && (
            <p className="admin-note">
              This description is broad. The result is a category, not an identification; inspect
              the route evidence before acting.
            </p>
          )}

          {/* Tab Switcher */}
          <div className="search-tabs" style={{ marginTop: 16 }}>
            <button
              className={`search-tab ${activeTab === 'sightings' ? 'active' : ''}`}
              onClick={() => setActiveTab('sightings')}
            >
              Vehicle Matches ({sightings.length})
            </button>
            <button
              className={`search-tab ${activeTab === 'routes' ? 'active' : ''}`}
              onClick={() => setActiveTab('routes')}
            >
              Route Chains ({result.routes?.length ?? 0})
            </button>
          </div>

          {/* Sightings Tab */}
          {activeTab === 'sightings' && (
            <section style={{ marginTop: 12 }}>
              {sightings.length > 0 ? (
                <div className="search-results-grid">
                  {sightings.map((s) => {
                    // Routes arrive rank-ordered, so the first one holding this
                    // sighting is its best-scored route.
                    const route = result.routes?.find((r) => r.read_ids?.includes(s.read_id))
                    const Card = route ? Link : 'div'
                    return (
                      <Card
                        className="search-result-card"
                        key={s.read_id}
                        to={route ? `/routes/${route.route_id}` : undefined}
                      >
                        <div className="src-image-wrap">
                          <img
                            src={api.mediaUrl(s.crop_ref)}
                            alt="Vehicle crop"
                            className="src-image"
                            loading="lazy"
                          />
                          {s.score > 0 && (
                            <span className="src-score">{(s.score * 100).toFixed(0)}%</span>
                          )}
                        </div>
                        <div className="src-body">
                          <div className="src-title-row">
                            <span className="src-title">
                              {s.plate_text || [s.colour, s.make, s.model].filter(Boolean).join(' ') || s.type || 'Unknown'}
                            </span>
                            {s.plate_text && <span className="signal-badge" style={{ fontSize: '0.6rem', padding: '2px 5px' }}>PLATE</span>}
                          </div>
                          <div className="src-meta">
                            {s.colour && <span className="src-attr">{s.colour}</span>}
                            {s.type && <span className="src-attr">{s.type}</span>}
                            {s.make && <span className="src-attr">{s.make}</span>}
                            {s.model && <span className="src-attr">{s.model}</span>}
                          </div>
                          <div className="src-detail">
                            <span>{s.camera_name || s.camera_id}</span>
                            <span>{s.seen_at ? new Date(s.seen_at).toLocaleString() : ''}</span>
                          </div>
                          {s.matched_on?.length > 0 && (
                            <div className="src-signals">
                              {s.matched_on.map((sig) => (
                                <span key={sig} className={`signal-badge ${sig === 'plate' ? '' : sig === 'attribute' ? '' : 'caution'}`}>
                                  {sig}
                                </span>
                              ))}
                            </div>
                          )}
                          {s.caption && !s.caption.startsWith('[stub]') && (
                            <p className="src-caption">{s.caption}</p>
                          )}
                          <span className="src-route">
                            {route
                              ? `View route #${route.rank} · ${route.read_ids.length} sightings →`
                              : 'Not part of any route chain'}
                          </span>
                        </div>
                      </Card>
                    )
                  })}
                </div>
              ) : (
                <div className="panel state-panel">
                  <div className="state-inner">
                    <div className="state-icon">⌕</div>
                    <h2>No matching vehicles found</h2>
                    <p>Try broadening your search criteria or using a different description.</p>
                  </div>
                </div>
              )}
            </section>
          )}

          {/* Routes Tab */}
          {activeTab === 'routes' && (
            <section className="panel workspace-card" style={{ marginTop: 12 }}>
              <div className="section-heading">
                <div>
                  <div className="section-kicker">Route candidates</div>
                  <h2>Evidence-ranked route chain</h2>
                  <p>
                    Competing route count stays visible beside score so confidence is never mistaken
                    for certainty.
                  </p>
                </div>
                <span className="signal-badge">{result.routes?.length ?? 0} returned</span>
              </div>
              <div className="workspace-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Score</th>
                      <th>Competing</th>
                      <th>Plate anchored</th>
                      <th>Weakest camera</th>
                      <th>Camera chain</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.routes?.map((route) => (
                      <tr key={route.route_id}>
                        <td>#{route.rank}</td>
                        <td>{route.score?.toFixed(3)}</td>
                        <td>{route.competing_count}</td>
                        <td>
                          <span className={`signal-badge ${route.plate_anchored ? '' : 'caution'}`}>
                            {route.plate_anchored ? 'anchored' : 'visual only'}
                          </span>
                        </td>
                        <td>{route.min_trust?.toFixed(2) ?? '—'}</td>
                        <td className="muted">{route.cameras?.join(' → ')}</td>
                        <td>
                          <Link to={`/routes/${route.route_id}`}>Inspect route</Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {result.routes?.length === 0 && (
                <div className="state-panel">
                  <div className="state-inner">
                    <div className="state-icon">⌕</div>
                    <h2>No route chain cleared validation</h2>
                    <p>
                      The candidate sightings did not form a physically possible route for this query.
                    </p>
                  </div>
                </div>
              )}
            </section>
          )}
        </>
      )}

      <WatchlistPanel seed={{ registration_no: registration, colour, vtype, make, model }} />
    </section>
  )
}

function WatchlistPanel({ seed }) {
  const [entries, setEntries] = useState(null)
  const [draft, setDraft] = useState(EMPTY_ENTRY)
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState(null)
  const [removing, setRemoving] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = () =>
    api
      .watchlist()
      .then(setEntries)
      .catch((e) => setError(e.message))
  useEffect(() => {
    load()
  }, [])

  const edit = (key) => (e) => setDraft((d) => ({ ...d, [key]: e.target.value }))
  const hasTarget = WATCH_TARGETS.some((k) => draft[k].trim())
  const seedHasTarget = WATCH_TARGETS.some((k) => seed[k].trim())

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
    <section className="panel search-mode watchlist-panel">
      <div className="mode-strip" />
      <header>
        <div className="section-kicker">C / Watchlist</div>
        <h2>Standing watch</h2>
        <p className="muted">
          Every new detection is checked against active entries. Adding an entry also searches
          recorded history. Give a plate or at least one vehicle attribute.
        </p>
      </header>
      <form onSubmit={add} className="search-filters-form">
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
          <button
            type="button"
            onClick={() => setDraft((d) => ({ ...d, ...seed }))}
            disabled={!seedHasTarget}
          >
            Use search details
          </button>
          <button
            className="primary"
            disabled={adding || !draft.label.trim() || !draft.reason.trim() || !hasTarget}
          >
            {adding ? 'Checking history…' : 'Add to watchlist'}
          </button>
        </div>
      </form>

      {(error || notice) && (
        <p className={`watchlist-message ${error ? 'is-error' : ''}`} role="status">
          {error || notice}
        </p>
      )}

      <div className="watchlist-list">
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
              {entries?.map((w) => (
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
              {entries?.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    No active watchlist entries.
                  </td>
                </tr>
              )}
              {entries === null && !error && (
                <tr>
                  <td colSpan={6} className="muted">
                    Loading watchlist…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
