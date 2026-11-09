import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // 构建产物直接落到后端托管目录，生产只需一个进程
    outDir: '../backend/aitp/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['vue', 'vue-router', 'pinia'],
          element: ['element-plus', '@element-plus/icons-vue'],
          charts: ['echarts'],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    // 默认端口与后端地址不变；并行验收需要独立端口 + 独立临时库时靠这两个环境变量错开
    port: Number(process.env.AITP_DEV_PORT) || 5180,
    proxy: {
      '/api': {
        target: process.env.AITP_PROXY_TARGET || 'http://127.0.0.1:8760',
        changeOrigin: false,
      },
    },
  },
})
