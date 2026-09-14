import { useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'
import AnnotatedFeed from './AnnotatedFeed'
import './operator-components.css'

export default function VideoPlayer({ cameraId, title }) {
  const videoRef = useRef(null)
  const [status, setStatus] = useState('loading')
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [mode, setMode] = useState('raw')

  useEffect(() => {
    const video = videoRef.current
    if (mode !== 'raw' || !video || !cameraId) return
    let active = true
    let hls
    let recovered = false
    setStatus('loading')
    setError(null)
    const streamUrl = `/stream/${encodeURIComponent(cameraId)}/index.m3u8`
    const fail = (message) => {
      if (!active) return
      clearTimeout(timeout)
      setError(message)
      setStatus('error')
      hls?.destroy()
    }
    const ready = () => {
      if (!active) return
      clearTimeout(timeout)
      setStatus('ready')
      setError(null)
      video.play().catch(() => {})
    }
    const playing = () => {
      if (active) setStatus('playing')
    }
    const paused = () => {
      if (active) setStatus('paused')
    }
    const playbackError = () => fail('This camera feed is unavailable.')
    const timeout = setTimeout(() => fail('The camera did not respond. Try reconnecting.'), 20000)
    video.addEventListener('canplay', ready)
    video.addEventListener('playing', playing)
    video.addEventListener('pause', paused)
    video.addEventListener('error', playbackError)

    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        backBufferLength: 30,
      })
      hls.loadSource(streamUrl)
      hls.attachMedia(video)
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && !recovered) {
          recovered = true
          hls.recoverMediaError()
        } else fail('This camera feed is unavailable.')
      })
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = streamUrl
    } else fail('Streaming is not supported by this browser.')

    return () => {
      active = false
      clearTimeout(timeout)
      video.removeEventListener('canplay', ready)
      video.removeEventListener('playing', playing)
      video.removeEventListener('pause', paused)
      video.removeEventListener('error', playbackError)
      hls?.destroy()
      video.removeAttribute('src')
      video.load()
    }
  }, [cameraId, attempt, mode])

  const label = {
    loading: 'CONNECTING',
    error: 'UNAVAILABLE',
    ready: 'READY',
    playing: 'PLAYING',
    paused: 'PAUSED',
  }[status]
  return (
    <div className="sentinel-player">
      <div className="player-header">
        <strong>{title || cameraId}</strong>
        <span
          className={`player-status ${mode === 'raw' && status === 'playing' ? 'is-live' : ''}`}
        >
          <i aria-hidden="true" />
          {mode === 'annotated' ? 'ANNOTATED' : label}
        </span>
      </div>
      <div className="player-mode-switch" aria-label="Camera playback mode">
        <button type="button" aria-pressed={mode === 'raw'} onClick={() => setMode('raw')}>
          Live
        </button>
        <button
          type="button"
          aria-pressed={mode === 'annotated'}
          onClick={() => setMode('annotated')}
        >
          Annotated · ~1 min delay
        </button>
      </div>
      {mode === 'annotated' ? (
        <AnnotatedFeed key={cameraId} cameraId={cameraId} title={title} />
      ) : (
        <div className="player-stage">
          <video
            ref={videoRef}
            muted
            playsInline
            controls
            aria-label={`${title || cameraId} camera feed`}
          />
          {status === 'loading' && (
            <div className="player-overlay" role="status">
              <div className="player-spinner" aria-hidden="true" />
              <p>Establishing video connection</p>
            </div>
          )}
          {error && (
            <div className="player-overlay" role="status">
              <svg
                width="25"
                height="25"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                aria-hidden="true"
              >
                <path d="M4 7h11v10H4zM15 10l5-3v10l-5-3M3 3l18 18" />
              </svg>
              <p>{error}</p>
              <button type="button" onClick={() => setAttempt((value) => value + 1)}>
                Reconnect feed ↻
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
