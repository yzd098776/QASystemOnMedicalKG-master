import { defineStore } from 'pinia'
import { ref, shallowRef, computed } from 'vue'
import request from '@/utils/request'

// 用户状态管理
export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem('token') || '')
  const userInfo = shallowRef(JSON.parse(localStorage.getItem('userInfo') || 'null'))
  const profile = shallowRef(JSON.parse(localStorage.getItem('profile') || 'null'))

  const isLoggedIn = computed(() => !!token.value)

  function setToken(newToken) {
    token.value = newToken
    localStorage.setItem('token', newToken)
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

  function logout() {
    token.value = ''
    userInfo.value = null
    profile.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('userInfo')
    localStorage.removeItem('profile')
  }

  return {
    token, userInfo, profile, isLoggedIn,
    setToken, setUserInfo, setProfile,
    login, register, fetchProfile, updateProfile, logout,
  }
})
