import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'
import { api } from '../api.js'
import VideoPlayer from '../components/VideoPlayer.jsx'
import OnboardCameraModal from '../components/OnboardCameraModal.jsx'

// Gujarat, roughly centred.
const CENTRE = [22.3, 71.5]

// A camera is coloured by what it is actually doing, not by whether it
// answers a ping. Unsurveyed is its own state: those cameras produce nothing,
// and drawing them as working would overstate coverage.
function colourFor(camera) {
  if (!camera.enabled) return '#5a6474'
  if (!camera.surveyed) return '#7a5cc4'
  switch (camera.verdict) {
    case 'ok': return '#4f9d69'
    case 'degraded': return '#d9a441'
    case 'silent': return '#e2554c'
    case 'down': return '#8b2f2a'
    default: return '#8d97a8'
  }
}

export default function MapPage() {
  const [cameras, setCameras] = useState([])
  const [error, setError] = useState(null)
  const [showOnboardModal, setShowOnboardModal] = useState(false)

  const fetchCameras = () => {
    api.cameras().then(setCameras).catch((e) => setError(e.message))
  }

  useEffect(() => {
    fetchCameras()
  }, [])

  const unsurveyed = cameras.filter((c) => !c.surveyed).length

  return (
    <>
      <div className="panel row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <strong>{cameras.length}</strong> cameras
          {unsurveyed > 0 && (
            <span className="warn">
              {unsurveyed} not yet surveyed — they produce nothing until they are
            </span>
          )}
          {error && <span className="warn">{error}</span>}
        </div>
        <button
          onClick={() => setShowOnboardModal(true)}
          style={{
            padding: '6px 14px',
            borderRadius: '6px',
            border: 'none',
            background: 'linear-gradient(135deg, #4f46e5 0%, #3b82f6 100%)',
            color: '#fff',
            fontWeight: 600,
            fontSize: '0.85rem',
            cursor: 'pointer',
            boxShadow: '0 2px 8px rgba(79,70,229,0.3)',
            transition: 'all 0.2s ease'
          }}
        >
          + Onboard Camera
        </button>
      </div>

      <OnboardCameraModal
        isOpen={showOnboardModal}
        onClose={() => setShowOnboardModal(false)}
        onSuccess={fetchCameras}
      />

      <MapContainer center={CENTRE} zoom={7} className="map">
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution="&copy; OpenStreetMap contributors"
        />
        {cameras
          .filter((c) => c.lat && c.lon)
          .map((c) => (
            <CircleMarker
              key={c.camera_id}
              center={[c.lat, c.lon]}
              radius={7}
              pathOptions={{ color: colourFor(c), fillOpacity: 0.85 }}
              eventHandlers={{
                mouseover: (e) => {
                  e.target.openPopup()
                },
              }}
            >
              <Popup minWidth={290}>
                <VideoPlayer cameraId={c.camera_id} title={c.name} />
                <div style={{ marginTop: '8px', fontSize: '12px', color: '#475569' }}>
                  <strong>ID:</strong> {c.camera_id} · <strong>Dept:</strong> {c.department_code} · <strong>Kind:</strong> {c.kind}
                  <br />
                  {c.surveyed ? (
                    <>
                      <strong>Trust:</strong> {c.trust_level} · <strong>Plates:</strong>{' '}
                      {c.plate_viable ? 'yes' : 'no'} · <strong>Density:</strong>{' '}
                      {c.density_viable ? 'yes' : 'no'}
                    </>
                  ) : (
                    <em>not surveyed</em>
                  )}
                  <br />
                  <strong>Health:</strong> {c.verdict || 'unknown'}
                  {c.detections_1h != null && ` · ${c.detections_1h} det/h`}
                </div>
              </Popup>
            </CircleMarker>
          ))}
      </MapContainer>
    </>
  )
}

