import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import electron from 'vite-plugin-electron'
import { resolve } from 'path'
import net from 'node:net'
import fs from 'node:fs'

// ============================================================
// 开发端口动态分配:
//   1. 从 5173 起探测空闲端口, strictPort 保证"配置的端口 == 实际监听端口";
//   2. 通过两条通道把真实端口传递出去:
//      - process.env.DEEPCLEAN_DEV_PORT → vite-plugin-electron 拉起的 Electron(继承环境变量)
//      - frontend/.dev-port 文件        → 独立启动的 Electron(electron:dev)/wait-on 等外部进程
//   3. Electron 侧(electron/dev-url.js)还会按页面标题校验, 即使端口文件过期,
//      也不会把别的项目占用的端口当成 DeepClean 的页面加载。
// ============================================================

const DEV_PORT_FILE = resolve(__dirname, '.dev-port')
const PORT_MIN = 5173
const PORT_MAX = 5199

function isPortFree(port) {
  return new Promise((res) => {
    const probe = net.createServer()
    probe.once('error', () => res(false))
    probe.once('listening', () => probe.close(() => res(true)))
    probe.listen(port, '127.0.0.1')
  })
}

async function resolveDevPort() {
  // 允许显式指定: DEEPCLEAN_DEV_PORT=6000 npm run dev
  const preset = Number(process.env.DEEPCLEAN_DEV_PORT)
  if (Number.isInteger(preset) && preset > 0) return preset
  for (let p = PORT_MIN; p <= PORT_MAX; p++) {
    if (await isPortFree(p)) return p
  }
  throw new Error(`[DeepClean] ${PORT_MIN}-${PORT_MAX} 区间没有可用端口`)
}

export default defineConfig(async ({ command }) => {
  // vite preview 也会传 command === 'serve', 用 argv 区分, 避免污染开发端口文件
  const isPreview = process.argv.includes('preview')
  const isServe = command === 'serve' && !isPreview

  const port = isServe ? await resolveDevPort() : undefined
  if (isServe) {
    // 环境变量通道: 配置阶段即写入, 之后 vite-plugin-electron spawn 的 Electron 会继承
    process.env.DEEPCLEAN_DEV_PORT = String(port)
  }
  if (process.env.DEEPCLEAN_DEV_DEBUG) {
    console.log('[DeepClean] config:', JSON.stringify({ command, argv: process.argv.slice(1), isServe, port }))
  }

  // 无 GUI 环境(CI/远程服务器)或 Electron 已由外部启动时, 跳过自动拉起
  const skipElectron = isServe && !!process.env.DEEPCLEAN_SKIP_ELECTRON

  return {
    plugins: [
      vue(),
      // .dev-port 文件通道: configureServer 是插件钩子, 必须放在 plugins 里才会执行
      ...(isServe ? [{
        name: 'deepclean:dev-port-file',
        configureServer(server) {
          // 服务真正开始监听后再写端口文件(避免文件先于服务就绪)
          server.httpServer?.once('listening', () => {
            try {
              fs.writeFileSync(DEV_PORT_FILE, String(port))
            } catch (e) {
              console.warn('[DeepClean] 写入 .dev-port 失败:', e.message)
            }
          })
          // 服务关闭时清理, 防止留下过期端口(过期时 Electron 侧也有标题校验兜底)
          server.httpServer?.once('close', () => {
            try {
              fs.unlinkSync(DEV_PORT_FILE)
            } catch { /* 文件不存在则忽略 */ }
          })
        }
      }] : []),
      ...(skipElectron ? [] : [electron({ entry: 'electron/main.js' })]),
    ],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    },
    server: {
      host: '127.0.0.1',   // 固定 IPv4, 与 Electron 加载/页面探活的地址保持一致
      port,
      strictPort: isServe, // 端口被占直接报错, 不再静默退避(配合空闲端口探测, 一般不会触发)
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
          secure: false,
          rewrite: (path) => path.replace(/^\/api/, '')
        }
      }
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets'
    }
  }
})
