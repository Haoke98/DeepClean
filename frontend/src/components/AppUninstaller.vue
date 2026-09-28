<template>
  <div class="app-uninstaller">
    <!-- 平台提示 -->
    <el-alert
      v-if="!loading && !isMacos"
      type="info"
      :closable="false"
      show-icon
      class="platform-alert"
      title="当前系统不是 macOS"
      description="应用深度卸载功能针对 macOS 设计（扫描 /Applications、~/Library、/Library 及安装收据）。在非 macOS 环境下应用列表可能为空。"
    />

    <!-- 工具栏 -->
    <div class="toolbar">
      <el-input
        v-model="query"
        placeholder="搜索应用名称 / 别名 / Bundle ID（支持中文名）"
        clearable
        :prefix-icon="Search"
        class="search-input"
        @input="onQueryInput"
        @clear="loadApps()"
      />
      <el-button :icon="Refresh" :loading="loading" @click="loadApps()">刷新列表</el-button>
      <span v-if="!loading" class="summary">
        共 {{ apps.length }} 个应用
        <template v-if="totalSize > 0">，合计 {{ formatSize(totalSize) }}</template>
      </span>
    </div>

    <!-- 应用列表 -->
    <div class="app-table">
      <el-table
        :data="apps"
        v-loading="loading"
        stripe
        style="width: 100%"
        empty-text="未找到应用（当前环境可能不支持）"
        :default-sort="{ prop: 'size', order: 'descending' }"
        @sort-change="onSortChange"
      >
        <el-table-column label="应用" min-width="230" prop="name" sortable="custom">
          <template #default="{ row }">
            <div class="app-name">
              <span class="name" :title="aliasTitle(row)">{{ row.name }}</span>
              <el-tag v-if="row.system" size="small" type="warning">系统应用</el-tag>
              <el-tag v-if="row.running" size="small" type="danger" effect="plain">运行中</el-tag>
            </div>
            <div class="bundle">{{ row.bundle_id || '未知 Bundle ID' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="100">
          <template #default="{ row }">{{ row.version || '-' }}</template>
        </el-table-column>
        <el-table-column label="大小" width="110" align="right" prop="size" sortable="custom">
          <template #default="{ row }">
            {{ row.size === null || row.size === undefined ? '-' : formatSize(row.size) }}
          </template>
        </el-table-column>
        <el-table-column label="位置" min-width="250">
          <template #default="{ row }">
            <span class="path-text" :title="row.path">{{ row.path }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button type="danger" size="small" :icon="Delete" @click="openAnalyze(row)">
              深度卸载
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 深度卸载弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="940px"
      top="5vh"
      :close-on-click-modal="false"
      destroy-on-close
      @closed="onDialogClosed"
    >
      <!-- 阶段一: 分析残留 -->
      <template v-if="step === 'analyze'">
        <div v-if="analyzing" class="loading-box">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>正在分析残留文件…</span>
        </div>

        <div v-else-if="analyzeError" class="loading-box">
          <el-alert type="error" :closable="false" show-icon :title="analyzeError" class="block-alert" />
          <el-button type="primary" :icon="Refresh" @click="runAnalyze">重试</el-button>
        </div>

        <template v-else>
          <!-- 应用信息 -->
          <div class="app-meta" v-if="currentApp">
            <span class="meta-name">{{ currentApp.name }}</span>
            <el-tag size="small" type="info" effect="plain">v{{ appDetail?.version || currentApp.version || '?' }}</el-tag>
            <span class="meta-bundle">{{ appDetail?.bundle_id || currentApp.bundle_id || '无 Bundle ID' }}</span>
            <span class="meta-path" :title="currentApp.path">{{ currentApp.path }}</span>
          </div>

          <!-- 警示 -->
          <el-alert
            v-if="currentApp?.system"
            type="warning" :closable="false" show-icon class="block-alert"
            title="这是系统应用"
            description="系统应用的部分文件受 SIP 保护，删除可能被系统拒绝；请确认这是你想要卸载的应用。"
          />
          <el-alert
            v-if="runningPids.length"
            type="warning" :closable="false" show-icon class="block-alert"
            :title="`应用正在运行（${runningPids.length} 个相关进程）`"
            description="卸载前将先尝试退出这些进程（优雅退出，5 秒后强制结束）。"
          />
          <el-alert
            v-for="(w, i) in warnings"
            :key="i"
            type="info" :closable="false" show-icon class="block-alert"
            :title="w"
          />

          <!-- 卸载选项 -->
          <div class="options-bar">
            <span class="opt-label">删除方式</span>
            <el-radio-group v-model="mode" size="small">
              <el-radio value="trash">移入废纸篓（可恢复）</el-radio>
              <el-radio value="permanent">永久删除（不可恢复）</el-radio>
            </el-radio-group>
            <el-divider direction="vertical" />
            <el-checkbox v-model="stopProcesses" size="small">退出运行中的进程</el-checkbox>
            <el-checkbox
              v-if="isMacos"
              v-model="forgetReceipts"
              size="small"
            >忘记安装收据（pkgutil --forget）</el-checkbox>
          </div>

          <!-- 残留清单 -->
          <div class="items-summary">
            <span>
              共找到 <b>{{ items.length }}</b> 项 · 合计 <b>{{ formatSize(itemsTotalSize) }}</b>
              <span v-if="unselectedCount" class="fuzzy-note">
                · {{ unselectedCount }} 项默认未勾选（疑似/需确认）
              </span>
            </span>
            <span class="sel-info">
              已勾选 <b>{{ selected.length }}</b> 项 · 合计 <b>{{ formatSize(selectedSize) }}</b>
            </span>
          </div>

          <el-table
            ref="itemsTable"
            :data="items"
            row-key="path"
            size="small"
            stripe
            max-height="360"
            style="width: 100%"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="42" reserve-selection />
            <el-table-column label="分类" width="170">
              <template #default="{ row }">
                <el-tag size="small" :type="categoryType(row.category)">{{ row.category }}</el-tag>
                <el-tag
                  v-if="row.selected === false"
                  size="small" type="warning" effect="plain" class="status-tag"
                  :title="row.match === 'fuzzy' ? '按关键字模糊匹配的疑似残留，需人工确认后勾选' : '该分类属于用户数据/安装包，默认不勾选'"
                >
                  {{ row.match === 'fuzzy' ? '疑似关联' : '默认未选' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="95" align="right">
              <template #default="{ row }">{{ formatSize(row.size) }}</template>
            </el-table-column>
            <el-table-column label="路径" min-width="300">
              <template #default="{ row }">
                <span class="path-text" :title="row.path">{{ row.path }}</span>
              </template>
            </el-table-column>
            <el-table-column label="范围" width="130">
              <template #default="{ row }">
                <el-tag v-if="row.needs_admin" size="small" type="danger" effect="plain">
                  系统 · 需管理员
                </el-tag>
                <el-tag v-else size="small" type="info" effect="plain">用户</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </template>

      <!-- 阶段二: 执行结果 -->
      <template v-else>
        <el-alert
          :type="resultSummary?.failed_count ? 'warning' : 'success'"
          :closable="false" show-icon class="block-alert"
          :title="doneTitle"
          :description="doneDescription"
        />
        <el-table :data="results" size="small" stripe max-height="420" style="width: 100%">
          <el-table-column label="目标" min-width="300">
            <template #default="{ row }">
              <span class="path-text" :title="row.path">{{ row.path }}</span>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="140">
            <template #default="{ row }">
              <el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="95" align="right">
            <template #default="{ row }">{{ formatSize(row.size) }}</template>
          </el-table-column>
          <el-table-column label="说明" min-width="180">
            <template #default="{ row }">{{ row.message }}</template>
          </el-table-column>
        </el-table>
      </template>

      <template #footer>
        <div class="dialog-footer">
          <template v-if="step === 'analyze'">
            <el-button @click="dialogVisible = false">取消</el-button>
            <el-button
              type="danger"
              :icon="Delete"
              :loading="uninstalling"
              :disabled="analyzing || !!analyzeError || !selected.length"
              @click="confirmAndUninstall"
            >
              {{ selected.length ? `确认卸载（${selected.length} 项）` : '请勾选要删除的项' }}
            </el-button>
          </template>
          <el-button v-else type="primary" @click="dialogVisible = false">完成</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, Delete, Loading } from '@element-plus/icons-vue'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const api = axios.create({ baseURL: API_BASE })

const formatSize = (bytes) => {
  if (bytes === undefined || bytes === null) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = Number(bytes)
  let unitIndex = 0
  if (isNaN(size)) return '0 B'
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(2)} ${units[unitIndex]}`
}

// 统一错误信息提取(后端 detail 可能是字符串或 {message, rejected} 对象)
const fmtDetail = (error) => {
  const d = error?.response?.data?.detail
  if (!d) return error?.message || '未知错误'
  if (typeof d === 'string') return d
  if (d.message) {
    const rej = (d.rejected || []).map(r => `${r.path}（${r.reason}）`).join('；')
    return rej ? `${d.message}：${rej}` : d.message
  }
  return JSON.stringify(d)
}

const categoryType = (cat) => {
  if (cat === '应用本体') return 'danger'
  if (['缓存', '崩溃报告', '网络缓存', '网页视图缓存', '窗口状态', 'Cookie', '日志'].includes(cat)) return 'success'
  if (['启动项', '特权助手', '安装收据'].includes(cat)) return 'warning'
  return 'info'
}

const statusLabel = (s) => ({ trashed: '已移入废纸篓', deleted: '已删除', skipped: '已跳过', failed: '失败' }[s] || s)
const statusType = (s) => ({ trashed: 'success', deleted: 'success', skipped: 'info', failed: 'danger' }[s] || 'info')

// ============ 应用列表 ============
const apps = ref([])
const loading = ref(false)
const query = ref('')
const isMacos = ref(true)
const totalSize = computed(() => apps.value.reduce((s, a) => s + (a.size || 0), 0))

let queryTimer = null
const onQueryInput = () => {
  clearTimeout(queryTimer)
  queryTimer = setTimeout(() => loadApps(), 300)
}

// 排序交给后端完成(过滤/排序均不做前端实现): 默认体积降序
const sortBy = ref('size')
const sortOrder = ref('desc')

const onSortChange = ({ prop, order }) => {
  let sb = 'size'
  let so = 'desc'
  if (order !== null && (prop === 'name' || prop === 'size')) {
    sb = prop
    so = order === 'ascending' ? 'asc' : 'desc'
  }
  // 表格 default-sort 挂载时会触发一次, 参数未变则跳过重复请求
  if (sb === sortBy.value && so === sortOrder.value && apps.value.length) return
  sortBy.value = sb
  sortOrder.value = so
  loadApps()
}

const aliasTitle = (row) => {
  const others = (row.aliases || []).filter(a => a && a !== row.name)
  return others.length ? `别名: ${others.join(' / ')}` : ''
}

const loadApps = async (q = query.value) => {
  loading.value = true
  try {
    const { data } = await api.get('/api/apps', {
      params: { q: q || '', with_sizes: true, sort_by: sortBy.value, order: sortOrder.value },
    })
    apps.value = data.apps || []
    isMacos.value = !!data.is_macos
  } catch (error) {
    console.error('load apps error:', error)
    ElMessage.error('获取应用列表失败: ' + fmtDetail(error))
  } finally {
    loading.value = false
  }
}

// ============ 深度卸载弹窗 ============
const dialogVisible = ref(false)
const step = ref('analyze')            // analyze | done
const currentApp = ref(null)
const analyzing = ref(false)
const analyzeError = ref('')
const appDetail = ref(null)
const items = ref([])
const warnings = ref([])
const runningPids = ref([])
const mode = ref('trash')
const stopProcesses = ref(true)
const forgetReceipts = ref(true)
const itemsTable = ref(null)
const selected = ref([])
const uninstalling = ref(false)
const results = ref([])
const resultSummary = ref(null)

const dialogTitle = computed(() =>
  step.value === 'done' ? '卸载结果' : `深度卸载 - ${currentApp.value?.name || ''}`)
const itemsTotalSize = computed(() => items.value.reduce((s, i) => s + (i.size || 0), 0))
const selectedSize = computed(() => selected.value.reduce((s, i) => s + (i.size || 0), 0))
const unselectedCount = computed(() => items.value.filter(i => i.selected === false).length)

const doneTitle = computed(() => {
  const s = resultSummary.value
  if (!s) return '卸载完成'
  return s.failed_count
    ? `完成：${s.removed_count} 项成功，${s.failed_count} 项失败`
    : `卸载完成：共处理 ${s.removed_count} 项`
})

const doneDescription = computed(() => {
  const s = resultSummary.value
  if (!s) return ''
  const parts = []
  if (s.mode === 'trash') {
    parts.push(`已将合计 ${formatSize(s.total_size)} 的内容移入废纸篓，清空废纸篓后才会真正释放空间。`)
  } else {
    parts.push(`已永久删除合计 ${formatSize(s.total_size)} 的内容，不可恢复。`)
  }
  if (s.stopped?.length) parts.push(`已结束 ${s.stopped.length} 个相关进程。`)
  if (s.receipt_forgotten) parts.push('已执行 pkgutil --forget 忘记安装收据。')
  if (s.skipped_count) parts.push(`${s.skipped_count} 项已不存在，自动跳过。`)
  return parts.join(' ')
})

const openAnalyze = (row) => {
  currentApp.value = row
  step.value = 'analyze'
  analyzeError.value = ''
  appDetail.value = null
  items.value = []
  warnings.value = []
  runningPids.value = []
  selected.value = []
  results.value = []
  resultSummary.value = null
  mode.value = 'trash'
  stopProcesses.value = true
  forgetReceipts.value = true
  dialogVisible.value = true
  runAnalyze()
}

const runAnalyze = async () => {
  if (!currentApp.value) return
  analyzing.value = true
  analyzeError.value = ''
  try {
    const { data } = await api.get('/api/apps/analyze', { params: { path: currentApp.value.path } })
    appDetail.value = data.app
    items.value = data.items || []
    warnings.value = data.warnings || []
    runningPids.value = data.pids || []
  } catch (error) {
    console.error('analyze error:', error)
    analyzeError.value = '分析失败: ' + fmtDetail(error)
  } finally {
    analyzing.value = false
  }
  if (analyzeError.value || !items.value.length) return
  // 表格在 analyzing=false 后才渲染, 等 DOM 更新完成再按后端标记默认勾选
  await nextTick()
  applyDefaultSelection()
  setTimeout(applyDefaultSelection, 80)
}

// 只默认勾选强关联项(selected !== false); 疑似(模糊)/用户数据类默认不勾选
const applyDefaultSelection = () => {
  const table = itemsTable.value
  if (!table) return
  items.value.forEach(item => table.toggleRowSelection(item, item.selected !== false))
}

const onSelectionChange = (rows) => {
  selected.value = rows
}

const confirmAndUninstall = async () => {
  if (!selected.value.length) {
    ElMessage.warning('请至少勾选一项要删除的目标')
    return
  }
  const app = currentApp.value
  const isDanger = !!app?.system || mode.value === 'permanent'
  const modeText = mode.value === 'trash' ? '移入废纸篓' : '永久删除'
  const preview = selected.value.slice(0, 5).map(i => i.path).join('、')
  const more = selected.value.length > 5 ? ` 等 ${selected.value.length} 项` : ''
  const hasApp = selected.value.some(i => i.path === app.path)

  let msg = `将「${app.name}」${hasApp ? '（含应用本体）' : ''}${modeText}，`
  msg += `共 ${selected.value.length} 项，合计 ${formatSize(selectedSize.value)}：\n`
  msg += `${preview}${more}。\n`
  if (mode.value === 'permanent') msg += '\n⚠ 永久删除不可恢复！'
  if (app.system) msg += '\n⚠ 这是系统应用，部分文件受 SIP 保护。'
  if (runningPids.value.length && stopProcesses.value) msg += `\n将先结束 ${runningPids.value.length} 个运行中的进程。`

  try {
    await ElMessageBox.confirm(msg, isDanger ? '危险操作确认' : '确认卸载', {
      type: 'warning',
      confirmButtonText: isDanger ? '强制执行' : '确认卸载',
      cancelButtonText: '取消',
      customStyle: { whiteSpace: 'pre-line', textAlign: 'left' },
    })
  } catch {
    return // 用户取消
  }

  uninstalling.value = true
  try {
    const { data } = await api.post('/api/apps/uninstall', {
      app_path: app.path,
      paths: selected.value.map(i => i.path),
      bundle_id: appDetail.value?.bundle_id || app.bundle_id || '',
      app_name: app.name,
      mode: mode.value,
      stop_processes: stopProcesses.value,
      forget_receipts: forgetReceipts.value,
    })
    results.value = data.results || []
    resultSummary.value = data
    step.value = 'done'
    if (data.status === 'partial') {
      ElMessage.warning('部分目标删除失败，详见结果列表')
    }
  } catch (error) {
    console.error('uninstall error:', error)
    ElMessage.error('卸载失败: ' + fmtDetail(error))
  } finally {
    uninstalling.value = false
  }
}

const onDialogClosed = () => {
  // 卸载后关闭弹窗时刷新应用列表
  if (step.value === 'done') loadApps()
  step.value = 'analyze'
}

loadApps()
</script>

<style scoped>
.platform-alert {
  margin-bottom: 16px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}
.search-input {
  width: 320px;
}
.summary {
  color: #909399;
  font-size: 13px;
}
.app-table {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  padding: 4px;
}
.app-name {
  display: flex;
  align-items: center;
  gap: 8px;
}
.app-name .name {
  font-weight: 600;
}
.bundle {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
}
.path-text {
  color: #606266;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 36px 0;
  color: #909399;
}
.block-alert {
  margin-bottom: 10px;
}
.app-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #f5f7fa;
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}
.meta-name {
  font-weight: 600;
  font-size: 15px;
}
.meta-bundle {
  color: #409eff;
}
.meta-path {
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.options-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 8px 0 12px;
  font-size: 13px;
}
.opt-label {
  color: #606266;
}
.items-summary {
  display: flex;
  justify-content: space-between;
  padding: 6px 2px;
  font-size: 13px;
  color: #606266;
}
.sel-info b {
  color: #e6a23c;
}
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.fuzzy-note {
  color: #e6a23c;
  font-size: 12px;
}
.status-tag {
  margin-left: 4px;
}
</style>
