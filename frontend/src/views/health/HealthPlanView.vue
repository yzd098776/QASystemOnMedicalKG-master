<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useUserStore } from '@/stores/index'
import request from '@/utils/request'
import { ElMessage, ElMessageBox } from 'element-plus'

const userStore = useUserStore()
const activeTab = ref('prevention')
const loading = ref(false)

const preventionPlan = ref(null)

const chronicDiseases = ['高血压', '糖尿病', '冠心病', '慢性支气管炎', '关节炎', '痛风', '胃炎', '哮喘']
const selectedChronic = ref('高血压')
const customDisease = ref('')
const chronicPlan = ref(null)

const healthRecords = ref([])
const recordForm = reactive({
  date: new Date().toISOString().split('T')[0],
  weight: null,
  bloodPressureHigh: null,
  bloodPressureLow: null,
  bloodSugar: null,
  heartRate: null,
  note: '',
})
const recordDialogVisible = ref(false)

const savedPlans = ref([])

const profileSummary = computed(() => {
  const p = userStore.profile
  if (!p) return '暂无健康档案'
  const parts = []
  if (p.age) parts.push(`${p.age}岁`)
  if (p.gender) parts.push(p.gender)
  if (p.weight) parts.push(`${p.weight}kg`)
  if (p.height) parts.push(`${p.height}cm`)
  return parts.join(' | ') || '暂无健康档案'
})

onMounted(async () => {
  await loadHealthRecords()
  await loadPlans()
})

async function generatePreventionPlan() {
  loading.value = true
  try {
    const res = await request.post('/api/health/prevention', {
      profile: userStore.profile,
    })
    preventionPlan.value = res
    await savePlan('prevention', res)
  } catch {
    ElMessage.error('生成预防计划失败')
  } finally {
    loading.value = false
  }
}

async function generateChronicPlan() {
  const disease = customDisease.value.trim() || selectedChronic.value
  if (!disease) {
    ElMessage.warning('请输入或选择疾病名称')
    return
  }
  loading.value = true
  try {
    const res = await request.post('/api/health/chronic', {
      disease,
      profile: userStore.profile,
    })
    chronicPlan.value = res
    await savePlan('chronic', res, disease)
  } catch {
    ElMessage.error('生成管理计划失败')
  } finally {
    loading.value = false
  }
}

async function loadHealthRecords() {
  try {
    const res = await request.get('/api/health/records')
    healthRecords.value = res.records || []
  } catch {
    healthRecords.value = []
  }
}

async function saveRecord() {
  loading.value = true
  try {
    await request.post('/api/health/records', recordForm)
    ElMessage.success('记录已保存')
    recordDialogVisible.value = false
    await loadHealthRecords()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    loading.value = false
  }
}

