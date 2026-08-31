<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import request from '@/utils/request'
import { ElMessage } from 'element-plus'

const router = useRouter()
const loading = ref(false)
const searchQuery = ref('')
const selectedSymptoms = ref([])
const results = ref([])

const symptomCategories = [
  {
    name: '呼吸系统',
    icon: 'WindPower',
    symptoms: ['咳嗽', '流鼻涕', '鼻塞', '打喷嚏', '喉咙痛', '气喘', '胸闷', '呼吸困难', '咳痰', '咯血'],
  },
  {
    name: '消化系统',
    icon: 'Bowl',
    symptoms: ['恶心', '呕吐', '腹泻', '腹痛', '腹胀', '便秘', '食欲不振', '反酸', '嗳气', '吞咽困难'],
  },
  {
    name: '神经系统',
    icon: 'Monitor',
    symptoms: ['头痛', '头晕', '失眠', '记忆力减退', '手抖', '抽搐', '麻木', '意识模糊', '耳鸣', '视物模糊'],
  },
  {
    name: '心血管系统',
    icon: 'Star',
    symptoms: ['心悸', '胸痛', '血压升高', '血压降低', '水肿', '紫绀', '心律不齐'],
  },
  {
    name: '运动系统',
    icon: 'Position',
    symptoms: ['关节疼痛', '肌肉酸痛', '腰痛', '颈椎痛', '膝盖痛', '骨折', '扭伤', '肢体无力'],
  },
  {
    name: '皮肤系统',
    icon: 'MagicStick',
    symptoms: ['皮疹', '瘙痒', '红肿', '脱皮', '水泡', '荨麻疹', '痤疮', '脱发'],
  },
  {
    name: '泌尿系统',
    icon: 'Coin',
    symptoms: ['尿频', '尿急', '尿痛', '血尿', '尿失禁', '夜尿增多'],
  },
  {
    name: '全身症状',
    icon: 'Sunny',
    symptoms: ['发烧', '乏力', '体重下降', '盗汗', '畏寒', '疲劳', '发热'],
  },
]

const allSymptoms = computed(() =>
  symptomCategories.flatMap(c => c.symptoms)
)

const filteredSymptoms = computed(() => {
  if (!searchQuery.value) return []
  const q = searchQuery.value.toLowerCase()
  return allSymptoms.value.filter(s => s.toLowerCase().includes(q))
})

const selectedSymptomSet = computed(() => new Set(selectedSymptoms.value.map(s => s.name)))

function addSymptom(symptom) {
  if (!selectedSymptoms.value.find(s => s.name === symptom)) {
    selectedSymptoms.value.push({
      name: symptom,
      duration: '',
      severity: '中等',
    })
  }
  searchQuery.value = ''
}

function removeSymptom(index) {
  selectedSymptoms.value.splice(index, 1)
}

async function handleDiagnosis() {
  if (selectedSymptoms.value.length === 0) {
    ElMessage.warning('请至少选择一个症状')
    return
  }
  loading.value = true
  try {
    const res = await request.post('/api/diagnosis', {
      symptoms: selectedSymptoms.value.map(s => s.name),
    })
    results.value = res.results || []
    if (results.value.length === 0) {
      ElMessage.info('未找到匹配的疾病，请尝试添加更多症状')
    }
  } catch {
    ElMessage.error('诊断请求失败，请稍后再试')
  } finally {
    loading.value = false
  }
}

function goToKG(diseaseName) {
  router.push({ path: '/kg', query: { entity: diseaseName } })
}

function consultAI(disease) {
  const symptoms = selectedSymptoms.value.map(s => s.name).join('、')
  router.push({ path: '/chat', query: { q: `我有${symptoms}的症状，可能是${disease.name}吗？请帮我分析一下` } })
}

function getSeverityColor(severity) {
  if (severity === '严重') return 'bg-danger-500'
  if (severity === '中等') return 'bg-warning-500'
  return 'bg-success-500'
}
</script>

