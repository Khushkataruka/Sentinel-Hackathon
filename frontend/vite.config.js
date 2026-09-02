import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API serves this build in production; in dev it proxies so there is no
// CORS story to get wrong.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/search': 'http://localhost:8001',
      '/routes': 'http://localhost:8001',
      '/searches': 'http://localhost:8001',
      '/alerts': 'http://localhost:8001',
      '/violations': 'http://localhost:8001',
      '/traffic': 'http://localhost:8001',
      '/evidence': 'http://localhost:8001',
      '/frames': 'http://localhost:8001',
      '/media': 'http://localhost:8001',
      '/watchlist': 'http://localhost:8001',
      '/map': 'http://localhost:8001',
      '/audit': 'http://localhost:8001',
      '/config': 'http://localhost:8001',
      '/cameras': 'http://localhost:8000',
      '/departments': 'http://localhost:8000',
      '/adapters': 'http://localhost:8000',
      '/queues': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/import': 'http://localhost:8000',
      '/sync': 'http://localhost:8000'
    }
  }
})
