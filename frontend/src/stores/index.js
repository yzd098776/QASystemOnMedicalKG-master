import { defineStore } from 'pinia'
import { ref, shallowRef, computed } from 'vue'
import request from '@/utils/request'

// 用户状态管理
export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem('token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')
  const userInfo = shallowRef(JSON.parse(localStorage.getItem('userInfo') || 'null'))
  const profile = shallowRef(JSON.parse(localStorage.getItem('profile') || 'null'))

  const isLoggedIn = computed(() => !!token.value)

  function setToken(newToken) {
    token.value = newToken
    localStorage.setItem('token', newToken)
  }

  function setRefreshToken(newRefreshToken) {
    refreshToken.value = newRefreshToken || ''
    if (newRefreshToken) {
      localStorage.setItem('refresh_token', newRefreshToken)
    } else {
      localStorage.removeItem('refresh_token')
    }
  }

  function setUserInfo(info) {
    userInfo.value = info
    localStorage.setItem('userInfo', JSON.stringify(info))
  }

  function setProfile(data) {
    profile.value = data
    localStorage.setItem('profile', JSON.stringify(data))
  }

  async function login(username, password) {
    const res = await request.post('/api/auth/login', { username, password })
    setToken(res.token)
    // 后端新增字段：7 天刷新令牌，用于 access token 过期后静默续期
    setRefreshToken(res.refresh_token)
    setUserInfo(res.user)
    return res
  }

  async function register(data) {
    return await request.post('/api/auth/register', data)
  }

  async function fetchProfile() {
    const res = await request.get('/api/profile/get')
    setProfile(res)
    return res
  }

  async function updateProfile(data) {
    const res = await request.post('/api/profile/update', data)
    setProfile(data)
    return res
  }

  async function logout() {
    // 先清本地再通知服务端，避免路由守卫竞态：
    // 若先 await 后端登出再清本地，调用方（MainLayout.handleLogout）未 await 就
    // router.push('/login')，此时 token 仍在 localStorage，路由守卫会把导航弹回首页。
    // 同步清空本地全部登录态（含 refresh_token）后，路由守卫立即看到已登出状态；
    // 同时保持调用方现有同步语义（调用返回时本地必定已清理完毕）
    const authHeader = token.value ? `Bearer ${token.value}` : ''
    token.value = ''
    refreshToken.value = ''
    userInfo.value = null
    profile.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('userInfo')
    localStorage.removeItem('profile')
    // 再通知后端登出（jti 黑名单 + token_version 自增）：不 await 阻塞调用方。
    // 本地已先清，请求拦截器取不到新 token，故显式携带登出前快照的 Authorization；
    // _skipAuthError 标记豁免统一 401 处理（旧会话过期时不弹「登录已过期」），
    // 清存储与跳转本就由本函数负责，无需拦截器兜底。失败仅静默，不弹错。
    request
      .post('/api/auth/logout', null, {
        _skipAuthError: true,
        headers: authHeader ? { Authorization: authHeader } : {},
      })
      .catch(() => {
        // 登出接口失败时本地状态已清理完毕，静默处理即可（不弹错）
      })
  }

  return {
    token, refreshToken, userInfo, profile, isLoggedIn,
    setToken, setRefreshToken, setUserInfo, setProfile,
    login, register, fetchProfile, updateProfile, logout,
  }
})
