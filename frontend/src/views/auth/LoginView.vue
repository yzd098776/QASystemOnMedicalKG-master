<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/index'
import { ElMessage } from 'element-plus'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const formRef = ref(null)

const form = reactive({
  username: '',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  try {
    await userStore.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push('/')
  } catch (err) {
    // error handled by interceptor
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center p-4 relative overflow-hidden">
    <!-- Background decoration -->
    <div class="absolute inset-0 pointer-events-none">
      <div class="absolute top-[-10%] right-[-5%] w-[500px] h-[500px] rounded-full bg-gradient-to-br from-primary-300/20 to-accent-300/10 blur-3xl"></div>
      <div class="absolute bottom-[-10%] left-[-5%] w-[400px] h-[400px] rounded-full bg-gradient-to-tr from-accent-300/15 to-primary-300/10 blur-3xl"></div>
    </div>

    <div class="w-full max-w-md relative z-10">
      <!-- Header -->
      <div class="text-center mb-10">
        <div class="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-primary-500 to-accent-500 rounded-2xl mb-5 shadow-glow animate-float-y">
          <el-icon class="text-white" :size="30"><Share /></el-icon>
        </div>
        <h1 class="text-2xl font-bold font-display text-surface-900 tracking-tight">医疗知识图谱智能问答系统</h1>
        <p class="text-surface-400 mt-2 text-sm">基于 4.4 万实体 + 30 万关系的医疗知识库</p>
      </div>

      <!-- Login card -->
      <div class="float-card p-8">
        <h2 class="text-lg font-bold font-display text-center mb-8 gradient-text">用户登录</h2>
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="handleLogin">
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" placeholder="请输入用户名" prefix-icon="User" size="large" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input v-model="form.password" type="password" placeholder="请输入密码" prefix-icon="Lock" size="large" show-password />
          </el-form-item>
          <el-form-item>
            <el-button
              size="large"
              class="w-full !rounded-xl !h-11 !text-sm !font-semibold"
              :loading="loading"
              @click="handleLogin"
            >
              登录
            </el-button>
          </el-form-item>
        </el-form>
        <div class="text-center text-sm text-surface-400">
          还没有账号？
          <router-link to="/register" class="gradient-text font-semibold hover:opacity-80 transition-opacity">立即注册</router-link>
        </div>
      </div>

      <!-- Disclaimer -->
      <p class="text-center text-xs text-surface-400 mt-8">
        免责声明：本系统仅供参考，不能替代专业医生诊断
      </p>
    </div>
  </div>
</template>
