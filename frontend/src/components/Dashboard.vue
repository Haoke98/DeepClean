<template>
  <div class="dashboard">
    <!-- 清理成果(全局贡献统计, 数据源=删除历史) -->
    <div class="stats-row">
      <el-card v-for="c in statCards" :key="c.label" class="stat-card" shadow="hover">
        <div class="stat-value" :class="{ accent: c.accent }">{{ c.value }}</div>
        <div class="stat-label">{{ c.label }}</div>
      </el-card>
      <el-card class="stat-card actions-card" shadow="never">
        <el-button type="primary" plain :icon="Clock" @click="openHistory">删除历史</el-button>
        <el-button :icon="Refresh" circle size="small" title="刷新统计" @click="refreshStats" />
      </el-card>
    </div>

    <!-- 系统状态(原"文件扫描" Tab 内的 Dashboard, 上移为全局) -->
    <div class="monitor-grid">
      <el-card>
        <template #header>
          <div class="monitor-header"><span>磁盘使用情况</span></div>
        </template>
        <el-progress
          type="dashboard"
          :percentage="systemStatus.disk.percent || 0"
          :color="getColorByPercentage(systemStatus.disk.percent)"
        >
          <template #default="{ percentage }">
            <div class="progress-content">
              <div>{{ percentage.toFixed(1) }}%</div>
              <div class="usage-details">
                <div>已用: {{ formatSize(systemStatus.disk.used) }}</div>
                <div>可用: {{ formatSize(systemStatus.disk.free) }}</div>
                <div>总计: {{ formatSize(systemStatus.disk.total) }}</div>
              </div>
            </div>
          </template>
        </el-progress>
      </el-card>

      <el-card>
        <template #header>
          <div class="monitor-header"><span>内存使用情况</span></div>
        </template>
        <el-progress
          type="dashboard"
          :percentage="systemStatus.memory.percent || 0"
          :color="getColorByPercentage(systemStatus.memory.percent)"
        >
          <template #default="{ percentage }">
            <div class="progress-content">
              <div>{{ percentage.toFixed(1) }}%</div>
              <div class="usage-details">
                <div>已用: {{ formatSize(systemStatus.memory.total - systemStatus.memory.used) }}</div>
                <div>可用: {{ formatSize(systemStatus.memory.used) }}</div>
                <div>总计: {{ formatSize(systemStatus.memory.total) }}</div>
              </div>
            </div>
          </template>
        </el-progress>
      </el-card>

      <el-card>
        <template #header>
          <div class="monitor-header"><span>交换内存使用情况</span></div>
        </template>
        <el-progress
          type="dashboard"
          :percentage="systemStatus.swap.percent || 0"
          :color="getColorByPercentage(systemStatus.swap.percent)"
        >
          <template #default="{ percentage }">
            <div class="progress-content">
              <div>{{ percentage.toFixed(1) }}%</div>
              <div class="usage-details">
                <div>已用: {{ formatSize(systemStatus.swap.used) }}</div>
                <div>可用: {{ formatSize(systemStatus.swap.total - systemStatus.swap.used) }}</div>
                <div>总计: {{ formatSize(systemStatus.swap.total) }}</div>
              </div>
            </div>
          </template>
        </el-progress>
      </el-card>
    </div>

    <!-- 删除历史弹窗 -->
    <el-dialog v-model="historyVisible" title="删除历史" width="880px" destroy-on-close>
      <div class="history-toolbar">
        <el-radio-group v-model="historySource" size="small" @change="reloadHistory">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="file_scan">文件扫描</el-radio-button>
          <el-radio-button value="app_uninstall">应用卸载</el-radio-button>
          <el-radio-button value="orphan_residue">残留清理</el-radio-button>
        </el-radio-group>
        <span class="history-total">共 {{ historyTotal }} 条</span>
        <el-button
          size="small" type="danger" plain :icon="Delete"
          :disabled="!historyTotal"
          @click="clearHistory"
        >清空历史</el-button>
      </div>

      <el-table :data="historyRecords" size="small" v-loading="historyLoading" max-height="440">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
        </el-table-column>
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="sourceTag(row.source)">{{ sourceName(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="方式" width="96">
          <template #default="{ row }">{{ row.mode === 'trash' ? '废纸篓' : '永久删除' }}</template>
        </el-table-column>
        <el-table-column label="条目" width="70" align="right">
          <template #default="{ row }">{{ row.count }}</template>
        </el-table-column>
        <el-table-column label="释放" width="100" align="right">
          <template #default="{ row }">{{ formatSize(row.bytes) }}</template>
        </el-table-column>
        <el-table-column label="明细" min-width="220">
          <template #default="{ row }">
            <span class="history-detail" :title="detailTip(row)">
              {{ detailText(row) }}
            </span>
            <el-tag v-if="!row.ok" size="small" type="danger" class="fail-tag">失败</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <template #footer>
        <el-button
          v-if="historyRecords.length < historyTotal"
          size="small" @click="loadMoreHistory"
        >加载更多（已显示 {{ historyRecords.length }}/{{ historyTotal }}）</el-button>
        <el-button size="small" type="primary" @click="historyVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Clock, Refresh, Delete } from '@element-plus/icons-vue'
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

// ============ 系统状态 ============
const systemStatus = ref({
  disk: { total: 0, used: 0, free: 0, percent: 0 },
  memory: { total: 0, used: 0, free: 0, percent: 0 },
  swap: { total: 0, used: 0, free: 0, percent: 0 },
})

const getColorByPercentage = (percent) => {
  if (percent < 60) return '#67C23A'
  if (percent < 80) return '#E6A23C'
  return '#F56C6C'
}

const updateSystemStatus = async () => {
  try {
    const { data } = await api.get('/api/monitor')
    systemStatus.value = data
  } catch (error) {
    console.error('Failed to get system status:', error)
  }
}

// ============ 清理成果统计 ============
const cleanStats = ref({
  operations: 0,
  items_deleted: 0,
  bytes_freed: 0,
  apps_uninstalled: 0,
  residues_cleared: 0,
})

const statCards = computed(() => [
  { label: '累计释放空间', value: formatSize(cleanStats.value.bytes_freed), accent: true },
  { label: '删除操作', value: `${cleanStats.value.operations} 次` },
  { label: '删除条目', value: `${cleanStats.value.items_deleted} 个` },
  { label: '卸载应用', value: `${cleanStats.value.apps_uninstalled} 个` },
  { label: '清理残留', value: `${cleanStats.value.residues_cleared} 项` },
])

const refreshStats = async () => {
  try {
    const { data } = await api.get('/api/history/stats')
    cleanStats.value = data
  } catch (error) {
    console.error('Failed to get clean stats:', error)
  }
}

// ============ 删除历史 ============
const historyVisible = ref(false)
const historyLoading = ref(false)
const historyRecords = ref([])
const historyTotal = ref(0)
const historySource = ref('')
const HISTORY_PAGE = 50

const sourceName = (s) =>
  ({ file_scan: '文件扫描', app_uninstall: '应用卸载', orphan_residue: '残留清理' }[s] || s || '未知')
const sourceTag = (s) =>
  ({ file_scan: '', app_uninstall: 'warning', orphan_residue: 'success' }[s] || 'info')

const fmtTime = (ts) => {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const detailText = (row) => {
  if (row.app?.name) {
    const more = row.count > 1 ? ` +${row.count - 1} 项残留` : ''
    return `${row.app.name}${more}`
  }
  const items = row.items || []
  if (!items.length) return row.detail || '-'
  const first = items[0].path.split('/').slice(-2).join('/')
  const more = row.count > items.length ? ` 等 ${row.count} 项` : (row.count > 1 ? ` 等 ${row.count} 项` : '')
  return `${first}${more}`
}

const detailTip = (row) => (row.items || []).map((i) => i.path).join('\n')

const loadHistory = async (append = false) => {
  historyLoading.value = true
  try {
    const offset = append ? historyRecords.value.length : 0
    const { data } = await api.get('/api/history', {
      params: { limit: HISTORY_PAGE, offset, source: historySource.value },
    })
    historyRecords.value = append ? historyRecords.value.concat(data.records) : data.records
    historyTotal.value = data.total
  } catch (error) {
    console.error('load history error:', error)
    ElMessage.error('加载删除历史失败')
  } finally {
    historyLoading.value = false
  }
}

const reloadHistory = () => loadHistory(false)
const loadMoreHistory = () => loadHistory(true)

const openHistory = () => {
  historyVisible.value = true
  historyRecords.value = []
  loadHistory(false)
}

const clearHistory = async () => {
  try {
    await ElMessageBox.confirm(
      '清空后统计归零且无法再追踪历史删除内容, 确定清空吗?', '清空删除历史',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' })
  } catch {
    return
  }
  try {
    const { data } = await api.delete('/api/history')
    ElMessage.success(`已清空 ${data.cleared} 条历史`)
    loadHistory(false)
    refreshStats()
  } catch (error) {
    ElMessage.error('清空失败: ' + (error.response?.data?.detail || error.message))
  }
}

// ============ 刷新时机 ============
let timer = null
const onDeleted = () => refreshStats()  // 其它 Tab 删除完成后立即刷新

onMounted(() => {
  updateSystemStatus()
  refreshStats()
  timer = setInterval(() => {
    updateSystemStatus()
    refreshStats()
  }, 30000)
  window.addEventListener('deepclean:deleted', onDeleted)
})

onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('deepclean:deleted', onDeleted)
})
</script>

<style scoped>
.dashboard {
  margin-bottom: 18px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.stat-card {
  text-align: center;
}

.stat-card :deep(.el-card__body) {
  padding: 14px 10px;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
  color: #303133;
  line-height: 1.3;
}

.stat-value.accent {
  color: #67c23a;
}

.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.actions-card {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.monitor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 20px;
}

.monitor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.progress-content {
  text-align: center;
}

.usage-details {
  margin-top: 10px;
  font-size: 12px;
  color: #666;
}

.history-toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
}

.history-total {
  flex: 1;
  color: #909399;
  font-size: 13px;
}

.history-detail {
  font-size: 12px;
  color: #606266;
}

.fail-tag {
  margin-left: 6px;
}
</style>
