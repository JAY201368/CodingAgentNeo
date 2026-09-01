import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // Keep development requests on the same /api/v1 wire paths as the
    // production composition launcher. The browser client owns all Agent
    // semantics; Vite only forwards the transport prefix.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
})
