import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { cpSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // maplibre-gl expects its worker at <bundle>/maplibre-gl-worker.mjs; Vite
    // doesn't copy it, so do it manually post-build (closeBuildStart hook).
    {
      name: 'copy-maplibre-worker',
      closeBundle() {
        const src = resolve(__dirname, 'node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs')
        const destDir = resolve(__dirname, 'dist/assets')
        mkdirSync(destDir, { recursive: true })
        cpSync(src, resolve(destDir, 'maplibre-gl-worker.mjs'))
      },
    },
  ],
})
