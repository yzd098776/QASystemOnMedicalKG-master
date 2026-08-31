<script setup>
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/index'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const menuItems = [
  { path: '/kg', icon: 'Share', title: '知识图谱' },
  { path: '/chat', icon: 'ChatDotRound', title: '智能问答' },
  { path: '/diagnosis', icon: 'FirstAidKit', title: '疾病自查' },
  { path: '/drug', icon: 'Warning', title: '用药安全' },
  { path: '/health', icon: 'Calendar', title: '健康计划' },
  { path: '/guide', icon: 'Guide', title: '就医指南' },
  { path: '/wiki', icon: 'Collection', title: '知识百科' },
]

const activeMenu = computed(() => route.path)

function handleLogout() {
  userStore.logout()
  router.push('/login')
}
</script>

<template>
  <div class="min-h-screen flex flex-col">
    <!-- Floating capsule navigation -->
    <header class="sticky top-3 z-50 flex justify-center px-4 pointer-events-none">
      <nav class="flex items-center gap-1 px-2 py-1.5 rounded-pill bg-white/60 backdrop-blur-xl border border-white/40 shadow-nav pointer-events-auto max-w-[960px] w-full">
        <!-- Logo -->
        <div class="flex items-center gap-2 px-3 py-1 mr-1 cursor-pointer" @click="router.push('/kg')">
          <div class="w-8 h-8 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center shadow-md">
            <el-icon class="text-white" :size="16"><Share /></el-icon>
          </div>
          <span class="hidden lg:block text-sm font-bold font-display gradient-text tracking-tight">MedGraph</span>
        </div>

        <!-- Divider -->
        <div class="w-px h-6 bg-surface-200/50 mx-1"></div>

        <!-- Nav items -->
        <router-link
          v-for="item in menuItems"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-1.5 px-3 py-2 rounded-pill text-xs font-medium transition-all duration-300 whitespace-nowrap"
          :class="activeMenu === item.path
            ? 'bg-gradient-to-r from-primary-500 to-accent-500 text-white shadow-md shadow-primary-500/20'
            : 'text-surface-600 hover:text-primary-600 hover:bg-primary-50/50'"
        >
          <el-icon :size="15"><component :is="item.icon" /></el-icon>
          <span class="hidden sm:inline">{{ item.title }}</span>
        </router-link>

        <!-- Spacer -->
        <div class="flex-1"></div>

        <!-- User -->
        <el-dropdown trigger="click">
          <div class="flex items-center gap-2 cursor-pointer px-3 py-1.5 rounded-pill hover:bg-primary-50/50 transition-colors">
            <div class="w-7 h-7 rounded-full bg-gradient-to-br from-primary-400 to-accent-500 flex items-center justify-center text-white text-xs font-bold shadow-sm">
              {{ userStore.userInfo?.username?.[0]?.toUpperCase() || 'U' }}
            </div>
            <span class="hidden md:inline text-xs font-medium text-surface-600">{{ userStore.userInfo?.username || '用户' }}</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="router.push('/profile')">
                <el-icon><User /></el-icon> 个人中心
              </el-dropdown-item>
              <el-dropdown-item divided @click="handleLogout">
                <el-icon><SwitchButton /></el-icon> 退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </nav>
    </header>

    <!-- Content area with perspective -->
    <main class="flex-1 px-4 sm:px-6 lg:px-8 pb-8 pt-4" style="perspective: 1200px;">
      <router-view v-slot="{ Component }">
        <transition name="page-spatial" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>
