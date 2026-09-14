import { useEffect, useRef, useState } from 'react'
import Icon from './Icon.jsx'
import './evidence-image.css'

export default function EvidenceImage({ src, alt, caption, className = '', badge }) {
  const [open, setOpen] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [failed, setFailed] = useState(false)
  const dialogRef = useRef(null)
  useEffect(() => setFailed(false), [src])
  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    dialog.showModal()
    setZoom(1)
    return () => dialog.close()
  }, [open])

  const changeZoom = (delta) =>
    setZoom((value) => Math.min(4, Math.max(1, +(value + delta).toFixed(2))))
  return (
    <>
      <button
        type="button"
        className={`evidence-thumbnail ${className}`}
        aria-label={`Expand image: ${alt}`}
        disabled={!src || failed}
        onClick={() => setOpen(true)}
      >
        {src && !failed ? (
          <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
        ) : (
          <span className="evidence-missing">
            <Icon name="image" size={25} />
            <span>{failed ? 'Image unavailable' : 'No image attached'}</span>
          </span>
        )}
        {badge && <span className="evidence-image-badge">{badge}</span>}
        {src && !failed && (
          <span className="evidence-expand">
            <Icon name="expand" size={14} />
            <span>Inspect image</span>
          </span>
        )}
      </button>
      {open && (
        <dialog
          className="evidence-lightbox"
          ref={dialogRef}
          aria-label={`Expanded evidence: ${alt}`}
          onCancel={(event) => {
            event.preventDefault()
            setOpen(false)
          }}
          onKeyDown={(event) => {
            if (event.key === '+' || event.key === '=') {
              event.preventDefault()
              changeZoom(0.5)
            }
            if (event.key === '-') {
              event.preventDefault()
              changeZoom(-0.5)
            }
            if (event.key === '0') {
              event.preventDefault()
              setZoom(1)
            }
          }}
        >
          <header className="evidence-viewer-header">
            <div>
              <span>VISUAL EVIDENCE / INSPECTOR</span>
              <h2>{alt}</h2>
            </div>
            <button
              type="button"
              aria-label="Close expanded image"
              onClick={() => setOpen(false)}
              autoFocus
            >
              <Icon name="close" size={21} />
            </button>
          </header>
          <div className={`evidence-viewport ${zoom > 1 ? 'is-zoomed' : ''}`}>
            <div
              className="evidence-canvas"
              style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}
            >
              <img src={src} alt={alt} />
            </div>
          </div>
          <footer className="evidence-viewer-footer">
            <div>
              <strong>{caption || 'Original captured image'}</strong>
              <span>
                {zoom > 1
                  ? 'Scroll to pan · 0 to fit · Esc to close'
                  : 'Use + / − to zoom · Esc to close'}
              </span>
            </div>
            <div className="evidence-zoom">
              <button
                type="button"
                aria-label="Zoom out image"
                disabled={zoom <= 1}
                onClick={() => changeZoom(-0.5)}
              >
                <Icon name="minus" size={17} />
              </button>
              <output aria-live="polite">{Math.round(zoom * 100)}%</output>
              <button
                type="button"
                aria-label="Zoom in image"
                disabled={zoom >= 4}
                onClick={() => changeZoom(0.5)}
              >
                <Icon name="plus" size={17} />
              </button>
              <button type="button" onClick={() => setZoom(1)}>
                Fit
              </button>
            </div>
          </footer>
        </dialog>
      )}
    </>
  )
}
