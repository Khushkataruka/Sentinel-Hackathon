import React, { useState, useEffect } from 'react'
import { api } from '../api.js'

export default function OnboardCameraModal({ isOpen, onClose, onSuccess }) {
  const [departments, setDepartments] = useState([])
  const [loadingDepts, setLoadingDepts] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)

  const [form, setForm] = useState({
    cameraId: '',
    name: '',
    departmentId: '',
    kind: 'ip',
    lat: '22.3000',
    lon: '71.5000',
    autoSurvey: true,
  })

  useEffect(() => {
    if (isOpen) {
      setError(null)
      setSuccessMsg(null)
      setLoadingDepts(true)
      api.departments()
        .then((depts) => {
          setDepartments(depts)
          if (depts.length > 0 && !form.departmentId) {
            setForm((f) => ({ ...f, departmentId: depts[0].id }))
          }
        })
        .catch((err) => setError('Failed to load departments: ' + err.message))
        .finally(() => setLoadingDepts(false))
    }
  }, [isOpen])

  if (!isOpen) return null

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target
    setForm((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setSuccessMsg(null)

    if (!form.cameraId.trim()) {
      setError('Camera ID is required.')
      return
    }
    if (!form.name.trim()) {
      setError('Camera Name is required.')
      return
    }
    if (!form.departmentId) {
      setError('Please select a department.')
      return
    }

    const lat = parseFloat(form.lat)
    const lon = parseFloat(form.lon)
    if (isNaN(lat) || lat < -90 || lat > 90) {
      setError('Latitude must be a number between -90 and 90.')
      return
    }
    if (isNaN(lon) || lon < -180 || lon > 180) {
      setError('Longitude must be a number between -180 and 180.')
      return
    }

    setSubmitting(true)

    try {
      const cameraPayload = {
        camera_id: form.cameraId.trim(),
        department_id: parseInt(form.departmentId, 10),
        name: form.name.trim(),
        kind: form.kind,
        lat: lat,
        lon: lon,
        enabled: true,
      }

      await api.onboardCamera(form.cameraId.trim(), cameraPayload)

      if (form.autoSurvey) {
        const surveyPayload = {
          resolution_class: 'full',
          permitted_attributes: ['colour', 'make', 'type'],
          permitted_violations: [],
          plate_viable: true,
          density_viable: false,
          trust_level: 0.9,
          decode_fps: 10.0,
        }
        await api.surveyCamera(form.cameraId.trim(), surveyPayload)
      }

      setSuccessMsg(`Camera ${form.cameraId} onboarded successfully!`)
      setTimeout(() => {
        if (onSuccess) onSuccess(cameraPayload)
        onClose()
      }, 1200)
    } catch (err) {
      setError(err.message || 'Failed to onboard camera')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(15, 23, 42, 0.75)',
        backdropFilter: 'blur(4px)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
      }}
    >
      <div
        style={{
          backgroundColor: '#1e293b',
          border: '1px solid #334155',
          borderRadius: '10px',
          width: '100%',
          maxWidth: '480px',
          padding: '24px',
          boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)',
          color: '#f8fafc',
          fontFamily: 'sans-serif',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3 style={{ margin: 0, fontSize: '18px', color: '#38bdf8' }}>📷 Onboard New Camera</h3>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: '#94a3b8',
              fontSize: '20px',
              cursor: 'pointer',
            }}
          >
            &times;
          </button>
        </div>

        {error && (
          <div style={{ padding: '10px', backgroundColor: '#7f1d1d', color: '#fca5a5', borderRadius: '6px', marginBottom: '14px', fontSize: '13px' }}>
            {error}
          </div>
        )}

        {successMsg && (
          <div style={{ padding: '10px', backgroundColor: '#14532d', color: '#86efac', borderRadius: '6px', marginBottom: '14px', fontSize: '13px' }}>
            {successMsg}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#94a3b8', marginBottom: '4px' }}>
              Camera ID *
            </label>
            <input
              type="text"
              name="cameraId"
              placeholder="e.g. cam30 or rajkot_north_01"
              value={form.cameraId}
              onChange={handleChange}
              required
              style={inputStyle}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#94a3b8', marginBottom: '4px' }}>
              Camera Name *
            </label>
            <input
              type="text"
              name="name"
              placeholder="e.g. Rajkot Ring Road Junction"
              value={form.name}
              onChange={handleChange}
              required
              style={inputStyle}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#94a3b8', marginBottom: '4px' }}>
                Department *
              </label>
              <select
                name="departmentId"
                value={form.departmentId}
                onChange={handleChange}
                disabled={loadingDepts}
                style={inputStyle}
              >
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.code} - {d.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#94a3b8', marginBottom: '4px' }}>
                Camera Kind
              </label>
              <select name="kind" value={form.kind} onChange={handleChange} style={inputStyle}>
                <option value="ip">IP Camera</option>
                <option value="ptz">PTZ Camera</option>
                <option value="anpr">ANPR Dedicated</option>
                <option value="body">Body Cam</option>
                <option value="dash">Dash Cam</option>
              </select>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#94a3b8', marginBottom: '4px' }}>
                Latitude *
              </label>
              <input
                type="number"
                step="any"
                name="lat"
                value={form.lat}
                onChange={handleChange}
                required
                style={inputStyle}
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#94a3b8', marginBottom: '4px' }}>
                Longitude *
              </label>
              <input
                type="number"
                step="any"
                name="lon"
                value={form.lon}
                onChange={handleChange}
                required
                style={inputStyle}
              />
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '6px' }}>
            <input
              type="checkbox"
              id="autoSurvey"
              name="autoSurvey"
              checked={form.autoSurvey}
              onChange={handleChange}
              style={{ cursor: 'pointer' }}
            />
            <label htmlFor="autoSurvey" style={{ fontSize: '13px', color: '#cbd5e1', cursor: 'pointer' }}>
              Auto-survey camera (enables live detections on map)
            </label>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '16px' }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '8px 16px',
                borderRadius: '6px',
                border: '1px solid #475569',
                backgroundColor: 'transparent',
                color: '#cbd5e1',
                fontSize: '13px',
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              style={{
                padding: '8px 18px',
                borderRadius: '6px',
                border: 'none',
                backgroundColor: '#0284c7',
                color: '#ffffff',
                fontWeight: 600,
                fontSize: '13px',
                cursor: submitting ? 'wait' : 'pointer',
              }}
            >
              {submitting ? 'Onboarding...' : 'Onboard Camera'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

const inputStyle = {
  width: '100%',
  padding: '8px 12px',
  borderRadius: '6px',
  border: '1px solid #475569',
  backgroundColor: '#0f172a',
  color: '#f8fafc',
  fontSize: '13px',
  boxSizing: 'border-box',
}
