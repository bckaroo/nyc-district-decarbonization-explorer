import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { cpSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

// https://vite.dev/config/
// `base` must match the deployment path or every asset 404s: GitHub Pages serves
// this app from /<repo>/, while the tailnet server serves it from /. Set
// VITE_BASE=/nyc-district-decarbonization-explorer/ for the Pages build.
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [
    react(),
    // maplibre-gl expects its worker at <bundle>/maplibre-gl-worker.mjs; Vite
    // doesn't copy it, so do it manually post-build (closeBuildStart hook).
    {
      name: 'copy-maplibre-worker',
      closeBundle() {
        const destDir = resolve(__dirname, 'dist/assets')
        mkdirSync(destDir, { recursive: true })
        // Worker entry alone is not enough: it statically imports the shared
        // chunk, which must sit next to it or the worker fails to load (a
        // 404-html fallthrough) and no tiles/polygons ever parse.
        for (const f of ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']) {
          const src = resolve(__dirname, 'node_modules/maplibre-gl/dist', f)
          cpSync(src, resolve(destDir, f))
        }
      },
    },
  ],
})
