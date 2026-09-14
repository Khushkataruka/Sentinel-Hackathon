import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function serviceProxy(target) {
  return {
    target,
    bypass(req) {
      // UI routes and service routes share prefixes. Browser navigation needs the SPA.
      if (req.method === 'GET' && req.headers.accept?.includes('text/html')) {
        return '/index.html'
      }
    },
  }
}

// The API serves this build in production; in dev it proxies so there is no
// CORS story to get wrong.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/search': serviceProxy('http://127.0.0.1:8001'),
      '/routes': serviceProxy('http://127.0.0.1:8001'),
      '/searches': serviceProxy('http://127.0.0.1:8001'),
      '/alerts': serviceProxy('http://127.0.0.1:8001'),
      '/violations': serviceProxy('http://127.0.0.1:8001'),
      '/traffic': serviceProxy('http://127.0.0.1:8001'),
      '/evidence': serviceProxy('http://127.0.0.1:8001'),
      '/frames': serviceProxy('http://127.0.0.1:8001'),
      '/media': serviceProxy('http://127.0.0.1:8001'),
      '/watchlist': serviceProxy('http://127.0.0.1:8001'),
      '/map': serviceProxy('http://127.0.0.1:8001'),
      '/audit': serviceProxy('http://127.0.0.1:8001'),
      '/config': serviceProxy('http://127.0.0.1:8001'),
      '/cameras': serviceProxy('http://127.0.0.1:8000'),
      '/departments': serviceProxy('http://127.0.0.1:8000'),
      '/adapters': serviceProxy('http://127.0.0.1:8000'),
      '/queues': serviceProxy('http://127.0.0.1:8000'),
      '/health': serviceProxy('http://127.0.0.1:8000'),
      '/import': serviceProxy('http://127.0.0.1:8000'),
      '/sync': serviceProxy('http://127.0.0.1:8000'),
      '/stream': serviceProxy('http://127.0.0.1:8000'),
      '/live_sightings': serviceProxy('http://127.0.0.1:8001'),
      '/annotated': serviceProxy('http://127.0.0.1:8001'),
    },
  },
})
