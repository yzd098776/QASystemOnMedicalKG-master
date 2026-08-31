import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '',
  timeout: 30000,
})

// 请求拦截器 - 添加JWT令牌
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// ========== 令牌刷新（单飞锁：并发 401 共享同一个刷新请求） ==========
let refreshPromise = null

/**
 * 清除本地登录态并跳转登录页
 */
function clearAuthAndRedirect(message = '登录已过期，请重新登录') {
  localStorage.removeItem('token')
  localStorage.removeItem('refresh_token')
  localStorage.removeItem('userInfo')
  localStorage.removeItem('profile')
  window.location.href = '/login'
  ElMessage.error(message)
}

/**
 * 使用 refresh_token 换取新的令牌对（裸 axios 调用，避免拦截器递归）。
 * 模块级单飞锁：多个并发 401 共享同一个刷新 Promise。
 * 供 request.js 拦截器与 ChatView 的 SSE 请求复用。
 */
export function refreshAuth() {
  if (refreshPromise) return refreshPromise
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) {
    return Promise.reject(new Error('无刷新令牌'))
  }
  refreshPromise = axios
    .post('/api/auth/refresh', { refresh_token: refreshToken })
    .then((res) => {
      const data = res.data || {}
      if (data.token) localStorage.setItem('token', data.token)
      if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token)
      return data
    })
    .finally(() => {
      refreshPromise = null
    })
  return refreshPromise
}

// 响应拦截器 - 统一错误处理 + 401 自动刷新重放
request.interceptors.response.use(
  (response) => {
    return response.data
  },
  async (error) => {
    if (error.response) {
      const { status, data } = error.response
      const original = error.config || {}
      const url = original.url || ''

      switch (status) {
        case 401: {
          // 豁免标记（如登出请求）：调用方自行负责清存储与跳转，
          // 这里静默拒绝即可，不触发刷新/清理/提示，避免主动登出时弹「登录已过期」
          if (original._skipAuthError) {
            break
          }
          // 登录请求的 401（如密码错误）：展示后端 detail，不触发刷新也不跳转，
          // 避免把「用户名或密码错误」误报为「登录已过期」
          if (url.includes('/api/auth/login')) {
            ElMessage.error(data?.detail || '用户名或密码错误')
            break
          }
          // 刷新请求自身的 401 不得再触发刷新（防死循环）
          const isRefreshCall = url.includes('/api/auth/refresh')
          const hasRefreshToken = !!localStorage.getItem('refresh_token')
          if (!original._retried && !isRefreshCall && hasRefreshToken) {
            try {
              await refreshAuth()
              // 刷新成功：用新令牌重放原请求（仅重试一次）
              original._retried = true
              original.headers = original.headers || {}
              original.headers.Authorization = `Bearer ${localStorage.getItem('token')}`
              return request(original)
            } catch {
              // 刷新失败：落入下方清理登录态逻辑
            }
          }
          clearAuthAndRedirect()
          break
        }
        case 403:
          ElMessage.error('没有权限执行此操作')
          break
        case 404:
          ElMessage.error('请求的资源不存在')
          break
        case 422:
          ElMessage.error(data.detail || '输入参数有误')
          break
        case 429:
          ElMessage.error(data?.detail || '操作过于频繁，请稍后再试')
          break
        case 500:
          ElMessage.error('服务器内部错误，请稍后再试')
          break
        default:
          ElMessage.error(data.detail || '请求失败')
      }
    } else if (error.message.includes('timeout')) {
      ElMessage.error('请求超时，请稍后再试')
    } else {
      ElMessage.error('网络错误，请检查网络连接')
    }
    return Promise.reject(error)
  }
)

export default request
