import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const target = env.VITE_API_BASE_URL?.trim() || 'http://127.0.0.1:8000'
  const simulationEnv = loadEnv(mode, process.cwd(), 'SIMULATION_')
  const simulationTarget = simulationEnv.SIMULATION_API_URL?.trim() || 'http://127.0.0.1:8087'

  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
      proxy: {
        '/__simulation/clock': {
          target: simulationTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/__simulation\/clock/, '/simulation/clock'),
        },
        '/__simulation/settings': {
          target: simulationTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/__simulation\/settings/, '/simulation/settings'),
        },
        '/__simulation/spawn-orders': {
          target: simulationTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/__simulation\/spawn-orders/, '/simulation/spawn-orders'),
        },
        '/api': { target, changeOrigin: true, ws: true },
        '/orders/': { target, changeOrigin: true },
        '/health': { target, changeOrigin: true },
        '/fleet/capacity': { target, changeOrigin: true },
      },
    },
  }
})
