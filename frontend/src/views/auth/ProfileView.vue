<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useUserStore } from '@/stores/index'
import { ElMessage } from 'element-plus'

const userStore = useUserStore()
const loading = ref(false)
const formRef = ref(null)

const form = reactive({
  age: '',
  gender: '',
  height: '',
  weight: '',
  blood_type: '',
  allergy_drug: '',
  allergy_food: '',
  medical_history: '',
  family_history: '',
  smoking: false,
  drinking: false,
})

const genderOptions = ['男', '女']
const bloodTypeOptions = ['A型', 'B型', 'AB型', 'O型', '未知']

onMounted(async () => {
  try {
    const profile = await userStore.fetchProfile()
    if (profile) {
      Object.assign(form, profile)
    }
  } catch {
    // first time user, no profile yet
  }
})

async function handleSave() {
  loading.value = true
  try {
    await userStore.updateProfile({ ...form })
    ElMessage.success('健康档案已保存')
  } catch {
    // error handled by interceptor
  } finally {
    loading.value = false
  }
}

function handleExportPDF() {
  ElMessage.info('PDF导出功能开发中...')
}
</script>

<template>
  <div class="max-w-4xl mx-auto">
    <!-- Header -->
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold font-display text-surface-900 flex items-center gap-2">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-primary-500/10 to-accent-500/10 flex items-center justify-center">
          <el-icon class="gradient-text" :size="18"><User /></el-icon>
        </div>
        个人健康档案
      </h2>
      <el-button @click="handleExportPDF">
        <el-icon class="mr-1"><Download /></el-icon> 导出PDF
      </el-button>
    </div>

    <!-- Form card -->
    <div class="glass-card p-6">
      <el-form ref="formRef" :model="form" label-width="100px" label-position="right">
        <!-- Basic info -->
        <div class="mb-6">
          <h3 class="text-sm font-bold font-display gradient-text uppercase tracking-wider mb-4 flex items-center gap-2">
            <span class="w-1 h-4 bg-gradient-to-b from-primary-500 to-accent-500 rounded-full"></span>
            基本信息
          </h3>
          <el-row :gutter="24">
            <el-col :span="8">
              <el-form-item label="年龄">
                <el-input-number v-model="form.age" :min="1" :max="150" placeholder="请输入年龄" class="!w-full" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="性别">
                <el-select v-model="form.gender" placeholder="请选择性别" class="!w-full">
                  <el-option v-for="g in genderOptions" :key="g" :label="g" :value="g" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="血型">
                <el-select v-model="form.blood_type" placeholder="请选择血型" class="!w-full">
                  <el-option v-for="b in bloodTypeOptions" :key="b" :label="b" :value="b" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="24">
            <el-col :span="8">
              <el-form-item label="身高(cm)">
                <el-input-number v-model="form.height" :min="50" :max="250" placeholder="身高" class="!w-full" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="体重(kg)">
                <el-input-number v-model="form.weight" :min="20" :max="300" :precision="1" placeholder="体重" class="!w-full" />
              </el-form-item>
            </el-col>
          </el-row>
        </div>

        <!-- Allergy history -->
        <div class="mb-6">
          <h3 class="text-sm font-bold font-display gradient-text uppercase tracking-wider mb-4 flex items-center gap-2">
            <span class="w-1 h-4 bg-gradient-to-b from-primary-500 to-accent-500 rounded-full"></span>
            过敏史
          </h3>
          <el-row :gutter="24">
            <el-col :span="12">
              <el-form-item label="药品过敏">
                <el-input v-model="form.allergy_drug" type="textarea" :rows="2" placeholder="如：青霉素、头孢类（多个用逗号分隔）" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="食物过敏">
                <el-input v-model="form.allergy_food" type="textarea" :rows="2" placeholder="如：海鲜、花生（多个用逗号分隔）" />
              </el-form-item>
            </el-col>
          </el-row>
        </div>

        <!-- Medical history -->
        <div class="mb-6">
          <h3 class="text-sm font-bold font-display gradient-text uppercase tracking-wider mb-4 flex items-center gap-2">
            <span class="w-1 h-4 bg-gradient-to-b from-primary-500 to-accent-500 rounded-full"></span>
            病史
          </h3>
          <el-form-item label="既往病史">
            <el-input v-model="form.medical_history" type="textarea" :rows="3" placeholder="如：高血压3年、2020年阑尾炎手术" />
          </el-form-item>
          <el-form-item label="家族病史">
            <el-input v-model="form.family_history" type="textarea" :rows="3" placeholder="如：父亲糖尿病、母亲高血压" />
          </el-form-item>
        </div>

        <!-- Lifestyle -->
        <div class="mb-6">
          <h3 class="text-sm font-bold font-display gradient-text uppercase tracking-wider mb-4 flex items-center gap-2">
            <span class="w-1 h-4 bg-gradient-to-b from-primary-500 to-accent-500 rounded-full"></span>
            生活习惯
          </h3>
          <el-row :gutter="24">
            <el-col :span="12">
              <el-form-item label="吸烟">
                <el-switch v-model="form.smoking" active-text="是" inactive-text="否" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="饮酒">
                <el-switch v-model="form.drinking" active-text="是" inactive-text="否" />
              </el-form-item>
            </el-col>
          </el-row>
        </div>

        <el-form-item>
          <el-button type="primary" size="large" class="w-full !rounded-xl !h-12 !text-sm !font-bold" :loading="loading" @click="handleSave">
            保存健康档案
          </el-button>
        </el-form-item>
      </el-form>
    </div>

    <!-- Privacy notice -->
    <div class="mt-4 flex items-start gap-3 p-4 glass-card text-sm text-primary-700 border-l-4 border-l-primary-500" style="background: rgba(79,70,229,0.04);">
      <el-icon class="mt-0.5 flex-shrink-0"><InfoFilled /></el-icon>
      <p>您的健康数据将被加密存储，仅用于提供个性化医疗建议，不会泄露给第三方。</p>
    </div>
  </div>
</template>
