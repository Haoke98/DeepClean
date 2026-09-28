// 开发服务器地址解析 —— 纯 Node 实现, 不依赖 electron, 可用 node 独立测试。
//
// 背景: vite 的开发端口是动态的(5173 被占用则顺延), 端口信息通过
// 环境变量 DEEPCLEAN_DEV_PORT 和 frontend/.dev-port 文件两条通道传递;
// 但这两条通道都可能过期(端口文件残留、另一个项目恰好占了该端口),
// 因此最终加载前会 GET 页面并按 <title> 校验"这确实是 DeepClean 的页面",
// 从根上避免把别的项目(如另一个占用 5173 的 dev server)当成自己的页面加载。
const fs = require('fs')
const path = require('path')
const http = require('http')

const PAGE_MARK = 'DeepFC' // frontend/index.html 中的 <title>
const PORT_MIN = 5173
const PORT_MAX = 5199
const FETCH_TIMEOUT = 1500 // 毫秒/次
const MAX_BODY = 64 * 1024

function fetchBody(url, redirectsLeft = 2) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: FETCH_TIMEOUT }, (res) => {
      const status = res.statusCode || 0
      if (status >= 300 && status < 400 && res.headers.location && redirectsLeft > 0) {
        res.resume()
        let next
        try {
          next = new URL(res.headers.location, url).toString()
        } catch {
          resolve('')
          return
        }
        resolve(fetchBody(next, redirectsLeft - 1))
        return
      }
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (chunk) => {
        if (body.length < MAX_BODY) body += chunk
      })
      res.on('end', () => resolve(body))
      res.on('error', () => resolve(body))
    })
    req.on('timeout', () => {
      req.destroy()
      resolve('')
    })
    req.on('error', () => resolve(''))
  })
}

async function isDeepCleanPage(url) {
  const body = await fetchBody(url)
  return body.includes(PAGE_MARK)
}

/**
 * 解析开发服务器地址。
 * 顺序: 显式环境变量 → .dev-port 文件 → DEEPCLEAN_DEV_PORT → 5173-5199 扫描,
 * 每个候选都必须通过页面标题校验; 全部失败返回 null。
 * @param {{env?: object, portFiles?: string[]}} [options]
 * @returns {Promise<string|null>} 形如 http://127.0.0.1:5174
 */
async function resolveDevUrl(options = {}) {
  const env = options.env || process.env

  // 1) 显式指定优先(人工覆盖)
  if (env.DEEPCLEAN_DEV_URL) return env.DEEPCLEAN_DEV_URL

  // 2) 收集候选端口(去重保序)
  const candidates = []
  const add = (value) => {
    const p = Number(String(value).trim())
    if (Number.isInteger(p) && p > 0 && p < 65536 && !candidates.includes(p)) candidates.push(p)
  }
  const portFiles = options.portFiles || [
    path.join(__dirname, '..', '.dev-port'),       // 源码运行: frontend/electron/../.dev-port
    path.join(process.cwd(), '.dev-port'),          // 兜底: 从 frontend 目录启动时
  ]
  for (const file of portFiles) {
    try {
      add(fs.readFileSync(file, 'utf8'))
    } catch { /* 文件不存在则忽略 */ }
  }
  add(env.DEEPCLEAN_DEV_PORT)
  for (let p = PORT_MIN; p <= PORT_MAX; p++) add(p)

  // 3) 逐个校验: 端口上跑的必须是 DeepClean 的页面
  for (const p of candidates) {
    const base = `http://127.0.0.1:${p}`
    if (await isDeepCleanPage(`${base}/`)) return base
  }
  return null
}

module.exports = { resolveDevUrl, isDeepCleanPage, fetchBody, PAGE_MARK, PORT_MIN, PORT_MAX }
