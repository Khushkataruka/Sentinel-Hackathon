import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'
import { api } from '../api.js'

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

  useEffect(() => {
    api.cameras().then(setCameras).catch((e) => setError(e.message))
  }, [])

  const unsurveyed = cameras.filter((c) => !c.surveyed).length

  return (
    <>
      <div className="panel row">
        <strong>{cameras.length}</strong> cameras
        {unsurveyed > 0 && (
          <span className="warn">
            {unsurveyed} not yet surveyed — they produce nothing until they are
          </span>
        )}
        {error && <span className="warn">{error}</span>}
      </div>

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
              radius={6}
              pathOptions={{ color: colourFor(c), fillOpacity: 0.8 }}
            >
              <Popup>
                <strong>{c.name}</strong>
                <br />
                {c.camera_id} · {c.department_code} · {c.kind}
                <br />
                {c.surveyed ? (
                  <>
                    trust {c.trust_level} · plates{' '}
                    {c.plate_viable ? 'yes' : 'no'} · density{' '}
                    {c.density_viable ? 'yes' : 'no'}
                  </>
                ) : (
                  <em>not surveyed</em>
                )}
                <br />
                health: {c.verdict || 'unknown'}
                {c.detections_1h != null && ` · ${c.detections_1h} detections/h`}
              </Popup>
            </CircleMarker>
          ))}
      </MapContainer>
    </>
  )
}
