import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // 前端构建产物由 FastAPI 挂载在 /app 下（exe 壳加载 http://127.0.0.1:8901/app/）
  // 必须设置 base，否则产物内引用 /assets/... 绝对路径导致挂载点下白屏
  base: '/app/',
  server: {
    port: 5173,
    proxy: {
      '/wb': 'http://127.0.0.1:8901',
      '/api': 'http://127.0.0.1:8901',
      '/static': 'http://127.0.0.1:8901'
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500
  }
})
