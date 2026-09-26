import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// viewer はリポジトリルート直下の data/ を別プロセス(python http.server)から読む。
// 開発時は vite proxy 経由で同一オリジン化し CORS を回避する。
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    proxy: {
      '/data': {
        target: 'http://localhost:8000',
      },
    },
  },
})
