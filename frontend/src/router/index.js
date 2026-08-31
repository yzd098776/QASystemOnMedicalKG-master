import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/auth/LoginView.vue'),
    meta: { requiresAuth: false, title: '登录' },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/auth/RegisterView.vue'),
    meta: { requiresAuth: false, title: '注册' },
  },
  {
    path: '/',
    component: () => import('@/components/layout/MainLayout.vue'),
    meta: { requiresAuth: true },
    redirect: '/kg',
    children: [
      {
        path: 'kg',
        name: 'KnowledgeGraph',
        component: () => import('@/views/kg/KnowledgeGraphView.vue'),
        meta: { title: '知识图谱' },
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/chat/ChatView.vue'),
        meta: { title: '智能问答' },
      },
      {
        path: 'diagnosis',
        name: 'Diagnosis',
        component: () => import('@/views/diagnosis/DiagnosisView.vue'),
        meta: { title: '疾病自查' },
      },
      {
        path: 'drug',
        name: 'DrugSafety',
        component: () => import('@/views/drug/DrugSafetyView.vue'),
        meta: { title: '用药安全' },
      },
      {
        path: 'health',
        name: 'HealthPlan',
        component: () => import('@/views/health/HealthPlanView.vue'),
        meta: { title: '健康计划' },
      },
      {
        path: 'guide',
        name: 'MedicalGuide',
        component: () => import('@/views/guide/MedicalGuideView.vue'),
        meta: { title: '就医指南' },
      },
      {
        path: 'wiki',
        name: 'Wiki',
        component: () => import('@/views/wiki/WikiView.vue'),
        meta: { title: '知识百科' },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/auth/ProfileView.vue'),
        meta: { title: '个人中心' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    redirect: '/kg',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth !== false && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/')
  } else {
    next()
  }
})

export default router
