<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="760px"
    top="6vh"
    :close-on-click-modal="false"
    destroy-on-close
    @closed="handleClosed"
  >
    <!-- 顶部: 文件信息卡片 -->
    <div class="file-card" v-if="file">
      <div class="file-meta">
        <el-tag size="small" type="info">{{ file.type || '未知类型' }}</el-tag>
        <span class="file-size">{{ formatSize(file.size) }}</span>
        <span class="file-path" :title="file.path">{{ shortPath }}</span>
      </div>
    </div>

    <!-- 未配置 AI 时引导 -->
    <div v-if="!aiConfigured && !loadingConfig" class="not-configured">
      <el-empty description="尚未配置 AI 模型">
        <el-button type="primary" @click="openSettings">前往 AI 设置</el-button>
      </el-empty>
    </div>

    <div v-else class="ai-content">
      <!-- 分析结果 -->
      <div ref="analysisBox" class="analysis-box markdown-body">
        <div v-if="!analysis && !analyzing" class="analysis-empty">
          <el-empty description="点击下方按钮开始 AI 分析" :image-size="60">
            <el-button type="primary" :icon="MagicStick" @click="startAnalysis">
              开始 AI 分析
            </el-button>
          </el-empty>
        </div>
        <div v-if="analyzing && !analysis" class="analysis-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>AI 正在分析中…</span>
        </div>
        <div v-if="analysis" v-html="renderedAnalysis"></div>
        <div v-if="analyzing && analysis" class="cursor-blink">▊</div>
      </div>

      <!-- 对话区 -->
      <div class="chat-area">
        <div ref="chatBox" class="chat-list">
          <div v-for="(msg, i) in chatMessages" :key="i" :class="['chat-item', msg.role]">
            <div class="chat-bubble" v-html="renderMarkdown(msg.content)"></div>
          </div>
          <div v-if="chatLoading" class="chat-item assistant">
            <div class="chat-bubble">
              <el-icon class="is-loading"><Loading /></el-icon>
            </div>
          </div>
        </div>
        <div class="chat-input">
          <el-input
            v-model="chatInput"
            type="textarea"
            :rows="2"
            placeholder="基于以上分析继续提问, 例如: 这个文件删了会影响什么?"
            @keydown.enter.exact.prevent="sendChat"
            :disabled="chatLoading"
          />
          <el-button
            type="primary"
            :icon="Promotion"
            :loading="chatLoading"
            :disabled="!chatInput.trim()"
            @click="sendChat"
          >
            发送
          </el-button>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button :icon="Setting" @click="openSettings">AI 设置</el-button>
        <el-button
          v-if="analysis"
          type="primary"
          :icon="RefreshRight"
          :loading="analyzing"
          @click="startAnalysis"
        >
          重新分析
        </el-button>
      </div>
    </template>

    <!-- AI 设置抽屉 -->
    <el-drawer
      v-model="settingsVisible"
      title="AI 模型配置"
      size="480px"
      append-to-body
      :close-on-click-modal="false"
    >
      <el-form label-position="top" :model="configForm">
        <el-form-item label="接口地址 (Base URL)">
          <el-input v-model="configForm.base_url" placeholder="https://ark.cn-beijing.volces.com/api/v3" />
          <div class="form-tip">任何 OpenAI 兼容接口: 火山方舟 / OpenAI / DeepSeek / Ollama 等</div>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="configForm.api_key" placeholder="sk-... (本地 Ollama 可留空)" show-password />
          <div class="form-tip">仅保存在本机 backend/.env, 不会上传</div>
        </el-form-item>
        <el-form-item label="模型 ID / 名称">
          <el-input v-model="configForm.model" placeholder="ep-xxx / gpt-4o-mini / qwen2.5:7b" />
        </el-form-item>
        <el-form-item label="温度 (temperature)">
          <el-slider v-model="configForm.temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>
        <el-form-item label="最大 Token 数">
          <el-input-number v-model="configForm.max_tokens" :min="200" :max="16000" :step="200" />
        </el-form-item>
        <el-form-item label="超时 (秒)">
          <el-input-number v-model="configForm.timeout" :min="10" :max="300" :step="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="loadConfig" :icon="RefreshLeft">重置</el-button>
        <el-button type="primary" @click="saveConfig">保存配置</el-button>
      </template>
    </el-drawer>
  </el-dialog>
