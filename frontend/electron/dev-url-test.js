// 端口解析器场景测试: node electron/dev-url-test.js
const fs = require('fs')
const path = require('path')
const { resolveDevUrl } = require('./dev-url')

const PORT_FILE = path.join(__dirname, '..', '.dev-port')
let passed = 0, failed = 0

async function check(name, options, expectUrl) {
  const url = await resolveDevUrl(options)
  const ok = url === expectUrl
  console.log(`${ok ? '✅' : '❌'} ${name}\n     期望=${expectUrl} 实际=${url}`)
  ok ? passed++ : failed++
}

;(async () => {
  const saved = fs.readFileSync(PORT_FILE, 'utf8').trim() // 当前真实端口
  console.log(`当前 .dev-port = ${saved}; 5173 上是别的项目(tauri-app), ${saved} 上是 DeepClean\n`)

  // 1) 端口文件正常
  await check('端口文件正常时返回文件端口', {}, `http://127.0.0.1:${saved}`)

  // 2) ★用户 bug 场景: 端口文件过期/被污染成 5173(别的项目)
  fs.writeFileSync(PORT_FILE, '5173')
  await check('★端口文件被写成 5173(其它项目) → 标题校验拒绝, 扫描到正确端口', {},
    `http://127.0.0.1:${saved}`)

  // 3) 端口文件不存在 + 无环境变量 → 靠扫描
  fs.unlinkSync(PORT_FILE)
  await check('无端口文件无环境变量 → 扫描并校验', {}, `http://127.0.0.1:${saved}`)

  // 4) 环境变量通道
  await check('DEEPCLEAN_DEV_PORT 环境变量通道(值仍需标题校验)', { env: { DEEPCLEAN_DEV_PORT: '5173' } },
    `http://127.0.0.1:${saved}`)
  await check('DEEPCLEAN_DEV_PORT 环境变量通道(正确端口)', { env: { DEEPCLEAN_DEV_PORT: saved } },
    `http://127.0.0.1:${saved}`)

  // 5) 显式 URL 优先
  await check('DEEPCLEAN_DEV_URL 显式指定优先', { env: { DEEPCLEAN_DEV_URL: 'http://127.0.0.1:9999' } },
    'http://127.0.0.1:9999')

  // 6) 5173 的页面本身不能通过校验(单独断言)
  const { isDeepCleanPage } = require('./dev-url')
  const t5173 = await isDeepCleanPage('http://127.0.0.1:5173/')
  const tSaved = await isDeepCleanPage(`http://127.0.0.1:${saved}/`)
  const okPair = t5173 === false && tSaved === true
  console.log(`${okPair ? '✅' : '❌'} 标题校验: 5173(其它项目)=${t5173} 应为 false, ${saved}(DeepClean)=${tSaved} 应为 true`)
  okPair ? passed++ : failed++

  // 恢复端口文件
  fs.writeFileSync(PORT_FILE, saved)
  console.log(`\n结果: ${passed} 通过, ${failed} 失败`)
  process.exit(failed ? 1 : 0)
})()
