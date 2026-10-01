import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so web/dist works from any folder.
  base: './',
  // The API answers browser calls only from port 5173 (eww/config.py SERVE_CORS_ORIGINS), so the built map runs
  // through `npm run preview` there.
  server: { port: 5173, strictPort: true },
  preview: { port: 5173, strictPort: true },
  // maplibre-gl alone is about 1 MB minified (about 280 KB gzipped) and loads lazily in its own chunk.
  build: { chunkSizeWarningLimit: 1200 },
})
