<template>
  <div class="orphan-sweeper">
    <!-- 说明 + 控制栏 -->
    <div class="toolbar">
      <el-button type="primary" :icon="Search" :loading="scanning" @click="runScan">
        扫描无主残留
      </el-button>
      <el-radio-group v-model="mode" :disabled="scanning || cleaning">
        <el-radio-button value="trash">移入废纸篓</el-radio-button>
        <el-radio-button value="permanent">永久删除</el-radio-button>
      </el-radio-group>
      <span class="summary">
        针对<b>已被删除</b>的应用/文件遗留的残留（本体已不存在）：按证据分级，
        「无主 Bundle ID / 历史关联」默认勾选，「疑似」需手动确认。
      </span>
    </div>

    <!-- 扫描前的空态 -->
    <div v-if="!scanResult && !scanning" class="empty-box">
      <el-empty description="点击「扫描无主残留」开始 —— 可发现卸载时未勾选的漏网残留，以及在别处被删除的应用遗留文件">
        <el-button type="primary" :icon="Search" @click="runScan">开始扫描</el-button>
      </el-empty>
    </div>

    <div v-if="scanning" class="loading-box">
      <el-icon class="is-loading" :size="28"><Loading /></el-icon>
      <span>正在扫描残留目录与证据比对（含体积统计）…</span>
    </div>

    <template v-if="scanResult && !scanning">
      <!-- 警示 -->
      <el-alert
        v-for="(w, idx) in warnings" :key="idx"
        :title="w" type="warning" :closable="false" show-icon class="block-alert"
      />

      <!-- 汇总 -->
      <div class="items-summary">
        <span>
          共找到 <b>{{ items.length }}</b> 项 · 合计 <b>{{ formatSize(totalSize) }}</b>
          <template v-if="unselectedCount">
            · 默认勾选 <b>{{ items.length - unselectedCount }}</b> 项 <b>{{ formatSize(strongSize) }}</b>
            <span class="fuzzy-note">· 疑似 {{ unselectedCount }} 项 {{ formatSize(unselectedSize) }}（未勾选）</span>
          </template>
        </span>
        <span class="sel-info">
          已勾选 <b>{{ selected.length }}</b> 项 · 将清理 <b>{{ formatSize(selectedSize) }}</b>
        </span>
      </div>

      <!-- 结果 -->
      <div v-if="lastResult" class="last-result">
        <el-tag :type="lastResult.status === 'success' ? 'success' : 'warning'" effect="plain">
          {{ lastResultText }}
        </el-tag>
        <el-button size="small" text type="primary" @click="lastResult = null">关闭</el-button>
      </div>

      <!-- 0 项: 干净空态 -->
      <div v-if="!items.length" class="empty-box">
        <el-empty description="未发现无主残留 🎉 —— 可点击上方按钮重新扫描" />
      </div>

      <!-- 清单 -->
      <el-table v-if="items.length"
        ref="itemsTable"
        :data="items"
        row-key="path"
        size="small"
        stripe
        max-height="520"
        style="width: 100%"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column label="分类" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="categoryType(row.category)">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="证据" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="evidenceTag(row.match)" :effect="row.match === 'file_history' ? 'plain' : 'light'">
              {{ evidenceName(row.match) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100" align="right">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="路径" min-width="300">
          <template #default="{ row }">
            <span class="path-text" :title="row.path">{{ row.path }}</span>
          </template>
        </el-table-column>
        <el-table-column label="范围" width="90" align="center">
          <template #default="{ row }">
            <span :class="{ admin: row.needs_admin }">
              {{ row.needs_admin ? '系统·需管理员' : '用户' }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 底部操作 -->
      <div class="footer-bar">
        <span class="summary">已勾选 {{ selected.length }} 项 · 合计 {{ formatSize(selectedSize) }}</span>
        <el-button
          type="danger"
          :icon="Delete"
          :loading="cleaning"
          :disabled="!selected.length"
          @click="confirmAndClean"
        >
          清理选中项（{{ selected.length }}）
        </el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Delete, Loading } from '@element-plus/icons-vue'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const api = axios.create({ baseURL: API_BASE, withCredentials: true })

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

const categoryType = (cat) => {
  const map = {
    '偏好设置': 'primary', '缓存': 'info', '应用数据': 'warning',
    '沙盒容器': 'success', '群组容器': 'success', '安装收据': 'danger',
    '系统临时缓存': 'info', '用户隐藏配置': 'primary', '安装包': 'warning',
    '应用下载数据': 'warning',
  }
  return map[cat] || ''
}
const evidenceName = (m) =>
  ({ bundle: '无主', history: '历史关联', file_history: '疑似' }[m] || m || '未知')
const evidenceTag = (m) =>
  ({ bundle: 'danger', history: 'success', file_history: 'warning' }[m] || 'info')

// ============ 状态 ============
const scanning = ref(false)
const cleaning = ref(false)
const scanResult = ref(null)
const items = ref([])
const warnings = ref([])
const selected = ref([])
const itemsTable = ref(null)
const mode = ref('trash')
const lastResult = ref(null)

const totalSize = computed(() => items.value.reduce((s, i) => s + (i.size || 0), 0))
const selectedSize = computed(() => selected.value.reduce((s, i) => s + (i.size || 0), 0))
const unselectedCount = computed(() => items.value.filter(i => i.selected === false).length)
const unselectedSize = computed(() =>
  items.value.filter(i => i.selected === false).reduce((s, i) => s + (i.size || 0), 0))
const strongSize = computed(() =>
  items.value.filter(i => i.selected !== false).reduce((s, i) => s + (i.size || 0), 0))
const lastResultText = computed(() => {
  const r = lastResult.value
  if (!r) return ''
  let t = `上次清理：${r.removed_count} 项成功`
  if (r.failed_count) t += `，${r.failed_count} 项失败`
  t += `，释放 ${formatSize(r.total_size)}`
  return t
})

const onSelectionChange = (rows) => {
  selected.value = rows
}

const applyDefaultSelection = () => {
  const table = itemsTable.value
  if (!table) return
  items.value.forEach(item => table.toggleRowSelection(item, item.selected !== false))
}

const fmtDetail = (error) => {
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.rejected) {
    return detail.rejected.map(r => `${r.path} — ${r.reason}`).join('\n')
  }
  return error.message || '未知错误'
}

const runScan = async (keepResult = false) => {
  scanning.value = true
  scanResult.value = null
  items.value = []
  warnings.value = []
  selected.value = []
  if (keepResult !== true) lastResult.value = null
  try {
    const { data } = await api.get('/api/orphans/scan')
    scanResult.value = data
    items.value = data.items || []
    warnings.value = data.warnings || []
    await nextTick()
    applyDefaultSelection()
    setTimeout(applyDefaultSelection, 80)
    if (!items.value.length) {
      ElMessage.success('未发现无主残留 🎉')
    }
  } catch (error) {
    console.error('orphan scan error:', error)
    ElMessage.error('扫描失败: ' + fmtDetail(error))
  } finally {
    scanning.value = false
  }
}

const confirmAndClean = async () => {
  if (!selected.value.length) {
    ElMessage.warning('请至少勾选一项')
    return
  }
  const isDanger = mode.value === 'permanent' ||
    selected.value.some(i => i.needs_admin)
  const modeText = mode.value === 'trash' ? '移入废纸篓' : '永久删除'
  const preview = selected.value.slice(0, 5).map(i => i.path).join('、')
  const more = selected.value.length > 5 ? ` 等 ${selected.value.length} 项` : ''

  let msg = `将 ${selected.value.length} 项无主残留${modeText}，合计 ${formatSize(selectedSize.value)}：\n`
  msg += `${preview}${more}。\n`
  if (mode.value === 'permanent') msg += '\n⚠ 永久删除不可恢复！'
  if (selected.value.some(i => i.needs_admin)) msg += '\n⚠ 含系统范围目标，可能需要管理员权限。'

  try {
    await ElMessageBox.confirm(msg, isDanger ? '危险操作确认' : '确认清理', {
      type: 'warning',
      confirmButtonText: isDanger ? '强制执行' : '确认清理',
      cancelButtonText: '取消',
      customStyle: { whiteSpace: 'pre-line', textAlign: 'left' },
    })
  } catch {
    return
  }

  cleaning.value = true
  try {
    const { data } = await api.post('/api/orphans/delete', {
      paths: selected.value.map(i => i.path),
      mode: mode.value,
      forget_receipts: true,
    })
    lastResult.value = data
    if (data.status === 'partial') {
      ElMessage.warning('部分目标清理失败，详见结果')
    } else {
      ElMessage.success(`清理完成：${data.removed_count} 项，释放 ${formatSize(data.total_size)}`)
    }
    window.dispatchEvent(new CustomEvent('deepclean:deleted'))  // 通知 Dashboard 刷新统计
    await runScan(true)  // 重新扫描反映最新状态(保留本次清理结果)
  } catch (error) {
    console.error('orphan delete error:', error)
    ElMessage.error('清理失败: ' + fmtDetail(error))
  } finally {
    cleaning.value = false
  }
}

onMounted(() => {
  runScan()  // 进入 Tab 即自动扫描一次
})
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.summary {
  color: #909399;
  font-size: 13px;
}
.summary b {
  color: #303133;
}
.empty-box {
  background: white;
  border-radius: 8px;
  padding: 24px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}
.loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 48px 0;
  color: #909399;
}
.block-alert {
  margin-bottom: 10px;
}
.items-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: white;
  border-radius: 8px 8px 0 0;
  padding: 10px 14px;
  font-size: 13px;
  color: #606266;
  border-bottom: 1px solid #ebeef5;
}
.fuzzy-note {
  color: #e6a23c;
  font-size: 12px;
}
.sel-info b {
  color: #f56c6c;
}
.last-result {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
}
.path-text {
  color: #606266;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.admin {
  color: #e6a23c;
  font-size: 12px;
}
.footer-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: white;
  border-radius: 0 0 8px 8px;
  padding: 12px 14px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}
</style>
