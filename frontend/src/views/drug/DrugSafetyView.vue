<script setup>
import { ref, reactive } from 'vue'
import { useUserStore } from '@/stores/index'
import request from '@/utils/request'
import { ElMessage } from 'element-plus'

const userStore = useUserStore()
const activeTab = ref('drug')
const loading = ref(false)

const drugQuery = ref('')
const drugResult = ref(null)

const foodQuery = ref('')
const foodType = ref('food')
const foodResult = ref(null)

const selectedDrugs = ref([])
const drugSearchQuery = ref('')
const drugSearchResults = ref([])
const interactionResult = ref(null)

let drugSearchTimer = null
let foodSearchTimer = null
let interactionSearchTimer = null

function handleDrugSearch() {
  clearTimeout(drugSearchTimer)
  if (!drugQuery.value) return
  drugSearchTimer = setTimeout(async () => {
    loading.value = true
    try {
      const res = await request.get('/api/drug/contraindication', { params: { drug: drugQuery.value } })
      drugResult.value = res
    } catch {
      ElMessage.error('查询失败')
    } finally {
      loading.value = false
    }
  }, 300)
}

function handleFoodSearch() {
  clearTimeout(foodSearchTimer)
  if (!foodQuery.value) return
  foodSearchTimer = setTimeout(async () => {
    loading.value = true
    try {
      const res = await request.get('/api/food/contraindication', {
        params: { query: foodQuery.value, type: foodType.value },
      })
      foodResult.value = res
    } catch {
      ElMessage.error('查询失败')
    } finally {
      loading.value = false
    }
  }, 300)
}

function handleDrugInteractionSearch(query) {
  clearTimeout(interactionSearchTimer)
  if (!query) {
    drugSearchResults.value = []
    return
  }
  interactionSearchTimer = setTimeout(async () => {
    try {
      const res = await request.get('/api/kg/entities', { params: { search: query, type: 'Drug', limit: 10 } })
      drugSearchResults.value = res.nodes || []
    } catch {
      drugSearchResults.value = []
    }
  }, 300)
}

function addDrugForInteraction(drug) {
  if (!selectedDrugs.value.find(d => d.name === drug.name)) {
    selectedDrugs.value.push(drug)
  }
  drugSearchQuery.value = ''
  drugSearchResults.value = []
}

function removeDrug(idx) {
  selectedDrugs.value.splice(idx, 1)
}

async function checkInteraction() {
  if (selectedDrugs.value.length < 2) {
    ElMessage.warning('请至少选择两种药品')
    return
  }
  loading.value = true
  try {
    const res = await request.post('/api/drug/interaction', {
      drugs: selectedDrugs.value.map(d => d.name),
    })
    interactionResult.value = res
  } catch {
    ElMessage.error('查询失败')
  } finally {
    loading.value = false
  }
}

function hasAllergy(item) {
  if (!userStore.profile?.allergy_drug) return false
  return userStore.profile.allergy_drug.includes(item)
}

function hasDiseaseHistory(disease) {
  if (!userStore.profile?.medical_history) return false
  return userStore.profile.medical_history.includes(disease)
}

function getRiskGlow(risk) {
  if (risk === '高') return 'border-l-danger-500 shadow-[0_0_12px_rgba(244,63,94,0.1)]'
  if (risk === '中') return 'border-l-warning-500 shadow-[0_0_12px_rgba(245,158,11,0.1)]'
  return 'border-l-success-500 shadow-[0_0_12px_rgba(16,185,129,0.1)]'
}
</script>

