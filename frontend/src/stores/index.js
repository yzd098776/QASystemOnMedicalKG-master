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
    // 先通知后端登出（jti 黑名单 + token_version 自增，令牌即时失效），
    // 失败不阻断本地登出流程；随后清理本地全部登录态（含 refresh_token）
    try {
      await request.post('/api/auth/logout')
    } catch {
      // 登出接口失败时仅清理本地状态即可，静默处理（不弹错）
    }
    token.value = ''
    refreshToken.value = ''
    userInfo.value = null
    profile.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('userInfo')
    localStorage.removeItem('profile')
  }

  return {
    token, refreshToken, userInfo, profile, isLoggedIn,
    setToken, setRefreshToken, setUserInfo, setProfile,
    login, register, fetchProfile, updateProfile, logout,
  }
})