</template>

<script setup>
import { ref, computed, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { MagicStick, Setting, Promotion, Loading, RefreshRight, RefreshLeft } from '@element-plus/icons-vue'
import axios from 'axios'
import MarkdownIt from 'markdown-it'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const api = axios.create({ baseURL: API_BASE })

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const props = defineProps({
  modelValue: Boolean,
  file: Object
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

const formatSize = (bytes) => {
  if (bytes === undefined || bytes === null) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = Number(bytes); let unitIndex = 0
  if (isNaN(size)) return '0 B'
  while (size >= 1024 && unitIndex < units.length - 1) { size /= 1024; unitIndex++ }
  return `${size.toFixed(2)} ${units[unitIndex]}`
}

const shortPath = computed(() => {
  const p = props.file?.path || ''
  return p.length > 60 ? '…' + p.slice(-59) : p
})

const dialogTitle = computed(() => {
  const name = props.file?.path?.split('/').pop() || '文件'
  return `AI 分析 - ${name}`
})

// ============ AI 配置 ============
const aiConfigured = ref(true)
const loadingConfig = ref(false)
const settingsVisible = ref(false)
const configForm = ref({
  base_url: '', api_key: '', model: '',
  temperature: 0.3, max_tokens: 2000, timeout: 60
})

const loadConfig = async () => {
  loadingConfig.value = true
  try {
    const { data } = await api.get('/api/ai/config')
    aiConfigured.value = data.configured
    configForm.value = { ...data.config }
  } catch (e) {
    console.error('load ai config failed:', e)
  } finally {
    loadingConfig.value = false
  }
}

const saveConfig = async () => {
  try {
    const { data } = await api.post('/api/ai/config', configForm.value)
    aiConfigured.value = true
    ElMessage.success('AI 配置已保存')
    settingsVisible.value = false
    if (visible.value && !analysis.value && !analyzing.value) {
      // 刚配置完自动开始分析
      startAnalysis()
    }
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  }
}

const openSettings = () => { settingsVisible.value = true }

// ============ 分析 ============
const analysis = ref('')
const analyzing = ref(false)
const analysisBox = ref(null)
let analyzeAbort = null

const renderedAnalysis = computed(() => {
  if (!analysis.value) return ''
  try {
    return md.render(analysis.value)
  } catch (e) {
    return `<pre>${analysis.value}</pre>`
  }
})

const startAnalysis = async () => {
  if (analyzing.value) return
  analysis.value = ''
  analyzing.value = true
  analyzeAbort = new AbortController()
  try {
    const resp = await fetch(`${API_BASE}/api/ai/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_path: props.file?.path,
        file_type: props.file?.type,
        file_size: props.file?.size,
        md5: props.file?.md5
      }),
      signal: analyzeAbort.signal
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 按 SSE 事件解析
      let idx
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const event = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const line = event.split('\n').find(l => l.startsWith('data:'))
        if (!line) continue
        const payload = line.slice(5).trim()
        if (payload === '[DONE]') continue
        try {
          const obj = JSON.parse(payload)
          if (obj.content) {
            analysis.value += obj.content
            scrollToBottom(analysisBox.value)
          }
        } catch (e) { /* 忽略坏行 */ }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      analysis.value += `\n\n> ⚠️ 分析失败: ${e.message}`
    }
  } finally {
    analyzing.value = false
    analyzeAbort = null
  }
}

// ============ 对话 ============
const chatMessages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatBox = ref(null)

const sendChat = async () => {
  const question = chatInput.value.trim()
  if (!question || chatLoading.value) return
  chatInput.value = ''
  chatLoading.value = true
  const userMsg = { role: 'user', content: question }
  const assistantMsg = { role: 'assistant', content: '' }
  chatMessages.value.push(userMsg)
  chatMessages.value.push(assistantMsg)

  const history = [...chatMessages.value.slice(0, -1)]
  if (analysis.value) {
    history.unshift({ role: 'user', content: `[AI 分析报告]\n${analysis.value}` })
  }

  try {
    const resp = await fetch(`${API_BASE}/api/ai/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_path: props.file?.path,
        file_type: props.file?.type,
        file_size: props.file?.size,
        md5: props.file?.md5,
        question,
        history
      }),
      signal: undefined
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const event = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const line = event.split('\n').find(l => l.startsWith('data:'))
        if (!line) continue
        const payload = line.slice(5).trim()
        if (payload === '[DONE]') continue
        try {
          const obj = JSON.parse(payload)
          if (obj.content) {
            assistantMsg.content += obj.content
            chatMessages.value = [...chatMessages.value]
            scrollToBottom(chatBox.value)
          }
        } catch (e) { /* 忽略坏行 */ }
      }
    }
  } catch (e) {
    assistantMsg.content += `\n\n> ⚠️ 对话失败: ${e.message}`
    chatMessages.value = [...chatMessages.value]
  } finally {
    chatLoading.value = false
  }
}

