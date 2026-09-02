import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      imports: ['vue', 'vue-router', 'pinia'],
    }),
    Components({
      resolvers: [ElementPlusResolver()],
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    host: '0.0.0.0', // WSL 内监听全网卡，Windows 浏览器经 localhostForwarding 可访问
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // 不缓冲SSE响应
        headers: { 'X-Accel-Buffering': 'no' },
      },
    },
  },
  build: {
    target: 'es2015',
    // 阶段四：将大型第三方库拆分为独立 chunk，使业务路由组件不被 vendor 撑大、
    // 并可并行/按需加载；警告线恢复为 500KB（拆分后单个 vendor chunk 若确因库体积
    // 超标属预期，其余异常增长可被该阈值捕获）。
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('echarts') || id.includes('zrender')) return 'vendor-echarts'
            if (id.includes('element-plus') || id.includes('@element-plus')) return 'vendor-element-plus'
            if (id.includes('vue-router') || id.includes('/vue/') || id.includes('@vue/') || id.includes('pinia')) return 'vendor-vue'
            if (id.includes('axios') || id.includes('marked') || id.includes('dompurify')) return 'vendor-utils'
            return 'vendor-misc'
          }
        },
      },
    },
  },
})
