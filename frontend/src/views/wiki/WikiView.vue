<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import request from '@/utils/request'
import { ElMessage } from 'element-plus'

const router = useRouter()
const loading = ref(false)
const activeCategory = ref('Disease')
const searchQuery = ref('')
const entityList = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const selectedEntity = ref(null)
const entityDrawer = ref(false)
const dailyTip = ref(null)

const categories = [
  { type: 'Disease', label: '疾病', icon: 'FirstAidKit', color: '#e74c3c' },
  { type: 'Drug', label: '药品', icon: 'FirstAidKit', color: '#3498db' },
  { type: 'Symptom', label: '症状', icon: 'Warning', color: '#2ecc71' },
  { type: 'Food', label: '食物', icon: 'Bowl', color: '#f39c12' },
  { type: 'Check', label: '检查', icon: 'Monitor', color: '#9b59b6' },
  { type: 'Department', label: '科室', icon: 'OfficeBuilding', color: '#e67e22' },
]

const activeCategoryObj = computed(() => categories.find(c => c.type === activeCategory.value))
const activeCategoryColor = computed(() => activeCategoryObj.value?.color || '#95a5a6')

onMounted(() => {
  loadEntities()
  loadDailyTip()
})

async function loadEntities() {
  loading.value = true
  try {
    const res = await request.get('/api/kg/entities', {
      params: {
        type: activeCategory.value,
        search: searchQuery.value,
        page: currentPage.value,
        limit: pageSize.value,
      },
    })
    entityList.value = res.nodes || []
    total.value = res.total || 0
  } catch {
    entityList.value = []
  } finally {
    loading.value = false
  }
}

async function loadDailyTip() {
  try {
    const res = await request.get('/api/wiki/daily-tip')
    dailyTip.value = res
  } catch {
    dailyTip.value = {
      title: '感冒的预防与治疗',
      content: '感冒是最常见的呼吸道疾病，由病毒引起。预防措施包括：勤洗手、避免接触患者、增强免疫力。治疗以对症治疗为主，注意休息和多饮水。',
      category: 'Disease',
    }
  }
}

async function showEntityDetail(entity) {
  loading.value = true
  try {
    const res = await request.get(`/api/kg/entity/${encodeURIComponent(entity.name)}`)
    selectedEntity.value = res
  } catch {
    selectedEntity.value = { name: entity.name, label: entity.label || activeCategory.value, properties: {} }
  } finally {
    loading.value = false
    entityDrawer.value = true
  }
}

function goToKG(entityName) {
  router.push({ path: '/kg', query: { entity: entityName } })
}

const labelNames = { Disease: '疾病', Drug: '药品', Symptom: '症状', Food: '食物', Check: '检查', Department: '科室' }
function consultAI(entity) {
  const prompts = {
    Disease: `请详细介绍一下${entity.name}这种疾病，包括病因、症状、预防措施和治疗方法`,
    Drug: `请介绍一下${entity.name}这种药品的功效、用法用量、副作用和注意事项`,
    Symptom: `我出现了${entity.name}的症状，可能是什么原因导致的？需要注意什么？`,
    Food: `请介绍一下${entity.name}的营养价值和饮食建议`,
    Check: `请介绍一下${entity.name}这项检查的用途、流程和注意事项`,
    Department: `${entity.name}主要诊治哪些疾病？什么情况下应该挂这个科室？`,
  }
  const q = prompts[entity.label] || `请介绍一下${entity.name}的相关信息`
  router.push({ path: '/chat', query: { q } })
}

function handleCategoryChange() {
  currentPage.value = 1
  searchQuery.value = ''
  loadEntities()
}

function handleSearch() {
  currentPage.value = 1
  loadEntities()
}

function handlePageChange(page) {
  currentPage.value = page
  loadEntities()
}
</script>