async function deleteRecord(id, date) {
  try {
    await ElMessageBox.confirm(`确定要删除 ${date || ''} 的记录吗？`, '删除记录', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await request.delete(`/api/health/records/${encodeURIComponent(id)}`)
    ElMessage.success('记录已删除')
    await loadHealthRecords()
  } catch {}
}

function openRecordDialog() {
  Object.assign(recordForm, {
    date: new Date().toISOString().split('T')[0],
    weight: null,
    bloodPressureHigh: null,
    bloodPressureLow: null,
    bloodSugar: null,
    heartRate: null,
    note: '',
  })
  recordDialogVisible.value = true
}

async function loadPlans() {
  try {
    const res = await request.get('/api/health/plans')
    savedPlans.value = res.plans || []
  } catch {
    savedPlans.value = []
  }
}

async function savePlan(type, data, disease) {
  try {
    await request.post('/api/health/plans', { type, data, disease: disease || '' })
    await loadPlans()
  } catch {}
}

async function clearPlans() {
  try {
    await ElMessageBox.confirm('确定要清除所有历史计划吗？此操作不可恢复。', '清除计划', {
      confirmButtonText: '确定清除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await request.delete('/api/health/plans')
    savedPlans.value = []
    ElMessage.success('所有计划已清空')
  } catch {}
}

function loadPlanIntoView(plan) {
  if (plan.type === 'prevention') {
    preventionPlan.value = plan.data
    activeTab.value = 'prevention'
  } else if (plan.type === 'chronic') {
    chronicPlan.value = plan.data
    activeTab.value = 'chronic'
  }
}
</script>

<template>
  <div class="max-w-5xl mx-auto">
    <div class="glass-card overflow-hidden">
      <div class="flex items-center justify-between px-5 py-3.5 border-b border-primary-500/5">
        <div class="flex items-center gap-2">
          <div class="card-header-icon bg-success-500/10">
            <el-icon class="text-success-500" :size="16"><Calendar /></el-icon>
          </div>
          <span class="card-header-title">个性化健康管理计划</span>
        </div>
        <span class="text-xs text-surface-400 bg-primary-500/5 px-3 py-1 rounded-pill font-medium">{{ profileSummary }}</span>
      </div>

      <div class="p-5">
        <el-tabs v-model="activeTab">
          <!-- Disease prevention -->
          <el-tab-pane label="疾病预防计划" name="prevention">
            <div class="space-y-4">
              <div class="flex items-start gap-3 p-4 rounded-xl text-sm text-primary-700 border-l-4 border-l-primary-500" style="background: rgba(79,70,229,0.04);">
                <el-icon class="mt-0.5 flex-shrink-0"><InfoFilled /></el-icon>
                <p>基于您的年龄、性别、家族病史等信息，为您推荐个性化的疾病预防方案。</p>
              </div>
              <el-button type="primary" :loading="loading" @click="generatePreventionPlan">
                生成我的预防计划
              </el-button>

              <div v-if="preventionPlan" class="space-y-4 animate-elastic-in">
                <el-collapse>
                  <el-collapse-item v-for="(item, idx) in preventionPlan.items" :key="idx" :title="item.disease" :name="idx">
                    <div class="space-y-3">
                      <p class="text-sm text-surface-500">{{ item.reason }}</p>
                      <h5 class="font-semibold text-sm font-display text-surface-700">预防措施：</h5>
                      <ul class="list-disc list-inside text-sm text-surface-500 space-y-1">
                        <li v-for="(m, i) in item.measures" :key="i">{{ m }}</li>
                      </ul>
                    </div>
                  </el-collapse-item>
                </el-collapse>

                <div v-if="preventionPlan.dailyTips" class="p-5 rounded-xl border border-success-200/30" style="background: linear-gradient(135deg, rgba(16,185,129,0.06) 0%, rgba(16,185,129,0.02) 100%);">
                  <h4 class="font-bold font-display text-success-700 mb-4 text-sm">每日健康提醒</h4>
                  <div class="grid grid-cols-3 gap-4 text-sm">
                    <div class="bg-white/40 backdrop-blur-sm rounded-xl p-3 border border-white/50">
                      <el-icon class="text-success-500 mb-1"><Bowl /></el-icon>
                      <p class="font-semibold font-display text-surface-700">饮食建议</p>
                      <p class="text-surface-500 mt-1">{{ preventionPlan.dailyTips.diet }}</p>
                    </div>
                    <div class="bg-white/40 backdrop-blur-sm rounded-xl p-3 border border-white/50">
                      <el-icon class="text-success-500 mb-1"><Position /></el-icon>
                      <p class="font-semibold font-display text-surface-700">运动建议</p>
                      <p class="text-surface-500 mt-1">{{ preventionPlan.dailyTips.exercise }}</p>
                    </div>
                    <div class="bg-white/40 backdrop-blur-sm rounded-xl p-3 border border-white/50">
                      <el-icon class="text-success-500 mb-1"><Moon /></el-icon>
                      <p class="font-semibold font-display text-surface-700">作息建议</p>
                      <p class="text-surface-500 mt-1">{{ preventionPlan.dailyTips.rest }}</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- Chronic disease management -->
          <el-tab-pane label="慢性病管理" name="chronic">
            <div class="space-y-4">
              <div class="space-y-3">
                <div class="flex items-center gap-3">
                  <span class="text-sm text-surface-500 font-medium whitespace-nowrap">输入疾病：</span>
                  <el-input v-model="customDisease" placeholder="输入任意疾病名称，如：甲亢、抑郁症..." clearable class="!w-72" />
                  <el-button type="primary" :loading="loading" @click="generateChronicPlan">生成管理计划</el-button>
                </div>
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-xs text-surface-400">快速选择：</span>
                  <el-tag
                    v-for="d in chronicDiseases"
                    :key="d"
                    :type="selectedChronic === d && !customDisease ? '' : 'info'"
                    :effect="selectedChronic === d && !customDisease ? 'dark' : 'plain'"
                    class="cursor-pointer"
                    @click="customDisease = ''; selectedChronic = d"
                  >{{ d }}</el-tag>
                </div>
              </div>

              <div v-if="chronicPlan" class="space-y-4 animate-elastic-in">
                <el-descriptions :column="2" border>
                  <el-descriptions-item label="疾病名称">{{ chronicPlan.name }}</el-descriptions-item>
                  <el-descriptions-item label="管理目标">{{ chronicPlan.goal }}</el-descriptions-item>
                </el-descriptions>

                <el-row :gutter="20">
                  <el-col :span="12">
                    <div class="rounded-xl p-4 border border-success-200/30" style="background: rgba(16,185,129,0.04);">
                      <h4 class="font-bold font-display text-success-700 mb-3 text-sm">每日饮食清单</h4>
                      <ul class="list-disc list-inside text-sm text-surface-500 space-y-1">
                        <li v-for="(item, i) in chronicPlan.diet" :key="i">{{ item }}</li>
                      </ul>
                    </div>
                  </el-col>
                  <el-col :span="12">
                    <div class="rounded-xl p-4 border border-primary-200/30" style="background: rgba(79,70,229,0.04);">
                      <h4 class="font-bold font-display text-primary-700 mb-3 text-sm">运动计划</h4>
                      <ul class="list-disc list-inside text-sm text-surface-500 space-y-1">
                        <li v-for="(item, i) in chronicPlan.exercise" :key="i">{{ item }}</li>
                      </ul>
                    </div>
                  </el-col>
                </el-row>

                <div class="rounded-xl p-4 border border-warning-200/30" style="background: rgba(245,158,11,0.04);">
                  <h4 class="font-bold font-display text-warning-700 mb-3 text-sm">定期检查项目</h4>
                  <div class="flex flex-wrap gap-1.5">
                    <el-tag v-for="c in chronicPlan.checks" :key="c" type="warning" effect="plain" size="small">{{ c }}</el-tag>
                  </div>
                </div>

                <div class="flex items-start gap-3 p-4 rounded-xl text-sm text-primary-700 border-l-4 border-l-primary-500" style="background: rgba(79,70,229,0.04);">
                  <el-icon class="mt-0.5 flex-shrink-0"><InfoFilled /></el-icon>
                  <div>
                    <strong>用药提醒：</strong>
                    <span>{{ chronicPlan.medicationReminder }}</span>
                  </div>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- Health calendar -->
          <el-tab-pane label="健康日历" name="calendar">
            <div class="space-y-4">
              <div class="flex justify-between items-center">
                <h4 class="font-semibold font-display text-surface-800 text-sm">健康数据记录</h4>
                <el-button type="primary" icon="Plus" size="small" @click="openRecordDialog">添加记录</el-button>
              </div>

              <el-table :data="healthRecords" stripe>
                <el-table-column prop="date" label="日期" width="120" />
                <el-table-column prop="weight" label="体重(kg)" width="100" />
                <el-table-column label="血压(mmHg)" width="130">
                  <template #default="{ row }">
                    <span class="font-mono text-sm">{{ row.bloodPressureHigh }}/{{ row.bloodPressureLow }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="bloodSugar" label="血糖(mmol/L)" width="120">
                  <template #default="{ row }">
                    <span class="font-mono text-sm">{{ row.bloodSugar }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="heartRate" label="心率(bpm)" width="100">
                  <template #default="{ row }">
                    <span class="font-mono text-sm">{{ row.heartRate }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="note" label="备注" show-overflow-tooltip />
                <el-table-column label="操作" width="80" align="center">
                  <template #default="{ row }">
                    <el-button type="danger" text size="small" @click="deleteRecord(row._id || row.date, row.date)">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>

              <el-empty v-if="healthRecords.length === 0" description="暂无健康记录，点击上方按钮添加" />
            </div>
          </el-tab-pane>

          <!-- Plan history -->
          <el-tab-pane label="计划历史" name="history">
            <div class="space-y-4">
              <div class="flex justify-between items-center">
                <h4 class="font-semibold font-display text-surface-800 text-sm">已保存的健康计划</h4>
                <el-button v-if="savedPlans.length > 0" type="danger" text size="small" icon="Delete" @click="clearPlans">一键清理</el-button>
              </div>

              <el-empty v-if="savedPlans.length === 0" description="暂无历史计划，生成后将自动保存" />

              <div v-for="plan in savedPlans" :key="plan._id" class="glass-card p-4 cursor-pointer hover:border-primary-300/50 transition-all" @click="loadPlanIntoView(plan)">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-3">
                    <el-tag :type="plan.type === 'prevention' ? 'success' : 'warning'" effect="plain" size="small">
                      {{ plan.type === 'prevention' ? '预防计划' : '慢性病管理' }}
                    </el-tag>
                    <span class="text-sm font-semibold text-surface-700">
                      {{ plan.type === 'prevention' ? '疾病预防' : plan.disease }}
                    </span>
                  </div>
                  <span class="text-xs text-surface-400">{{ new Date(plan.created_at).toLocaleString('zh-CN') }}</span>
                </div>
                <p class="text-xs text-surface-400 mt-2 line-clamp-2">
                  <template v-if="plan.type === 'prevention'">
                    涵盖 {{ plan.data?.items?.length || 0 }} 种疾病预防方案
                  </template>
                  <template v-else>
                    {{ plan.data?.goal || '' }}
                  </template>
                </p>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- Add record dialog -->
    <el-dialog v-model="recordDialogVisible" title="添加健康记录" width="500px">
      <el-form :model="recordForm" label-width="100px">
        <el-form-item label="日期">
          <el-date-picker v-model="recordForm.date" type="date" value-format="YYYY-MM-DD" class="!w-full" />
        </el-form-item>
        <el-form-item label="体重(kg)">
          <el-input-number v-model="recordForm.weight" :min="20" :max="300" :precision="1" class="!w-full" />
        </el-form-item>
        <el-form-item label="收缩压(mmHg)">
          <el-input-number v-model="recordForm.bloodPressureHigh" :min="50" :max="300" class="!w-full" />
        </el-form-item>
        <el-form-item label="舒张压(mmHg)">
          <el-input-number v-model="recordForm.bloodPressureLow" :min="30" :max="200" class="!w-full" />
        </el-form-item>
        <el-form-item label="血糖(mmol/L)">
          <el-input-number v-model="recordForm.bloodSugar" :min="1" :max="50" :precision="1" class="!w-full" />
        </el-form-item>
        <el-form-item label="心率(bpm)">
          <el-input-number v-model="recordForm.heartRate" :min="30" :max="250" class="!w-full" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="recordForm.note" type="textarea" :rows="2" placeholder="今日感受..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="recordDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="saveRecord">保存</el-button>
      </template>
    </el-dialog>

    <!-- Disclaimer -->
    <div class="mt-4 flex items-start gap-3 p-4 glass-card text-sm text-surface-500">
      <el-icon class="mt-0.5 flex-shrink-0 text-surface-400"><InfoFilled /></el-icon>
      <p><strong>免责声明：</strong>健康计划仅供参考，具体方案请咨询专业医生。</p>
    </div>
  </div>
</template>