<template>
  <div class="max-w-6xl mx-auto space-y-5">
    <!-- Disclaimer -->
    <div class="flex items-start gap-3 p-4 glass-card border-l-4 border-l-warning-500 text-sm text-warning-700" style="background: rgba(245,158,11,0.06);">
      <el-icon class="mt-0.5 flex-shrink-0"><Warning /></el-icon>
      <p><strong>免责声明：</strong>本工具仅供参考，不能替代专业医生诊断。如有严重症状，请立即就医。</p>
    </div>

    <el-row :gutter="24">
      <!-- Left: Symptom selection -->
      <el-col :span="10">
        <div class="glass-card overflow-hidden">
          <div class="card-header">
            <div class="card-header-icon bg-primary-500/10">
              <el-icon class="text-primary-500" :size="16"><FirstAidKit /></el-icon>
            </div>
            <span class="card-header-title">选择症状</span>
          </div>
          <div class="p-4">
            <!-- Search -->
            <div class="relative mb-4">
              <el-input
                v-model="searchQuery"
                placeholder="搜索症状..."
                prefix-icon="Search"
                clearable
              />
              <div v-if="filteredSymptoms.length > 0" class="absolute z-50 top-full left-0 w-full glass-card max-h-48 overflow-y-auto mt-1 animate-slide-up">
                <div
                  v-for="s in filteredSymptoms"
                  :key="s"
                  class="px-4 py-2.5 hover:bg-primary-500/5 cursor-pointer text-sm transition-colors"
                  @click="addSymptom(s)"
                >
                  {{ s }}
                </div>
              </div>
            </div>

            <!-- Symptom categories -->
            <el-collapse>
              <el-collapse-item v-for="cat in symptomCategories" :key="cat.name" :name="cat.name">
                <template #title>
                  <div class="flex items-center gap-2">
                    <el-icon class="text-primary-500"><component :is="cat.icon" /></el-icon>
                    <span class="text-sm font-semibold font-display">{{ cat.name }}</span>
                    <span class="text-xs text-surface-400 bg-surface-100 px-1.5 py-0.5 rounded-pill">{{ cat.symptoms.length }}</span>
                  </div>
                </template>
                <div class="flex flex-wrap gap-1.5">
                  <el-tag
                    v-for="s in cat.symptoms"
                    :key="s"
                    :type="selectedSymptomSet.has(s) ? '' : 'info'"
                    :effect="selectedSymptomSet.has(s) ? 'dark' : 'plain'"
                    class="cursor-pointer !transition-all"
                    @click="addSymptom(s)"
                  >
                    {{ s }}
                  </el-tag>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
      </el-col>

      <!-- Right: Selected symptoms and results -->
      <el-col :span="14">
        <!-- Selected symptoms -->
        <div class="glass-card overflow-hidden mb-4">
          <div class="flex items-center justify-between px-5 py-3.5 border-b border-primary-500/5">
            <span class="font-semibold font-display text-surface-800 text-sm">已选症状 ({{ selectedSymptoms.length }})</span>
            <el-button type="primary" size="small" :loading="loading" @click="handleDiagnosis" :disabled="selectedSymptoms.length === 0">
              开始诊断
            </el-button>
          </div>
          <div class="p-4">
            <div v-if="selectedSymptoms.length === 0" class="text-center text-surface-400 py-8">
              <el-icon class="text-4xl mb-2"><InfoFilled /></el-icon>
              <p class="text-sm">请从左侧选择您的症状</p>
            </div>

            <div v-else class="space-y-2">
              <div
                v-for="(s, idx) in selectedSymptoms"
                :key="idx"
                class="flex items-center gap-3 bg-white/40 rounded-xl p-3 border border-white/50 backdrop-blur-sm relative overflow-hidden"
              >
                <div :class="['absolute left-0 top-0 bottom-0 w-1 rounded-l-xl', getSeverityColor(s.severity)]"></div>
                <el-tag closable @close="removeSymptom(idx)" type="primary" effect="dark" size="small" class="ml-2">
                  {{ s.name }}
                </el-tag>
                <el-select v-model="s.duration" placeholder="持续时间" size="small" class="!w-28">
                  <el-option label="1天内" value="1天内" />
                  <el-option label="3天内" value="3天内" />
                  <el-option label="1周内" value="1周内" />
                  <el-option label="1月内" value="1月内" />
                  <el-option label="1月以上" value="1月以上" />
                </el-select>
                <el-select v-model="s.severity" placeholder="严重程度" size="small" class="!w-24">
                  <el-option label="轻微" value="轻微" />
                  <el-option label="中等" value="中等" />
                  <el-option label="严重" value="严重" />
                </el-select>
              </div>
            </div>
          </div>
        </div>

        <!-- Diagnosis results -->
        <div v-if="results.length > 0" class="glass-card overflow-hidden">
          <div class="px-5 py-3.5 border-b border-primary-500/5">
            <span class="font-semibold font-display text-surface-800 text-sm">诊断结果（共 {{ results.length }} 种可能疾病）</span>
          </div>
          <div class="p-4 space-y-3">
            <div
              v-for="(r, idx) in results"
              :key="idx"
              class="border border-white/50 rounded-xl p-4 transition-card hover:border-primary-200/50 stagger-item bg-white/30 backdrop-blur-sm"
            >
              <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-3">
                  <span class="text-2xl font-bold gradient-text font-mono">{{ idx + 1 }}</span>
                  <div>
                    <h4 class="text-base font-bold font-display text-surface-800 cursor-pointer hover:text-primary-500 transition-colors" @click="goToKG(r.name)">
                      {{ r.name }}
                    </h4>
                    <div class="flex items-center gap-2 mt-1">
                      <el-progress
                        :percentage="r.probability"
                        :stroke-width="4"
                        :show-text="false"
                        :color="r.probability > 70 ? '#F43F5E' : r.probability > 40 ? '#F59E0B' : '#10B981'"
                        class="!w-32"
                      />
                      <span class="text-xs font-bold font-mono" :class="r.probability > 70 ? 'text-danger-500' : r.probability > 40 ? 'text-warning-500' : 'text-success-500'">
                        {{ r.probability }}%
                      </span>
                    </div>
                  </div>
                </div>
                <el-button type="primary" size="small" plain @click="consultAI(r)">
                  <el-icon class="mr-1"><ChatDotRound /></el-icon> 咨询AI
                </el-button>
              </div>

              <p class="text-sm text-surface-500 mb-3 leading-relaxed">{{ r.desc || '暂无简介' }}</p>

              <div class="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span class="text-surface-400 text-xs font-medium">匹配症状：</span>
                  <div class="flex flex-wrap gap-1 mt-1">
                    <el-tag v-for="s in r.matchedSymptoms" :key="s" size="small" type="success" effect="plain">{{ s }}</el-tag>
                  </div>
                </div>
                <div>
                  <span class="text-surface-400 text-xs font-medium">建议检查：</span>
                  <div class="flex flex-wrap gap-1 mt-1">
                    <el-tag v-for="c in (r.checks || []).slice(0, 3)" :key="c" size="small" type="info" effect="plain">{{ c }}</el-tag>
                  </div>
                </div>
                <div>
                  <span class="text-surface-400 text-xs font-medium">推荐科室：</span>
                  <el-tag size="small" type="warning" effect="plain">{{ r.department || '暂无' }}</el-tag>
                </div>
                <div>
                  <span class="text-surface-400 text-xs font-medium">症状匹配度：</span>
                  <span class="font-bold font-mono text-surface-700">{{ r.matchedCount }}/{{ selectedSymptoms.length }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>
