import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

const messages = {
  waiting: 'Waiting for camera processing to start…',
  stalled: 'Camera processing has stopped. Reconnecting…',
  gap: 'Waiting for the next buffered frame…',
}

export default function AnnotatedFeed({ cameraId, title }) {
  const imageRef = useRef(null)
  const containerRef = useRef(null)
  const [feed, setFeed] = useState({ state: 'connecting' })
  const [expanded, setExpanded] = useState(false)
  const [expandError, setExpandError] = useState(null)

  useEffect(() => {
    const changed = () => setExpanded(document.fullscreenElement === containerRef.current)
    document.addEventListener('fullscreenchange', changed)
    return () => document.removeEventListener('fullscreenchange', changed)
  }, [])

  const toggleFullscreen = async () => {
    try {
      if (expanded) await document.exitFullscreen()
      else await containerRef.current.requestFullscreen()
      setExpandError(null)
    } catch {
      setExpandError('Full-screen playback is unavailable in this browser.')
    }
  }

  useEffect(() => {
    let active = true
    let timer
    let controller
    let displayedUrl
    const urls = new Set()
    setFeed({ state: 'connecting' })

    const poll = async () => {
      const started = performance.now()
      controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 10000)
      let interval = 1000
      try {
        const next = await api.annotatedFrame(cameraId, controller.signal)
        if (!active) return
        if (next.image) {
          const url = URL.createObjectURL(next.image)
          urls.add(url)
          // Decode before swapping so frames never flash blank between requests.
          const preload = new Image()
          preload.src = url
          await preload.decode()
          if (!active) return
          imageRef.current.src = url
          if (displayedUrl) {
            URL.revokeObjectURL(displayedUrl)
            urls.delete(displayedUrl)
          }
          displayedUrl = url
          interval = Math.max(0, 1000 / (next.fps || 5) - (performance.now() - started))
        }
        const { image: _image, ...status } = next
        setFeed(status)
      } catch (error) {
        if (!active) return
        for (const url of urls) {
          if (url !== displayedUrl) {
            URL.revokeObjectURL(url)
            urls.delete(url)
          }
        }
        setFeed({
          state: 'error',
          message:
            error.name === 'AbortError'
              ? 'The annotated feed is taking too long. Reconnecting…'
              : error.message,
        })
        interval = 2000
      } finally {
        clearTimeout(timeout)
        if (active) timer = setTimeout(poll, interval)
      }
    }
    poll()
    return () => {
      active = false
      clearTimeout(timer)
      controller?.abort()
      for (const url of urls) URL.revokeObjectURL(url)
    }
  }, [cameraId])

  const playing = feed.state === 'playing'
  const message =
    feed.message ||
    (feed.state === 'buffering'
      ? `Preparing annotated playback · ${Math.ceil(feed.wait_s)}s remaining`
      : messages[feed.state] || 'Connecting to annotated playback…')

  return (
    <div className="annotated-feed" ref={containerRef}>
      <div className="player-stage">
        <img
          ref={imageRef}
          className="annotated-feed-image"
          alt={`${title || cameraId} annotated camera feed`}
          hidden={!playing}
        />
        {!playing && (
          <div className="player-overlay" role="status">
            <div className="player-spinner" aria-hidden="true" />
            <p>{message}</p>
          </div>
        )}
      </div>
      <div className="annotated-feed-details" role="status" aria-live="off">
        <span>{playing ? `${Math.round(feed.delay_s)}s delayed` : 'Delayed playback'}</span>
        {playing && feed.seen_at && (
          <time dateTime={feed.seen_at}>
            Captured {new Date(feed.seen_at).toLocaleTimeString()}
          </time>
        )}
        <button type="button" className="player-expand" onClick={toggleFullscreen}>
          {expanded ? 'Exit full screen' : 'Expand feed'}
        </button>
      </div>
      {playing && feed.pending > 0 && (
        <p className="annotated-feed-note">Analysis pending for {feed.pending} vehicle(s).</p>
      )}
      {playing && feed.model_note && (
        <p className="annotated-feed-note is-warning">{feed.model_note}</p>
      )}
      {expandError && <p className="annotated-feed-note">{expandError}</p>}
    </div>
  )
}
