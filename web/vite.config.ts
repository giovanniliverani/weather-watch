import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so the build works whether `eww serve` or another server hosts web/dist.
  base: './',
  // The API allows browser calls only from port 5173.
  server: { port: 5173, strictPort: true },
  preview: { port: 5173, strictPort: true },
  // maplibre-gl alone is about 1 MB minified (about 280 KB gzipped) and loads lazily in its own chunk.
  build: { chunkSizeWarningLimit: 1200 },
})
