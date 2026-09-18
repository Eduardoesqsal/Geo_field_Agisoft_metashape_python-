import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendPort = Number(process.env.PORT || 8001)
const backendBase = `http://127.0.0.1:${backendPort}`

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/status': backendBase,
      '/logs': backendBase,
      '/overlay': backendBase,
      '/tiles': backendBase,
      '/ingesta': backendBase,
      '/proyecto': backendBase,
      '/procesar': backendBase,
      '/start': backendBase,
      '/stop': backendBase,
    },
  },
})
