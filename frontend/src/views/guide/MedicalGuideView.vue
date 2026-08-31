<script setup>
import { ref, reactive } from 'vue'
import request from '@/utils/request'
import { ElMessage } from 'element-plus'

const activeTab = ref('department')
const loading = ref(false)

const symptomInput = ref('')
const departmentResult = ref(null)

const checkInput = ref('')
const checkResult = ref(null)

const faqList = [
  {
    q: '如何预约挂号？',
    a: '您可以通过医院官网、微信公众号、电话预约或现场挂号。建议提前1-2周预约专家号，普通门诊可当天挂号。',
  },
  {
    q: '医保如何报销？',
    a: '携带医保卡到定点医院就诊，门诊费用达到起付线后按比例报销。住院费用报销比例一般为70%-90%，具体以当地政策为准。',
  },
  {
    q: '看诊前需要准备什么？',
    a: '1. 携带身份证、医保卡；2. 整理好既往病历和检查报告；3. 记录症状出现时间、频率；4. 列出正在服用的药物；5. 空腹检查需提前禁食。',
  },
  {
    q: '如何选择科室？',
    a: '根据主要症状选择对应科室。如不确定，可先挂全科门诊，由医生判断后转诊。您也可以使用本系统的科室推荐功能。',
  },
  {
    q: '检查报告多久出结果？',
    a: '血常规、尿常规等一般30分钟-2小时；CT、MRI一般1-2个工作日；病理检查一般3-5个工作日。具体以医院通知为准。',
  },
  {
    q: '住院需要准备什么？',
    a: '1. 身份证、医保卡、银行卡；2. 日用品（毛巾、牙刷、拖鞋等）；3. 换洗衣物；4. 陪护人员相关证件；5. 既往病历资料。',
  },
]

async function searchDepartment() {
  if (!symptomInput.value) {
    ElMessage.warning('请输入症状或疾病名称')
    return
  }
  loading.value = true
  try {
    const res = await request.get('/api/guide/department', { params: { query: symptomInput.value } })
    departmentResult.value = res
  } catch {
    ElMessage.error('查询失败')
  } finally {
    loading.value = false
  }
}

async function searchCheck() {
  if (!checkInput.value) {
    ElMessage.warning('请输入检查项目名称')
    return
  }
  loading.value = true
  try {
    const res = await request.get('/api/guide/check', { params: { query: checkInput.value } })
    checkResult.value = res
  } catch {
    ElMessage.error('查询失败')
  } finally {
    loading.value = false
  }
}

const flowSteps = [
  { label: '挂号', icon: 'Tickets' },
  { label: '就诊', icon: 'User' },
  { label: '检查', icon: 'Monitor' },
  { label: '取药', icon: 'FirstAidKit' },
  { label: '住院', icon: 'OfficeBuilding' },
]
</script>

