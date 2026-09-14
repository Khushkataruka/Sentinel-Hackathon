import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import './operator-components.css'

const INITIAL_FORM = {
  cameraId: '',
  name: '',
  departmentId: '',
  kind: 'ip',
  lat: '23.0225',
  lon: '72.5714',
  autoSurvey: true,
}

export default function OnboardCameraModal({ isOpen, onClose, onSuccess }) {
  const dialogRef = useRef(null)
  const [departments, setDepartments] = useState([])
  const [loadingDepts, setLoadingDepts] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)
  const [form, setForm] = useState(INITIAL_FORM)

  useEffect(() => {
    if (!isOpen) return
    let active = true
    const dialog = dialogRef.current
    dialog.showModal()
    setError(null)
    setSuccess(null)
    setForm(INITIAL_FORM)
    setLoadingDepts(true)
    setDepartments([])
    api
      .departments()
      .then((items) => {
        if (!active) return
        setDepartments(items)
        setForm((current) => ({
          ...current,
          departmentId: items[0]?.id ?? '',
        }))
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoadingDepts(false))
    return () => {
      active = false
      dialog.close()
    }
  }, [isOpen])

  if (!isOpen) return null

  function change(event) {
    const { name, type, checked, value } = event.target
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  async function submit(event) {
    event.preventDefault()
    setError(null)
    const lat = Number(form.lat)
    const lon = Number(form.lon)
    if (!form.cameraId.trim() || !form.name.trim() || !form.departmentId) {
      setError('Enter a camera ID, name, and department to continue.')
      return
    }
    if (
      !form.lat.trim() ||
      !form.lon.trim() ||
      !Number.isFinite(lat) ||
      !Number.isFinite(lon) ||
      Math.abs(lat) > 90 ||
      Math.abs(lon) > 180
    ) {
      setError('Enter a valid latitude (−90 to 90) and longitude (−180 to 180).')
      return
    }
    setSubmitting(true)
    const cameraId = form.cameraId.trim()
    const payload = {
      camera_id: cameraId,
      department_id: Number(form.departmentId),
      name: form.name.trim(),
      kind: form.kind,
      lat,
      lon,
      enabled: true,
    }
    let registered = false
    try {
      await api.onboardCamera(cameraId, payload)
      registered = true
      if (form.autoSurvey) {
        await api.surveyCamera(cameraId, {
          resolution_class: 'full',
          permitted_attributes: ['colour', 'make', 'type'],
          permitted_violations: [],
          plate_viable: true,
          density_viable: false,
          trust_level: 0.9,
          decode_fps: 10,
        })
      }
      setSuccess({ name: payload.name, partial: false })
      onSuccess?.(payload)
    } catch (err) {
      if (registered) {
        setSuccess({ name: payload.name, partial: true })
        setError(`Camera registered, but its survey profile could not be saved. ${err.message}`)
        onSuccess?.(payload)
      } else setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="onboard-dialog"
      aria-labelledby="onboard-title"
      onCancel={(event) => {
        event.preventDefault()
        if (!submitting) onClose()
      }}
      onClick={(event) => {
        if (event.target === dialogRef.current && !submitting) {
          const rect = dialogRef.current.getBoundingClientRect()
          if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          )
            onClose()
        }
      }}
    >
      <div className="onboard-accent" />
      <header className="onboard-header">
        <div>
          <span className="operator-eyebrow">NETWORK EXPANSION / 01</span>
          <h2 id="onboard-title">Bring a new camera online.</h2>
          <p>Connect another point of view to your city.</p>
        </div>
        <button
          type="button"
          className="operator-close"
          onClick={onClose}
          disabled={submitting}
          aria-label="Close onboarding"
        >
          ×
        </button>
      </header>
      {error && (
        <div className="operator-notice is-error" role="alert">
          {error}
        </div>
      )}
      {success ? (
        <div className="onboard-success" role="status">
          <div className="onboard-success-mark">✓</div>
          <h3>{success.partial ? 'Camera registered' : 'Connection configured'}</h3>
          <p>
            {success.name} has been added to your network.
            {success.partial && ' Complete its survey from Administration.'}
          </p>
          <button className="primary" onClick={onClose}>
            Return to workspace <span aria-hidden="true">↗</span>
          </button>
        </div>
      ) : (
        <form onSubmit={submit} className="onboard-form">
          <fieldset disabled={submitting}>
            <div className="onboard-field">
              <label htmlFor="onboard-camera-id">
                Camera ID <span>Required</span>
              </label>
              <input
                autoFocus
                id="onboard-camera-id"
                name="cameraId"
                placeholder="e.g. AMD-JCT-034"
                value={form.cameraId}
                onChange={change}
                required
              />
            </div>
            <div className="onboard-field">
              <label htmlFor="onboard-camera-name">
                Location name <span>Required</span>
              </label>
              <input
                id="onboard-camera-name"
                name="name"
                placeholder="e.g. Ashram Road Junction"
                value={form.name}
                onChange={change}
                required
              />
            </div>
            <div className="onboard-columns">
              <div className="onboard-field">
                <label htmlFor="onboard-department">Department</label>
                <select
                  id="onboard-department"
                  name="departmentId"
                  value={form.departmentId}
                  onChange={change}
                  disabled={loadingDepts || !departments.length}
                  required
                >
                  <option value="" disabled>
                    {loadingDepts ? 'Loading departments…' : 'Select department'}
                  </option>
                  {departments.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.code} · {item.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="onboard-field">
                <label htmlFor="onboard-kind">Camera type</label>
                <select id="onboard-kind" name="kind" value={form.kind} onChange={change}>
                  <option value="ip">IP camera</option>
                  <option value="analog">Analog camera</option>
                </select>
              </div>
            </div>
            <div className="onboard-columns">
              <div className="onboard-field">
                <label htmlFor="onboard-lat">Latitude</label>
                <input
                  id="onboard-lat"
                  type="number"
                  step="any"
                  min="-90"
                  max="90"
                  name="lat"
                  value={form.lat}
                  onChange={change}
                  required
                />
              </div>
              <div className="onboard-field">
                <label htmlFor="onboard-lon">Longitude</label>
                <input
                  id="onboard-lon"
                  type="number"
                  step="any"
                  min="-180"
                  max="180"
                  name="lon"
                  value={form.lon}
                  onChange={change}
                  required
                />
              </div>
            </div>
            <label className="onboard-survey">
              <input
                type="checkbox"
                name="autoSurvey"
                checked={form.autoSurvey}
                onChange={change}
              />
              <span>
                <strong>Apply provisional survey</strong>
                <small>
                  Enable vehicle attributes and plate processing with a default profile.
                </small>
              </span>
            </label>
          </fieldset>
          <footer className="onboard-actions">
            <button type="button" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              className="primary"
              type="submit"
              disabled={submitting || loadingDepts || !departments.length}
            >
              {submitting ? 'Connecting…' : 'Onboard camera'} <span aria-hidden="true">↗</span>
            </button>
          </footer>
        </form>
      )}
    </dialog>
  )
}
