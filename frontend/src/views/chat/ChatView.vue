<script setup>
import { ref, reactive, computed, nextTick, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useUserStore } from '@/stores/index'
import request, { refreshAuth } from '@/utils/request'
import { ElMessage, ElMessageBox } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const userStore = useUserStore()
const route = useRoute()
const inputMessage = ref('')
const chatContainer = ref(null)
const isGenerating = ref(false)
const currentController = ref(null)

// 会话列表
const sessions = ref([
  { id: '1', name: '新对话', messages: [] },
])
const activeSessionId = ref('1')

const currentSession = computed(() =>
  sessions.value.find(s => s.id === activeSessionId.value)
)

// ========== 后端同步 ==========

async function loadHistory() {
  if (!userStore.token) return
  try {
    const res = await request.get('/api/chat/history')
    if (res.sessions?.length) {
      sessions.value = res.sessions
      activeSessionId.value = res.sessions[0].id
    }
  } catch {}
}

async function saveSession(session) {
  if (!userStore.token) return
  try {
    await request.post('/api/chat/history/save', {
      session_id: session.id,
      session_name: session.name,
      messages: session.messages.map(m => ({
        role: m.role,
        content: m.content,
        timestamp: m.timestamp || new Date().toISOString(),
      })),
    })
  } catch {}
}

function fillFromQuery() {
  const q = route.query.q
  if (q) inputMessage.value = q
}

onMounted(async () => {
  await loadHistory()
  fillFromQuery()
})

watch(() => route.query.q, fillFromQuery)

function createSession() {
  const id = Date.now().toString()
  sessions.value.unshift({
    id,
    name: '新对话',
    messages: [],
  })
  activeSessionId.value = id
}

function deleteSession(id) {
  const idx = sessions.value.findIndex(s => s.id === id)
  if (idx >= 0) {
    sessions.value.splice(idx, 1)
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value[0]?.id || ''
      if (sessions.value.length === 0) createSession()
    }
  }
  // 同步到后端
  if (sessions.value.length && sessions.value[0].messages.length) {
    saveSession(sessions.value[0])
  }
}