<template>
  <div class="max-w-5xl mx-auto">
    <div class="glass-card overflow-hidden">
      <div class="card-header">
        <div class="card-header-icon" style="background: rgba(249,115,22,0.1);">
          <el-icon class="text-orange-500" :size="16"><Guide /></el-icon>
        </div>
        <span class="card-header-title">就医指南与科室导航</span>
      </div>

      <div class="p-5">
        <el-tabs v-model="activeTab">
          <!-- Department recommendation -->
          <el-tab-pane label="科室推荐" name="department">
            <div class="space-y-4">
              <div class="flex gap-3">
                <el-input
                  v-model="symptomInput"
                  placeholder="输入症状或疾病名称，如：头痛、糖尿病"
                  prefix-icon="Search"
                  size="large"
                  clearable
                  @keydown.enter="searchDepartment"
                  class="flex-1"
                />
                <el-button type="primary" size="large" :loading="loading" @click="searchDepartment">
                  查询科室
                </el-button>
              </div>

              <div v-if="departmentResult" class="space-y-3">
                <div
                  v-for="(dept, idx) in departmentResult.departments"
                  :key="idx"
                  class="border border-white/50 rounded-xl p-4 transition-card hover:border-primary-200/50 stagger-item bg-white/30 backdrop-blur-sm"
                >
                  <div class="flex items-center gap-3 mb-3">
                    <div class="w-9 h-9 rounded-xl flex items-center justify-center" style="background: rgba(249,115,22,0.1);">
                      <el-icon class="text-orange-500" :size="18"><OfficeBuilding /></el-icon>
                    </div>
                    <h4 class="text-base font-bold font-display text-surface-800">{{ dept.name }}</h4>
                  </div>
                  <p class="text-sm text-surface-500 mb-3 leading-relaxed">{{ dept.description || '暂无简介' }}</p>
                  <div class="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <span class="text-surface-400 text-xs font-medium">常见疾病：</span>
                      <div class="flex flex-wrap gap-1 mt-1">
                        <el-tag v-for="d in (dept.diseases || []).slice(0, 5)" :key="d" size="small" type="danger" effect="plain">{{ d }}</el-tag>
                      </div>
                    </div>
                    <div>
                      <span class="text-surface-400 text-xs font-medium">常用检查：</span>
                      <div class="flex flex-wrap gap-1 mt-1">
                        <el-tag v-for="c in (dept.checks || []).slice(0, 5)" :key="c" size="small" type="info" effect="plain">{{ c }}</el-tag>
                      </div>
                    </div>
                  </div>
                </div>
                <el-empty v-if="!departmentResult.departments?.length" description="未找到相关科室" />
              </div>
            </div>
          </el-tab-pane>

          <!-- Check item explanation -->
          <el-tab-pane label="检查项目说明" name="check">
            <div class="space-y-4">
              <div class="flex gap-3">
                <el-input
                  v-model="checkInput"
                  placeholder="输入检查项目名称，如：血常规、CT"
                  prefix-icon="Search"
                  size="large"
                  clearable
                  @keydown.enter="searchCheck"
                  class="flex-1"
                />
                <el-button type="primary" size="large" :loading="loading" @click="searchCheck">
                  查询
                </el-button>
              </div>

              <div v-if="checkResult" class="border border-white/50 rounded-xl p-5 animate-elastic-in bg-white/30 backdrop-blur-sm">
                <h3 class="text-lg font-bold font-display text-surface-900 mb-4">{{ checkResult.name }}</h3>
                <el-descriptions :column="1" border>
                  <el-descriptions-item label="检查目的">{{ checkResult.purpose || '暂无' }}</el-descriptions-item>
                  <el-descriptions-item label="检查流程">{{ checkResult.process || '暂无' }}</el-descriptions-item>
                  <el-descriptions-item label="注意事项">{{ checkResult.precautions || '暂无' }}</el-descriptions-item>
                  <el-descriptions-item label="正常值范围">{{ checkResult.normalRange || '暂无' }}</el-descriptions-item>
                </el-descriptions>
                <div v-if="checkResult.relatedDiseases?.length" class="mt-4">
                  <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">需要做此检查的疾病：</h4>
                  <div class="flex flex-wrap gap-1.5">
                    <el-tag v-for="d in checkResult.relatedDiseases" :key="d" type="warning" effect="plain" size="small">{{ d }}</el-tag>
                  </div>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- Medical process guide -->
          <el-tab-pane label="就医流程指引" name="flow">
            <div class="space-y-6">
              <!-- Process flow -->
              <div class="flex items-center justify-center gap-3 py-6">
                <template v-for="(step, idx) in flowSteps" :key="idx">
                  <div class="flex flex-col items-center group">
                    <div class="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-500/10 to-accent-500/10 flex items-center justify-center text-primary-600 font-bold text-lg font-display border border-primary-200/20 transition-all group-hover:shadow-glow group-hover:scale-110">
                      {{ idx + 1 }}
                    </div>
                    <span class="mt-2 text-xs font-semibold font-display text-surface-600">{{ step.label }}</span>
                  </div>
                  <el-icon v-if="idx < 4" class="mx-1 text-surface-300 text-lg"><Right /></el-icon>
                </template>
              </div>

              <!-- FAQ -->
              <div>
                <h3 class="text-base font-bold font-display text-surface-800 mb-3">常见问题解答</h3>
                <el-collapse>
                  <el-collapse-item v-for="(faq, idx) in faqList" :key="idx" :title="faq.q" :name="idx">
                    <p class="text-sm text-surface-500 leading-relaxed">{{ faq.a }}</p>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- Disclaimer -->
    <div class="mt-4 flex items-start gap-3 p-4 glass-card text-sm text-surface-500">
      <el-icon class="mt-0.5 flex-shrink-0 text-surface-400"><InfoFilled /></el-icon>
      <p><strong>温馨提示：</strong>以上信息仅供参考，具体就医流程以各医院实际规定为准。</p>
    </div>
  </div>
</template>
