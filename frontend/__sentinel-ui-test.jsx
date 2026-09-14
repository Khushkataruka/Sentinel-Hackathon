import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import App from './src/App.jsx'
import { api } from './src/api.js'
import './src/styles.css'
import 'leaflet/dist/leaflet.css'
const fixtures = [
  {
    id: 901,
    read_id: 'fixture-01',
    camera_id: 'TEST-CAM-01',
    violation_type: 'no_helmet',
    label: 'Riding without helmet',
    confidence: 0.92,
    trust_level: 0.7,
    seen_at: '2026-09-14T10:22:00Z',
    crop_ref: 'test',
    evidence_ref: null,
    review_status: 'pending_review',
    colour: 'Black',
    make: 'Test vehicle',
    class: 'motorcycle',
  },
  {
    id: 902,
    read_id: 'fixture-02',
    camera_id: 'TEST-CAM-02',
    violation_type: 'phone_use',
    label: 'Mobile phone use',
    confidence: 0.68,
    trust_level: 0.5,
    seen_at: '2026-09-14T10:20:00Z',
    crop_ref: 'test',
    review_status: 'pending_review',
    colour: 'White',
    class: 'car',
    needs_glass_penetration: true,
  },
  {
    id: 903,
    read_id: 'fixture-03',
    camera_id: 'TEST-CAM-03',
    violation_type: 'red_light',
    label: 'Signal violation',
    confidence: 0.83,
    trust_level: 0.8,
    seen_at: '2026-09-14T10:18:00Z',
    crop_ref: null,
    review_status: 'pending_review',
    class: 'car',
  },
]
api.violations = async (query) => {
  const params = new URLSearchParams(query)
  return fixtures.filter(
    (row) =>
      row.review_status === (params.get('review_status') || 'pending_review') &&
      (!params.get('violation_type') || row.violation_type === params.get('violation_type')),
  )
}
api.violationTypes = async () => [
  { code: 'no_helmet', label: 'Riding without helmet' },
  { code: 'phone_use', label: 'Mobile phone use' },
  { code: 'red_light', label: 'Signal violation' },
]
api.reviewViolation = async (id, body) => {
  fixtures.find((row) => row.id === id).review_status = body.review_status
  return { id, ...body }
}
api.mediaUrl = (ref) => (ref ? '/sentinel.svg' : undefined)
api.sightings = async () =>
  fixtures.map((row) => ({ ...row, camera_name: row.camera_id, type: row.class }))
api.departments = async () => [{ id: 1, code: 'TEST', name: 'Local fixture' }]
createRoot(document.getElementById('root')).render(
  <MemoryRouter initialEntries={['/violations']}>
    <div
      style={{
        position: 'fixed',
        bottom: 8,
        left: 10,
        zIndex: 999,
        fontSize: 9,
        color: '#e8b961',
        background: '#19130d',
        padding: 6,
      }}
    >
      LOCAL UI TEST · SYNTHETIC FIXTURES · NO BACKEND WRITES
    </div>
    <App />
  </MemoryRouter>,
)