<template>
  <div class="max-w-6xl mx-auto space-y-5">
    <!-- Daily health tip -->
    <div v-if="dailyTip" class="glass-card overflow-hidden border-l-4 border-l-primary-500">
      <div class="p-5">
        <div class="flex items-start gap-4">
          <div class="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-500/10 to-accent-500/10 flex items-center justify-center flex-shrink-0 animate-float-y">
            <el-icon class="gradient-text" :size="24"><Sunny /></el-icon>
          </div>
          <div class="flex-1">
            <h3 class="text-base font-bold font-display text-surface-800 mb-1">每日健康科普</h3>
            <h4 class="gradient-text font-bold mb-2 text-sm">{{ dailyTip.title }}</h4>
            <p class="text-sm text-surface-500 leading-relaxed">{{ dailyTip.content }}</p>
            <div class="flex items-center gap-3 mt-3">
              <el-button text size="small" type="primary">
                <el-icon class="mr-1"><Star /></el-icon> 收藏
              </el-button>
              <el-button text size="small" class="!text-surface-400">
                <el-icon class="mr-1"><Share /></el-icon> 分享
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Main content -->
    <div class="glass-card overflow-hidden">
      <div class="flex items-center justify-between px-5 py-3.5 border-b border-primary-500/5">
        <span class="font-semibold font-display text-surface-800 text-sm">医疗知识百科</span>
        <el-input
          v-model="searchQuery"
          placeholder="搜索实体..."
          prefix-icon="Search"
          clearable
          class="!w-64"
          size="small"
          @input="handleSearch"
        />
      </div>

      <div class="p-5">
        <!-- Category filters -->
        <div class="flex flex-wrap gap-2 mb-5">
          <button
            v-for="cat in categories"
            :key="cat.type"
            class="flex items-center gap-1.5 px-4 py-2 rounded-pill text-sm font-semibold border transition-all duration-300 font-display"
            :class="activeCategory === cat.type
              ? 'bg-gradient-to-r from-primary-500 to-accent-500 text-white border-transparent shadow-md shadow-primary-500/20'
              : 'bg-white/50 border-white/40 text-surface-600 hover:bg-white/70 hover:shadow-sm backdrop-blur-sm'"
            @click="activeCategory = cat.type; handleCategoryChange()"
          >
            <el-icon :size="14" :style="{ color: activeCategory === cat.type ? '#fff' : cat.color }"><component :is="cat.icon" /></el-icon>
            {{ cat.label }}
          </button>
        </div>

        <!-- Entity grid -->
        <div v-loading="loading">
          <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            <div
              v-for="entity in entityList"
              :key="entity.name"
              class="border border-white/50 rounded-xl p-3 cursor-pointer transition-all duration-300 hover:-translate-y-1 hover:shadow-glass bg-white/30 backdrop-blur-sm group"
              @click="showEntityDetail(entity)"
            >
              <div class="flex items-center gap-2 mb-1">
                <span class="w-2.5 h-2.5 rounded-full flex-shrink-0 shadow-sm" :style="{ backgroundColor: activeCategoryColor }"></span>
                <span class="font-semibold font-display text-sm text-surface-700 group-hover:text-primary-500 transition-colors truncate">{{ entity.name }}</span>
              </div>
              <p v-if="entity.desc" class="text-xs text-surface-400 truncate">{{ entity.desc }}</p>
            </div>
          </div>

          <el-empty v-if="entityList.length === 0 && !loading" description="暂无数据" />
        </div>

        <!-- Pagination -->
        <div v-if="total > pageSize" class="flex justify-center mt-6">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next"
            @current-change="handlePageChange"
          />
        </div>
      </div>
    </div>

    <!-- Entity detail drawer -->
    <el-drawer v-model="entityDrawer" :title="selectedEntity?.name || '实体详情'" size="450px">
      <div v-if="selectedEntity" class="space-y-4">
        <div class="flex items-center gap-2 mb-4">
          <span
            class="px-2.5 py-0.5 rounded-pill text-xs font-medium text-white shadow-sm"
            :style="{ background: `linear-gradient(135deg, ${categories.find(c => c.type === selectedEntity.label)?.color || '#95a5a6'}, ${categories.find(c => c.type === selectedEntity.label)?.color || '#95a5a6'}dd)` }"
          >{{ categories.find(c => c.type === selectedEntity.label)?.label || selectedEntity.label }}</span>
          <span class="text-lg font-bold font-display text-surface-900">{{ selectedEntity.name }}</span>
        </div>

        <template v-if="selectedEntity.label === 'Disease'">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="简介">{{ selectedEntity.properties?.desc || '暂无' }}</el-descriptions-item>
            <el-descriptions-item label="病因">{{ selectedEntity.properties?.cause || '暂无' }}</el-descriptions-item>
            <el-descriptions-item label="预防">{{ selectedEntity.properties?.prevent || '暂无' }}</el-descriptions-item>
            <el-descriptions-item label="易感人群">{{ selectedEntity.properties?.easy_get || '暂无' }}</el-descriptions-item>
            <el-descriptions-item label="治疗周期">{{ selectedEntity.properties?.cure_lasttime || '暂无' }}</el-descriptions-item>
            <el-descriptions-item label="治愈概率">{{ selectedEntity.properties?.cured_prob || '暂无' }}</el-descriptions-item>
          </el-descriptions>

          <div v-if="selectedEntity.symptoms?.length" class="mt-3">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">相关症状</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="s in selectedEntity.symptoms" :key="s" type="success" effect="plain" size="small">{{ s }}</el-tag>
            </div>
          </div>

          <div v-if="selectedEntity.drugs?.length" class="mt-3">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">常用药品</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="d in selectedEntity.drugs" :key="d" type="primary" effect="plain" size="small">{{ d }}</el-tag>
            </div>
          </div>

          <div v-if="selectedEntity.foods?.length" class="mt-3">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">宜吃食物</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="f in selectedEntity.foods" :key="f" type="warning" effect="plain" size="small">{{ f }}</el-tag>
            </div>
          </div>
        </template>

        <template v-else>
          <el-descriptions :column="1" border>
            <el-descriptions-item v-for="(v, k) in selectedEntity.properties" :key="k" :label="k">{{ v }}</el-descriptions-item>
          </el-descriptions>
        </template>

        <div class="flex gap-2">
          <el-button type="primary" @click="goToKG(selectedEntity.name)">
            <el-icon class="mr-1"><Share /></el-icon> 在知识图谱中查看
          </el-button>
          <el-button @click="consultAI(selectedEntity)">
            <el-icon class="mr-1"><ChatDotRound /></el-icon> 咨询AI
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- Disclaimer -->
    <div class="flex items-start gap-3 p-4 glass-card text-sm text-surface-500">
      <el-icon class="mt-0.5 flex-shrink-0 text-surface-400"><InfoFilled /></el-icon>
      <p><strong>免责声明：</strong>以上医疗知识仅供参考，如有不适请及时就医。</p>
    </div>
  </div>
</template>