const renderMarkdown = (content) => {
  try {
    return md.render(content || '')
  } catch (e) {
    return `<pre>${content}</pre>`
  }
}

const scrollToBottom = (el) => {
  nextTick(() => {
    if (el) el.scrollTop = el.scrollHeight
  })
}

const handleClosed = () => {
  // 关闭即中止流(不清理分析结果, 下次打开同一文件还能看)
  analyzeAbort?.abort()
  analyzeAbort = null
}

watch(() => props.modelValue, (v) => {
  if (v) {
    loadConfig()
    if (!analysis.value && aiConfigured.value) startAnalysis()
  }
})

watch(() => props.file?.path, (p, old) => {
  // 切换了文件: 清空分析/对话
  if (p && p !== old) {
    analysis.value = ''
    chatMessages.value = []
  }
})

defineExpose({ startAnalysis })
</script>

<style scoped>
.file-card {
  margin-bottom: 12px;
}
.file-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.file-size { color: #e6a23c; font-weight: 600; }
.file-path {
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.ai-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.analysis-box {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
  background: #fafbfc;
  max-height: 340px;
  min-height: 120px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.7;
}
.analysis-empty { padding: 20px 0; }
.analysis-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #909399;
  padding: 24px 0;
  justify-content: center;
}
.cursor-blink {
  color: #409eff;
  animation: blink 1s infinite;
  display: inline-block;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.chat-area {
  border-top: 1px solid #ebeef5;
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-list {
  max-height: 220px;
  min-height: 60px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-item {
  display: flex;
}
.chat-item.user { justify-content: flex-end; }
.chat-item.assistant { justify-content: flex-start; }
.chat-bubble {
  max-width: 85%;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
}
.chat-item.user .chat-bubble {
  background: #409eff;
  color: white;
}
.chat-item.assistant .chat-bubble {
  background: #f4f4f5;
  color: #303133;
}
.chat-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.chat-input :deep(.el-textarea__inner) {
  resize: none;
}
.dialog-footer {
  display: flex;
  justify-content: space-between;
}
.not-configured {
  padding: 30px 0;
}
.markdown-body :deep(h2) {
  font-size: 15px;
  margin: 14px 0 6px;
  color: #303133;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 4px;
}
.markdown-body :deep(p) { margin: 6px 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 20px; margin: 6px 0; }
.markdown-body :deep(code) {
  background: #f0f2f5;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
}
.markdown-body :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
}
.markdown-body :deep(pre code) { background: transparent; padding: 0; }
.markdown-body :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid #e6a23c;
  background: #fdf6ec;
  color: #b88230;
  border-radius: 0 4px 4px 0;
}
</style>
