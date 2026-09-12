import React, { useEffect, useRef, useState } from 'react'
import Hls from 'hls.js'

export default function VideoPlayer({ cameraId, title }) {
  const videoRef = useRef(null)
  const hlsRef = useRef(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const streamUrl = `/stream/${cameraId}/index.m3u8`

  useEffect(() => {
    let videoEl = videoRef.current
    if (!videoEl || !cameraId) return

    setLoading(true)
    setError(null)

    if (Hls.isSupported()) {
      if (hlsRef.current) {
        hlsRef.current.destroy()
      }

      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        backBufferLength: 30,
      })

      hlsRef.current = hls

      hls.loadSource(streamUrl)
      hls.attachMedia(videoEl)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setLoading(false)
        videoEl.play().catch((err) => {
          console.warn('Autoplay prevented or interrupted:', err)
        })
      })

      hls.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.error('Fatal network error encountered, trying to recover...')
              hls.startLoad()
              break
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.error('Fatal media error encountered, trying to recover...')
              hls.recoverMediaError()
              break
            default:
              console.error('Fatal unrecoverable HLS error:', data)
              setError('Failed to load video stream')
              hls.destroy()
              break
          }
        }
      })
    } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      videoEl.src = streamUrl
      videoEl.addEventListener('loadedmetadata', () => {
        setLoading(false)
        videoEl.play().catch(() => {})
      })
      videoEl.addEventListener('error', () => {
        setError('Video playback error')
        setLoading(false)
      })
    } else {
      setError('HLS is not supported in your browser')
      setLoading(false)
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
    }
  }, [cameraId, streamUrl])

  return (
    <div style={{ width: '280px', fontFamily: 'sans-serif' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '6px',
        }}
      >
        <span style={{ fontWeight: 'bold', fontSize: '13px', color: '#1e293b' }}>
          {title || cameraId}
        </span>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            background: '#ef444420',
            color: '#ef4444',
            padding: '2px 6px',
            borderRadius: '4px',
            fontSize: '11px',
            fontWeight: '600',
          }}
        >
          <span
            style={{
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              backgroundColor: '#ef4444',
              display: 'inline-block',
            }}
          />
          LIVE
        </span>
      </div>

      <div
        style={{
          position: 'relative',
          width: '100%',
          height: '158px',
          backgroundColor: '#0f172a',
          borderRadius: '6px',
          overflow: 'hidden',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {loading && (
          <div
            style={{
              position: 'absolute',
              color: '#94a3b8',
              fontSize: '12px',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <div
              style={{
                width: '18px',
                height: '18px',
                border: '2px solid #38bdf8',
                borderTopColor: 'transparent',
                borderRadius: '50%',
                animation: 'spin 1s linear infinite',
              }}
            />
            Loading feed...
          </div>
        )}

        {error ? (
          <div style={{ color: '#f87171', fontSize: '12px', padding: '8px', textAlign: 'center' }}>
            {error}
          </div>
        ) : (
          <video
            ref={videoRef}
            muted
            playsInline
            controls
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              display: loading ? 'none' : 'block',
            }}
          />
        )}
      </div>

      <style>{`
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
