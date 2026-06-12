import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    proxy: {
      '/find': 'http://localhost:8001',
      '/healthz': 'http://localhost:8001',
    },
  },
})