<template>
  <div class="max-w-5xl mx-auto">
    <div class="glass-card overflow-hidden">
      <div class="card-header">
        <div class="card-header-icon bg-danger-500/10">
          <el-icon class="text-danger-500" :size="16"><Warning /></el-icon>
        </div>
        <span class="card-header-title">用药安全与禁忌查询</span>
      </div>

      <div class="p-5">
        <el-tabs v-model="activeTab">
          <!-- Drug contraindication -->
          <el-tab-pane label="药品禁忌查询" name="drug">
            <div class="space-y-4">
              <el-input
                v-model="drugQuery"
                placeholder="输入药品名称查询禁忌信息"
                prefix-icon="Search"
                size="large"
                clearable
                @input="handleDrugSearch"
              />

              <div v-if="drugResult" class="space-y-4 animate-elastic-in">
                <div class="flex items-center gap-3">
                  <div class="w-10 h-10 rounded-xl bg-primary-500/10 flex items-center justify-center">
                    <el-icon class="text-primary-500" :size="20"><InfoFilled /></el-icon>
                  </div>
                  <h3 class="text-lg font-bold font-display text-surface-900">{{ drugResult.name }}</h3>
                </div>

                <div v-if="hasAllergy(drugResult.name)" class="flex items-start gap-3 p-4 rounded-xl text-sm text-danger-700 border-l-4 border-l-danger-500" style="background: rgba(244,63,94,0.06);">
                  <el-icon class="mt-0.5 flex-shrink-0"><CircleCloseFilled /></el-icon>
                  <p><strong>个人禁忌提醒：</strong>根据您的健康档案，您对 {{ drugResult.name }} 过敏，请勿使用！</p>
                </div>

                <el-descriptions :column="1" border>
                  <el-descriptions-item label="主治疾病">{{ drugResult.disease || '暂无' }}</el-descriptions-item>
                  <el-descriptions-item label="禁忌人群">
                    <div v-if="drugResult.contra?.length" class="flex flex-wrap gap-1.5">
                      <el-tag v-for="c in drugResult.contra" :key="c" type="danger" effect="plain" size="small">{{ c }}</el-tag>
                    </div>
                    <span v-else class="text-surface-400">暂无数据</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="忌吃食物">
                    <div v-if="drugResult.noEat?.length" class="flex flex-wrap gap-1.5">
                      <el-tag v-for="f in drugResult.noEat" :key="f" type="warning" effect="plain" size="small">{{ f }}</el-tag>
                    </div>
                    <span v-else class="text-surface-400">暂无数据</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="在售厂商">{{ drugResult.producer || '暂无' }}</el-descriptions-item>
                </el-descriptions>
              </div>
            </div>
          </el-tab-pane>

          <!-- Food-disease contraindication -->
          <el-tab-pane label="食物-疾病禁忌查询" name="food">
            <div class="space-y-4">
              <div class="flex gap-4">
                <el-radio-group v-model="foodType">
                  <el-radio-button value="food">按食物查询</el-radio-button>
                  <el-radio-button value="disease">按疾病查询</el-radio-button>
                </el-radio-group>
                <el-input
                  v-model="foodQuery"
                  :placeholder="foodType === 'food' ? '输入食物名称' : '输入疾病名称'"
                  prefix-icon="Search"
                  size="large"
                  clearable
                  @input="handleFoodSearch"
                />
              </div>

              <div v-if="foodResult" class="animate-elastic-in">
                <h3 class="text-lg font-bold font-display text-surface-900 mb-4">{{ foodResult.name }}</h3>

                <template v-if="foodType === 'food'">
                  <div v-if="foodResult.diseases?.length" class="flex items-start gap-3 p-4 rounded-xl text-sm text-warning-700 mb-4 border-l-4 border-l-warning-500" style="background: rgba(245,158,11,0.06);">
                    <el-icon class="mt-0.5 flex-shrink-0"><WarningFilled /></el-icon>
                    <p>以下疾病患者不宜食用</p>
                  </div>
                  <div class="flex flex-wrap gap-2 mb-4">
                    <el-tag
                      v-for="d in foodResult.diseases"
                      :key="d"
                      type="danger"
                      effect="plain"
                      class="cursor-pointer"
                      :class="{ '!bg-danger-50 !border-danger-200': hasDiseaseHistory(d) }"
                    >
                      {{ d }}
                      <span v-if="hasDiseaseHistory(d)" class="ml-1 text-xs">您的病史</span>
                    </el-tag>
                  </div>
                </template>

                <template v-else>
                  <el-descriptions :column="1" border>
                    <el-descriptions-item label="宜吃食物">
                      <div class="flex flex-wrap gap-1.5">
                        <el-tag v-for="f in (foodResult.doEat || [])" :key="f" type="success" effect="plain" size="small">{{ f }}</el-tag>
                        <span v-if="!foodResult.doEat?.length" class="text-surface-400">暂无数据</span>
                      </div>
                    </el-descriptions-item>
                    <el-descriptions-item label="忌吃食物">
                      <div class="flex flex-wrap gap-1.5">
                        <el-tag v-for="f in (foodResult.noEat || [])" :key="f" type="danger" effect="plain" size="small">{{ f }}</el-tag>
                        <span v-if="!foodResult.noEat?.length" class="text-surface-400">暂无数据</span>
                      </div>
                    </el-descriptions-item>
                    <el-descriptions-item label="推荐食物">
                      <div class="flex flex-wrap gap-1.5">
                        <el-tag v-for="f in (foodResult.recommandEat || [])" :key="f" type="primary" effect="plain" size="small">{{ f }}</el-tag>
                        <span v-if="!foodResult.recommandEat?.length" class="text-surface-400">暂无数据</span>
                      </div>
                    </el-descriptions-item>
                  </el-descriptions>
                </template>
              </div>
            </div>
          </el-tab-pane>

          <!-- Drug interaction -->
          <el-tab-pane label="药物相互作用查询" name="interaction">
            <div class="space-y-4">
              <div class="relative">
                <el-input
                  v-model="drugSearchQuery"
                  placeholder="输入药品名称添加到查询列表"
                  prefix-icon="Search"
                  size="large"
                  clearable
                  @input="handleDrugInteractionSearch"
                />
                <div v-if="drugSearchResults.length > 0" class="absolute z-50 top-full left-0 w-full glass-card max-h-48 overflow-y-auto mt-1 animate-slide-up">
                  <div
                    v-for="d in drugSearchResults"
                    :key="d.name"
                    class="px-4 py-2.5 hover:bg-primary-500/5 cursor-pointer text-sm transition-colors"
                    @click="addDrugForInteraction(d)"
                  >
                    {{ d.name }}
                  </div>
                </div>
              </div>

              <div v-if="selectedDrugs.length > 0" class="animate-elastic-in">
                <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">已选药品：</h4>
                <div class="flex flex-wrap gap-2 mb-4">
                  <el-tag
                    v-for="(d, idx) in selectedDrugs"
                    :key="d.name"
                    closable
                    type="primary"
                    effect="dark"
                    @close="removeDrug(idx)"
                  >
                    {{ d.name }}
                  </el-tag>
                </div>
                <el-button type="primary" :loading="loading" @click="checkInteraction">
                  查询相互作用
                </el-button>
              </div>

              <div v-if="interactionResult" class="space-y-3 animate-elastic-in">
                <h4 class="text-sm font-semibold font-display text-surface-700">查询结果：</h4>
                <div v-if="interactionResult.interactions?.length">
                  <div
                    v-for="(item, idx) in interactionResult.interactions"
                    :key="idx"
                    class="border border-white/50 border-l-4 rounded-xl p-4 mb-3 transition-card backdrop-blur-sm"
                    :class="getRiskGlow(item.risk)"
                  >
                    <div class="flex items-center justify-between mb-2">
                      <span class="font-semibold font-display text-surface-800 text-sm">{{ item.drug1 }} + {{ item.drug2 }}</span>
                      <el-tag
                        :type="item.risk === '高' ? 'danger' : item.risk === '中' ? 'warning' : 'success'"
                        size="small"
                      >
                        {{ item.risk }}风险
                      </el-tag>
                    </div>
                    <p class="text-sm text-surface-500 leading-relaxed">{{ item.description }}</p>
                  </div>
                </div>
                <el-empty v-else description="未发现已知的药物相互作用" />
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- Disclaimer -->
    <div class="mt-4 flex items-start gap-3 p-4 glass-card text-sm text-surface-500">
      <el-icon class="mt-0.5 flex-shrink-0 text-surface-400"><InfoFilled /></el-icon>
      <p><strong>免责声明：</strong>以上用药信息仅供参考，具体用药请遵医嘱。如有不适请及时就医。</p>
    </div>
  </div>
</template>
