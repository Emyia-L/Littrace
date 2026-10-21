import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 浏览器请求 /api/projects；Vite 将 /api 去掉后转发到 FastAPI 的 /projects。
      // 这个代理只在本地开发时生效，正式部署需另行配置同源 API 网关。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
