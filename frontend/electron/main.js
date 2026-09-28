const { app, BrowserWindow } = require('electron')
const path = require('path')

// 兼容两种产物布局: 源码运行(electron/main.js) 与 dist-electron 构建产物
let resolveDevUrl
try {
  ({ resolveDevUrl } = require('./dev-url'))
} catch {
  ({ resolveDevUrl } = require('../electron/dev-url'))
}

const isDev = process.env.NODE_ENV === 'development'
const FALLBACK_DEV_URL = 'http://127.0.0.1:5173'

function createWindow(url) {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      webSecurity: false
    }
  })

  if (url) {
    // 开发环境: 加载 vite 开发服务器(动态端口, 已按页面标题校验是 DeepClean 自己的页面)
    console.log(`[DeepClean] 开发模式加载: ${url}`)
    win.loadURL(url)
    win.webContents.openDevTools()
  } else {
    // 生产环境: 加载构建产物
    win.loadFile(path.join(__dirname, '../dist/index.html'))
  }
  return win
}

async function resolveUrl() {
  if (!isDev) return null
  try {
    const url = await resolveDevUrl()
    if (url) return url
    console.warn(`[DeepClean] 未找到运行中的开发服务器, 回退 ${FALLBACK_DEV_URL}`)
  } catch (e) {
    console.error('[DeepClean] 解析开发服务器地址失败, 回退 FALLBACK:', e)
  }
  return FALLBACK_DEV_URL
}

app.whenReady().then(async () => {
  const url = await resolveUrl()
  createWindow(url)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow(url)
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