async function renameSession(id) {
  const session = sessions.value.find(s => s.id === id)
  if (!session) return
  try {
    const { value } = await ElMessageBox.prompt('请输入新名称', '重命名对话', {
      inputValue: session.name,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    if (value) {
      session.name = value
      saveSession(session)
    }
  } catch {}
}

async function clearAllHistory() {
  try {
    await ElMessageBox.confirm('确定要清除所有聊天记录吗？此操作不可恢复。', '清除记录', {
      confirmButtonText: '确定清除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await request.delete('/api/chat/history')
    sessions.value = [{ id: '1', name: '新对话', messages: [] }]
    activeSessionId.value = '1'
    ElMessage.success('聊天记录已清除')
  } catch {}
}

async function sendMessage() {
  const text = inputMessage.value.trim()
  if (!text || isGenerating.value) return

  const session = currentSession.value
  if (!session) return

  session.messages.push({
    role: 'user',
    content: text,
    timestamp: new Date().toISOString(),
  })

  if (session.messages.length === 1) {
    session.name = text.substring(0, 20) + (text.length > 20 ? '...' : '')
  }

  inputMessage.value = ''
  scrollToBottom()

  const aiMessage = reactive({
    role: 'assistant',
    content: '',
    references: [],
    timestamp: new Date().toISOString(),
    loading: true,
  })
  session.messages.push(aiMessage)
  isGenerating.value = true

  try {
    const contextMessages = session.messages
      .filter(m => !m.loading)
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))

    let systemContext = ''
    if (userStore.profile) {
      const p = userStore.profile
      const parts = []
      if (p.age) parts.push(`${p.age}岁`)
      if (p.gender) parts.push(p.gender)
      if (p.allergy_drug) parts.push(`药品过敏：${p.allergy_drug}`)
      if (p.medical_history) parts.push(`病史：${p.medical_history}`)
      if (p.family_history) parts.push(`家族史：${p.family_history}`)
      if (parts.length) systemContext = `用户健康档案：${parts.join('，')}`
    }

    const controller = new AbortController()
    currentController.value = controller

    // SSE 走原生 fetch，不经过 Axios 拦截器：
    // 401 时若有 refresh_token 则先刷新令牌（复用 request.js 的单飞刷新）后重试一次；
    // 429 / 其他非 200 给出明确中文错误提示（优先读响应 JSON 的 detail）
    const doFetch = () => fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${userStore.token}`,
      },
      body: JSON.stringify({
        messages: contextMessages,
        context: systemContext,
      }),
      signal: controller.signal,
    })

    let response = await doFetch()
    if (response.status === 401 && localStorage.getItem('refresh_token')) {
      try {
        await refreshAuth()
        // 刷新成功：同步 store 中的新令牌，并用新令牌重试一次（仅一次）
        userStore.setToken(localStorage.getItem('token'))
        userStore.setRefreshToken(localStorage.getItem('refresh_token'))
        response = await doFetch()
      } catch {
        // 刷新失败：保留原 401 响应，走下方登录过期分支（防死循环）
      }
    }

    if (!response.ok) {
      let detail = ''
      try {
        const errData = await response.json()
        detail = errData && errData.detail ? errData.detail : ''
      } catch {
        // 错误响应体非 JSON 时忽略
      }
      if (response.status === 401) {
        localStorage.removeItem('token')
        localStorage.removeItem('refresh_token')
        aiMessage.content = '登录已过期，请重新登录'
        window.location.href = '/login'
      } else if (response.status === 429) {
        aiMessage.content = detail || '操作过于频繁，请稍后再试'
        ElMessage.error(aiMessage.content)
      } else {
        aiMessage.content = detail || `请求失败（HTTP ${response.status}），请稍后再试`
      }
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6)
          if (data === '[DONE]') break
          try {
            const parsed = JSON.parse(data)
            if (parsed.content) aiMessage.content += parsed.content
            if (parsed.references) aiMessage.references = parsed.references
          } catch {}
        }
      }
      throttledScroll()
    }

    if (buffer.startsWith('data: ')) {
      const data = buffer.slice(6)
      if (data !== '[DONE]') {
        try {
          const parsed = JSON.parse(data)
          if (parsed.content) aiMessage.content += parsed.content
          if (parsed.references) aiMessage.references = parsed.references
        } catch {}
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      aiMessage.content += '\n\n[已停止生成]'
    } else {
      aiMessage.content = 'AI服务暂时不可用，请稍后再试。'
    }
  } finally {
    aiMessage.loading = false
    isGenerating.value = false
    currentController.value = null
    scrollToBottom()
    // 对话完成后保存到后端
    saveSession(session)
  }
}

function stopGenerating() {
  currentController.value?.abort()
}

function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

let scrollRafId = null
function throttledScroll() {
  if (scrollRafId) return
  scrollRafId = requestAnimationFrame(() => {
    scrollRafId = null
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

function copyMessage(content) {
  navigator.clipboard.writeText(content)
  ElMessage.success('已复制到剪贴板')
}

function jumpToKG(entityName) {
  window.open(`/kg?entity=${encodeURIComponent(entityName)}`, '_blank')
}

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text, { breaks: true }))
}
</script>

<template>
  <div class="h-full flex gap-5">
    <!-- Session list -->
    <div class="w-64 flex-shrink-0 glass-card flex flex-col overflow-hidden">
      <div class="flex items-center justify-between px-4 py-3.5 border-b border-primary-500/5">
        <span class="font-semibold font-display text-surface-800 text-sm">对话历史</span>
        <div class="flex gap-1">
          <el-button type="primary" size="small" icon="Plus" @click="createSession">新建</el-button>
          <el-button size="small" icon="Delete" @click="clearAllHistory">清除</el-button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-2 space-y-1">
        <div
          v-for="session in sessions"
          :key="session.id"
          class="p-3 rounded-xl cursor-pointer transition-all group relative"
          :class="activeSessionId === session.id
            ? 'bg-primary-500/10 text-primary-700'
            : 'hover:bg-primary-500/5 text-surface-600'"
          @click="activeSessionId = session.id"
        >
          <div
            v-if="activeSessionId === session.id"
            class="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-gradient-to-b from-primary-500 to-accent-500 rounded-r-full"
          />
          <div class="flex items-center justify-between">
            <span class="text-sm truncate flex-1">{{ session.name }}</span>
            <el-dropdown trigger="click" @command="(cmd) => cmd === 'rename' ? renameSession(session.id) : deleteSession(session.id)">
              <el-icon class="text-surface-400 opacity-0 group-hover:opacity-100 transition-opacity"><MoreFilled /></el-icon>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="rename">重命名</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <p class="text-xs text-surface-400 mt-1">
            {{ session.messages.length }} 条消息
          </p>
        </div>
      </div>
    </div>

    <!-- Chat window -->
    <div class="flex-1 glass-card flex flex-col overflow-hidden">
      <!-- Header -->
      <div class="flex items-center justify-between px-5 py-3 border-b border-primary-500/5">
        <span class="font-semibold font-display text-surface-800">{{ currentSession?.name || '智能问答' }}</span>
        <span class="text-xs text-surface-400 bg-primary-500/5 px-3 py-1 rounded-pill font-medium">基于 DeepSeek 大模型</span>
      </div>

      <!-- Messages area -->
      <div ref="chatContainer" class="flex-1 overflow-y-auto p-5 space-y-4">
        <!-- Welcome screen -->
        <div v-if="!currentSession?.messages.length" class="flex flex-col items-center justify-center h-full text-surface-400">
          <div class="w-20 h-20 rounded-3xl bg-gradient-to-br from-primary-500/10 to-accent-500/10 flex items-center justify-center mb-5 animate-float-y">
            <el-icon class="gradient-text" :size="40"><ChatDotRound /></el-icon>
          </div>
          <h3 class="text-xl font-bold font-display text-surface-800 mb-2">医疗知识图谱智能问答</h3>
          <p class="text-sm mb-8 text-surface-400 max-w-sm text-center">基于 4.4 万实体 + 30 万关系的医疗知识库，为您提供专业健康咨询</p>
          <div class="grid grid-cols-2 gap-3 max-w-md">
            <div
              v-for="q in ['感冒的症状有哪些？', '高血压吃什么药好？', '糖尿病的预防措施？', '发烧应该做什么检查？']"
              :key="q"
              class="glass-card p-3 text-sm cursor-pointer hover:border-primary-300/50 hover:text-primary-600 text-surface-600 transition-all"
              @click="inputMessage = q; sendMessage()"
            >
              {{ q }}
            </div>
          </div>
        </div>

        <!-- Message list -->
        <div
          v-for="(msg, idx) in currentSession?.messages || []"
          :key="idx"
          class="flex gap-3"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <!-- AI avatar -->
          <div v-if="msg.role === 'assistant'" class="flex-shrink-0">
            <div class="w-8 h-8 rounded-full bg-gradient-to-br from-primary-400 to-accent-500 flex items-center justify-center shadow-sm">
              <el-icon class="text-white text-sm"><Cpu /></el-icon>
            </div>
          </div>

          <!-- Message content -->
          <div
            class="max-w-[70%] rounded-2xl px-4 py-3"
            :class="msg.role === 'user'
              ? 'bg-gradient-to-br from-primary-500 to-accent-500 text-white shadow-md shadow-primary-500/20'
              : 'bg-white/50 border border-white/60 backdrop-blur-sm'"
          >
            <!-- Loading dots -->
            <div v-if="msg.loading && !msg.content" class="flex items-center gap-2">
              <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce"></span>
              <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 0.1s"></span>
              <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 0.2s"></span>
            </div>
            <div v-else class="markdown-body text-sm" :class="msg.role === 'user' ? 'text-white' : ''" v-html="renderMarkdown(msg.content)"></div>

            <!-- References -->
            <div v-if="msg.references?.length" class="mt-3 pt-3 border-t border-white/20">
              <p class="text-xs text-surface-400 mb-2">参考来源：</p>
              <div class="flex flex-wrap gap-1.5">
                <el-tag
                  v-for="ref in msg.references"
                  :key="ref.name"
                  size="small"
                  class="cursor-pointer hover:opacity-80 transition-opacity"
                  @click="jumpToKG(ref.name)"
                >
                  {{ ref.name }}（{{ ref.relType }}）
                </el-tag>
              </div>
            </div>

            <!-- Action buttons -->
            <div v-if="msg.role === 'assistant' && !msg.loading" class="flex items-center gap-2 mt-2 pt-2 border-t border-surface-200/30">
              <el-button text size="small" class="!text-surface-400 hover:!text-primary-500" @click="copyMessage(msg.content)">
                <el-icon class="mr-1"><CopyDocument /></el-icon> 复制
              </el-button>
            </div>
          </div>

          <!-- User avatar -->
          <div v-if="msg.role === 'user'" class="flex-shrink-0">
            <div class="w-8 h-8 rounded-full bg-gradient-to-br from-surface-300 to-surface-400 flex items-center justify-center">
              <el-icon class="text-white text-sm"><User /></el-icon>
            </div>
          </div>
        </div>
      </div>

      <!-- Input area -->
      <div class="border-t border-primary-500/5 p-4">
        <div class="flex items-end gap-3">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="2"
            placeholder="请输入您的健康问题..."
            resize="none"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <div class="flex flex-col gap-2">
            <el-button
              v-if="isGenerating"
              type="danger"
              icon="VideoPause"
              class="!rounded-xl"
              @click="stopGenerating"
            >
              停止
            </el-button>
            <el-button
              v-else
              type="primary"
              icon="Promotion"
              class="!rounded-xl"
              :disabled="!inputMessage.trim()"
              @click="sendMessage"
            >
              发送
            </el-button>
          </div>
        </div>
        <p class="text-xs text-surface-400 mt-2">
          提示：以上内容仅供参考，如有不适请及时就医
        </p>
      </div>
    </div>
  </div>
</template>
