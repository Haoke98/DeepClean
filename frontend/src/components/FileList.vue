<template>
  <div class="file-manager">
    <div class="monitor-grid">
      <!-- 磁盘使用情况 -->
      <el-card>
        <template #header>
          <div class="monitor-header">
            <span>磁盘使用情况</span>
          </div>
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

      <!-- 内存使用情况 -->
      <el-card>
        <template #header>
          <div class="monitor-header">
            <span>内存使用情况</span>
          </div>
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
                <div>已用: {{ formatSize(systemStatus.memory.used) }}</div>
                <div>可用: {{ formatSize(systemStatus.memory.free) }}</div>
                <div>总计: {{ formatSize(systemStatus.memory.total) }}</div>
              </div>
            </div>
          </template>
        </el-progress>
      </el-card>

      <!-- 交换内存使用情况 -->
      <el-card>
        <template #header>
          <div class="monitor-header">
            <span>交换内存使用情况</span>
          </div>
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
                <div>可用: {{ formatSize(systemStatus.swap.free) }}</div>
                <div>总计: {{ formatSize(systemStatus.swap.total) }}</div>
              </div>
            </div>
          </template>
        </el-progress>
      </el-card>
    </div>

    <!-- 控制栏 -->
    <div class="controls">
      <el-select v-model="scanPath" placeholder="选择扫描路径" class="path-select">
        <el-option
          v-for="item in pathOptions"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      
      <el-input
        v-model="customPath"
        placeholder="输入自定义路径"
        class="custom-path"
        :disabled="scanPath !== 'custom'"
      />
      
      <el-input-number 
        v-model="minSize" 
        :min="1"
        :max="1000"
        label="最小文件大小(MB)"
      />
      
      <el-button type="primary" @click="scanFiles">
        扫描文件
      </el-button>
    </div>
    <el-progress
        v-if="scanning"
        :percentage="scanProgress"
        :status="scanProgress < 100 ? 'processing' : 'success'"
    >
      <span>已扫描 {{ scannedDirs }} 个目录</span>
    </el-progress>

    <!-- 文件列表 -->
    <div class="file-list">
      <el-table :data="files" style="width: 100%">
        <el-table-column prop="type" label="类型" width="160" />
        <el-table-column prop="size" label="大小" width="120">
          <template #default="scope">
            {{ formatSize(scope.row.size) }}
          </template>
        </el-table-column>
        <el-table-column prop="relative_path" label="路径" />
        <el-table-column label="操作" width="250">
          <template #default="scope">
            <el-button-group>
              <el-button 
                @click="handleAction('preview', scope.row)"
                :icon="View"
                size="small">
                预览
              </el-button>
              <el-button 
                @click="handleAction('reveal', scope.row)"
                :icon="Folder"
                size="small">
                打开目录
              </el-button>
              <el-button 
                @click="handleAction('delete', scope.row)"
                :icon="Delete"
                type="danger"
                size="small">
                删除
              </el-button>
            </el-button-group>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { View, Folder, Delete } from '@element-plus/icons-vue'
import axios from 'axios'

const files = ref([])
const groupBy = ref('size')
const minSize = ref(10)
const scanPath = ref('wechat')
const customPath = ref('')

const pathOptions = [
  {
    label: '系统级扫描',
    value: 'system',
  },
  {
    label: '用户级扫描',
    value: 'user',
  },
  {
    label: '微信历史消息',
    value: 'wechat',
  },
  {
    label: 'MacOS照片库',
    value: 'photos',
  },
  {
    label: 'Yarn缓存',
    value: 'yarn',
  },
  {
    label: 'Jetbrains全家桶缓存',
    value: 'jetbrains',
  },
  {
    label: 'Lark缓存',
    value: 'lark',
  },
  {
    label: 'PIP缓存',
    value: 'pip',
  },
  {
    label: 'Google缓存',
    value: 'google',
  },
  {
    label: '自定义路径',
    value: 'custom',
  },
]

const formatSize = (bytes) => {
  if (bytes === undefined || bytes === null) {
    return '0 B'
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = Number(bytes)
  let unitIndex = 0
  
  if (isNaN(size)) {
    return '0 B'
  }
  
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  
  return `${size.toFixed(2)} ${units[unitIndex]}`
}

// 创建 axios 实例
const api = axios.create({
  baseURL: 'http://127.0.0.1:5173',
  withCredentials: true
})

const systemStatus = ref({
  disk: { total: 0, used: 0, free: 0, percent: 0 },
  memory: { total: 0, used: 0, free: 0, percent: 0 },
  swap: { total: 0, used: 0, free: 0, percent: 0 }
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
    ElMessage.error('获取系统状态失败')
  }
}

const scanning = ref(false)
const scanProgress = ref(0)
const scannedDirs = ref(0)

// 定期检查扫描进度
const checkProgress = async () => {
  if (!scanning.value) return
  
  try {
    const { data } = await api.get('/api/scan/progress')
    scanning.value = data.scanning
    scanProgress.value = data.progress
    scannedDirs.value = data.scanned_dirs
    
    if (scanning.value) {
      // 获取当前已扫描的文件
      const { data: filesData } = await api.get('/api/scan/files')
      files.value = filesData.files
      
      // 继续检查进度
      setTimeout(checkProgress, 1000)
    }
  } catch (error) {
    console.error('Failed to get scan progress:', error)
  }
}

// 修改扫描方法
const scanFiles = async () => {
  try {
    scanning.value = true
    scanProgress.value = 0
    scannedDirs.value = 0
    
    const path = scanPath.value === 'custom' ? customPath.value : scanPath.value
    await api.get(`/api/scan?min_size=${minSize.value}&path=${path}`)
    
    // 开始检查进度
    checkProgress()
  } catch (error) {
    console.error('Scan error:', error)
    ElMessage.error('扫描失败')
    scanning.value = false
  }
}

const loadFiles = async () => {
  try {
    const { data } = await api.get(`/api/files?group_by=${groupBy.value}`)
    files.value = data
  } catch (error) {
    console.error('Load files error:', error)
    ElMessage.error('加载文件列表失')
  }
}

const handleAction = async (action, file) => {
  try {
    await api.post(`/api/file/action/${action}`, {
      file_path: file.path,
      base_path: scanPath.value
    })
    
    if (action === 'delete') {
      await loadFiles()
      ElMessage.success('删除成功')
    }
  } catch (error) {
    console.error('Action error:', error.response?.data || error)
    ElMessage.error(`操作失败: ${error.response?.data?.detail || '未知错误'}`)
  }
}

// 组件挂载时启动定时器
onMounted(() => {
  loadFiles()
  updateSystemStatus()
  // 每5秒更新一次系统状态
  setInterval(updateSystemStatus, 5000)
})
</script>

<style scoped>
.file-manager {
  padding: 20px;
}

.controls {
  margin-bottom: 20px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.path-select {
  width: 200px;
}

.custom-path {
  width: 300px;
}

.file-list {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 12px 0 rgba(0,0,0,0.1);
}

.monitor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 20px;
  margin-bottom: 20px;
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
</style> 