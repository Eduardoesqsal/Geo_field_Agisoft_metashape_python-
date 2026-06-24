import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/status': 'http://127.0.0.1:8000',
      '/logs': 'http://127.0.0.1:8000',
      '/overlay': 'http://127.0.0.1:8000',
      '/tiles': 'http://127.0.0.1:8000',
      '/ingesta': 'http://127.0.0.1:8000',
      '/proyecto': 'http://127.0.0.1:8000',
      '/procesar': 'http://127.0.0.1:8000',
      '/start': 'http://127.0.0.1:8000',
      '/stop': 'http://127.0.0.1:8000',
    },
  },
})
