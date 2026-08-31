<script setup>
import { ref, shallowRef, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import request from '@/utils/request'
import { ElMessage } from 'element-plus'

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const route = useRoute()
const router = useRouter()
const chartRef = ref(null)
const searchQuery = ref('')
const searchResults = shallowRef([])
const selectedEntity = shallowRef(null)
const entityDrawer = ref(false)
const pathDialog = ref(false)
const pathSource = ref('')
const pathTarget = ref('')
const pathResult = shallowRef([])
const loading = ref(false)

const entityTypes = [
  { label: '疾病', color: '#e74c3c', type: 'Disease' },
  { label: '药品', color: '#3498db', type: 'Drug' },
  { label: '症状', color: '#2ecc71', type: 'Symptom' },
  { label: '食物', color: '#f39c12', type: 'Food' },
  { label: '检查', color: '#9b59b6', type: 'Check' },
  { label: '科室', color: '#e67e22', type: 'Department' },
  { label: '在售药品', color: '#95a5a6', type: 'Producer' },
]
const filterChecked = ref(entityTypes.map(() => true))

const colorMap = {}
entityTypes.forEach(t => { colorMap[t.type] = t.color })
const categoryNames = entityTypes.map(t => t.label)
const labelToIdx = {}
entityTypes.forEach((t, i) => { labelToIdx[t.type] = i })

let chart = null
let chartReady = false
let resizeObserver = null

onMounted(() => {
  nextTick(initChart)
  window.addEventListener('resize', safeResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', safeResize)
  if (resizeObserver) resizeObserver.disconnect()
  if (chart) { chart.dispose(); chart = null }
})

function safeResize() {
  if (chart && chartReady) {
    try { chart.resize() } catch {}
  }
}

function initChart() {
  const el = chartRef.value
  if (!el || !el.offsetWidth) {
    resizeObserver = new ResizeObserver(() => {
      if (el.offsetWidth) {
        resizeObserver.disconnect()
        initChart()
      }
    })
    resizeObserver.observe(el)
    return
  }
  chart = echarts.init(el)
  chartReady = true
  chart.on('click', params => {
    if (params.dataType === 'node') {
      showEntityDetail({ name: params.data.name, label: params.data.entityType })
    }
  })
  loadInitialData()
}

async function loadInitialData() {
  loading.value = true
  const entityName = route.query.entity
  try {
    const res = await request.get('/api/kg/related', { params: { entity: entityName || '感冒', depth: 1 } })
    if (res.nodes?.length) {
      renderGraph(res.nodes, res.links || [])
    } else if (entityName) {
      renderGraph(getDemoNodes(), getDemoLinks())
    } else {
      renderGraph(getDemoNodes(), getDemoLinks())
    }
  } catch {
    if (!entityName) renderGraph(getDemoNodes(), getDemoLinks())
  } finally {
    loading.value = false
  }
  if (entityName) {
    searchQuery.value = entityName
    showEntityDetail({ name: entityName })
  }
}

watch(() => route.query.entity, (val) => {
  if (val && chartReady) {
    loadInitialData()
  }
})

function deduplicateNodes(nodes) {
  const seen = new Set()
  return nodes.filter(n => {
    if (seen.has(n.name)) return false
    seen.add(n.name)
    return true
  })
}

function renderGraph(nodes, links) {
  if (!chart) return
  const uniqueNodes = deduplicateNodes(nodes)
  const nodeNameSet = new Set(uniqueNodes.map(n => n.name))

  const graphNodes = uniqueNodes.map(n => {
    const label = n.label || 'Disease'
    const catIdx = labelToIdx[label] ?? 0
    return {
      id: n.name,
      name: n.name,
      entityType: label,
      category: catIdx,
      symbolSize: 30,
      itemStyle: { color: colorMap[label] || '#95a5a6', borderColor: 'rgba(255,255,255,0.6)', borderWidth: 2 },
    }
  })

  const graphLinks = links
    .filter(l => nodeNameSet.has(l.source) && nodeNameSet.has(l.target))
    .map(l => ({
      source: l.source,
      target: l.target,
      relType: l.relType || '',
      lineStyle: { color: 'rgba(79,70,229,0.15)', curveness: 0.2, width: 1.5 },
    }))

  nextTick(() => {
    if (!chart) return
    chart.setOption({
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(255,255,255,0.9)',
        backdropFilter: 'blur(12px)',
        borderColor: 'rgba(79,70,229,0.1)',
        borderWidth: 1,
        borderRadius: 12,
        textStyle: { color: '#0F172A', fontSize: 13 },
        formatter: p => {
          if (p.dataType === 'node') return `<b>${p.data.name}</b><br/>类型：${p.data.entityType}`
          return p.data.relType || ''
        },
      },
      legend: { data: categoryNames, top: 5, textStyle: { fontSize: 11, color: '#64748B' } },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        zoom: 0.7,
        force: { repulsion: 350, gravity: 0.06, edgeLength: [80, 220] },
        categories: categoryNames.map(n => ({ name: n })),
        label: { show: true, fontSize: 11, position: 'bottom', distance: 5, color: '#334155', fontWeight: 500 },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 4 },
          itemStyle: { shadowBlur: 16, shadowColor: 'rgba(79,70,229,0.3)' },
        },
        data: graphNodes,
        links: graphLinks,
      }],
    })
  })
}

function getDemoNodes() {
  return [
    { name: '感冒', label: 'Disease' },
    { name: '发烧', label: 'Symptom' }, { name: '咳嗽', label: 'Symptom' },
    { name: '流鼻涕', label: 'Symptom' }, { name: '头痛', label: 'Symptom' },
    { name: '阿莫西林', label: 'Drug' }, { name: '板蓝根', label: 'Drug' },
    { name: '呼吸内科', label: 'Department' }, { name: '血常规', label: 'Check' },
    { name: '姜汤', label: 'Food' }, { name: '肺炎', label: 'Disease' },
  ]
}
function getDemoLinks() {
  return [
    { source: '感冒', target: '发烧', relType: '症状' },
    { source: '感冒', target: '咳嗽', relType: '症状' },
    { source: '感冒', target: '流鼻涕', relType: '症状' },
    { source: '感冒', target: '头痛', relType: '症状' },
    { source: '感冒', target: '阿莫西林', relType: '常用药' },
    { source: '感冒', target: '板蓝根', relType: '推荐药' },
    { source: '感冒', target: '呼吸内科', relType: '科室' },
    { source: '感冒', target: '血常规', relType: '检查' },
    { source: '感冒', target: '姜汤', relType: '宜吃' },
    { source: '感冒', target: '肺炎', relType: '并发症' },
  ]
}

let searchTimer = null
function handleSearch(q) {
  clearTimeout(searchTimer)
  if (!q) { searchResults.value = []; return }
  searchTimer = setTimeout(async () => {
    try {
      const res = await request.get('/api/kg/entities', { params: { search: q, limit: 20 } })
      searchResults.value = res.nodes || []
    } catch { searchResults.value = [] }
  }, 300)
}

async function selectSearchEntity(entity) {
  searchQuery.value = entity.name
  searchResults.value = []
  loading.value = true
  try {
    const res = await request.get('/api/kg/related', { params: { entity: entity.name, depth: 1 } })
    if (res.nodes?.length) renderGraph(res.nodes, res.links || [])
  } catch { ElMessage.error('加载失败') }
  finally { loading.value = false }
  showEntityDetail(entity)
}

async function showEntityDetail(entity) {
  loading.value = true
  try {
    const res = await request.get(`/api/kg/entity/${encodeURIComponent(entity.name)}`)
    selectedEntity.value = res
  } catch {
    selectedEntity.value = { name: entity.name, label: entity.label || 'Disease', properties: {} }
  } finally { loading.value = false; entityDrawer.value = true }
}

async function findPath() {
  if (!pathSource.value || !pathTarget.value) { ElMessage.warning('请输入起始和目标实体'); return }
  loading.value = true
  try {
    const res = await request.get('/api/kg/path', { params: { source: pathSource.value, target: pathTarget.value } })
    pathResult.value = res.paths || []
    if (!pathResult.value.length) ElMessage.info('未找到路径')
    else {
      const ns = new Set(), ls = []
      for (const p of pathResult.value) {
        for (const n of p.nodes) ns.add(n)
        for (let i = 0; i < p.edges.length; i++) ls.push({ source: p.nodes[i], target: p.nodes[i+1], relType: p.edges[i] })
      }
      renderGraph(Array.from(ns).map(n => ({ name: n, label: 'Disease' })), ls)
    }
  } catch { pathResult.value = [] }
  finally { loading.value = false }
}

async function expandRelated(name) {
  loading.value = true
  try {
    const res = await request.get('/api/kg/related', { params: { entity: name, depth: 1 } })
    if (res.nodes?.length) renderGraph(res.nodes, res.links || [])
  } catch { ElMessage.error('加载失败') }
  finally { loading.value = false }
}

const labelNames = { Disease: '疾病', Drug: '药品', Symptom: '症状', Food: '食物', Check: '检查', Department: '科室', Producer: '在售药品' }
function consultAI(entity) {
  const typeName = labelNames[entity.label] || '实体'
  const prompts = {
    Disease: `请详细介绍一下${entity.name}这种疾病，包括病因、症状、预防措施和治疗方法`,
    Drug: `请介绍一下${entity.name}这种药品的功效、用法用量、副作用和注意事项`,
    Symptom: `我出现了${entity.name}的症状，可能是什么原因导致的？需要注意什么？`,
    Food: `请介绍一下${entity.name}的营养价值和饮食建议`,
    Check: `请介绍一下${entity.name}这项检查的用途、流程和注意事项`,
    Department: `${entity.name}主要诊治哪些疾病？什么情况下应该挂这个科室？`,
  }
  const q = prompts[entity.label] || `请介绍一下${entity.name}（${typeName}）的相关信息`
  router.push({ path: '/chat', query: { q } })
}

function handleFilterChange() {
  if (!chart) return
  const checked = filterChecked.value
  nextTick(() => {
    if (!chart) return
    const opt = chart.getOption()
    if (!opt.series?.[0]?.data) return
    chart.setOption({ series: [{ data: opt.series[0].data.map(n => ({
      ...n, itemStyle: { ...n.itemStyle, opacity: checked[n.category] ? 1 : 0.08 }
    })) }] })
  })
}

function zoomIn() {
  if (!chart) return
  const z = (chart.getOption().series[0]?.zoom || 0.7) * 1.3
  nextTick(() => chart?.setOption({ series: [{ zoom: z }] }))
}
function zoomOut() {
  if (!chart) return
  const z = (chart.getOption().series[0]?.zoom || 0.7) / 1.3
  nextTick(() => chart?.setOption({ series: [{ zoom: z }] }))
}
function resetView() {
  if (chart) { chart.dispose(); chart = null; chartReady = false }
  nextTick(initChart)
}
function exportImage() {
  if (!chart) return
  const a = document.createElement('a')
  a.download = '知识图谱.png'
  a.href = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#F8FAFC' })
  a.click()
}
</script>

<template>
  <div class="flex flex-col h-[calc(100vh-100px)] gap-5">
    <!-- Toolbar -->
    <div class="glass-card p-4 flex-shrink-0">
      <div class="flex items-center gap-3 flex-wrap">
        <!-- Search -->
        <div class="relative flex-1 min-w-[200px] max-w-[400px]">
          <el-input v-model="searchQuery" placeholder="搜索实体（疾病/药品/症状...）" prefix-icon="Search" clearable @input="handleSearch" />
          <div v-if="searchResults.length" class="absolute z-50 top-full left-0 w-full glass-card max-h-60 overflow-y-auto mt-1 animate-slide-up">
            <div
              v-for="item in searchResults"
              :key="item.name"
              class="px-4 py-2.5 cursor-pointer text-sm flex items-center gap-2 hover:bg-primary-500/5 transition-colors"
              @click="selectSearchEntity(item)"
            >
              <span class="w-2 h-2 rounded-full flex-shrink-0" :style="{ background: colorMap[item.label] }"></span>
              <span>{{ item.name }}</span>
            </div>
          </div>
        </div>

        <!-- Zoom controls -->
        <el-button-group>
          <el-tooltip content="放大"><el-button icon="ZoomIn" @click="zoomIn" /></el-tooltip>
          <el-tooltip content="缩小"><el-button icon="ZoomOut" @click="zoomOut" /></el-tooltip>
          <el-tooltip content="重置"><el-button icon="RefreshRight" @click="resetView" /></el-tooltip>
        </el-button-group>
        <el-button icon="Download" @click="exportImage">导出PNG</el-button>
        <el-button type="primary" icon="Connection" @click="pathDialog = true">路径发现</el-button>
      </div>

      <!-- Entity type filters -->
      <div class="flex items-center gap-2 mt-3 flex-wrap">
        <span class="text-xs text-surface-400 mr-1 font-medium">实体筛选：</span>
        <button
          v-for="(t, i) in entityTypes"
          :key="t.type"
          class="flex items-center gap-1.5 px-3 py-1 rounded-pill text-xs font-medium border transition-all duration-200"
          :class="filterChecked[i]
            ? 'bg-white/60 border-white/40 text-surface-700 shadow-sm backdrop-blur-sm'
            : 'bg-surface-100/50 border-transparent text-surface-400'"
          @click="filterChecked[i] = !filterChecked[i]; handleFilterChange()"
        >
          <span class="w-2 h-2 rounded-full" :style="{ background: filterChecked[i] ? t.color : '#CBD5E1' }"></span>
          {{ t.label }}
        </button>
      </div>
    </div>

    <!-- Graph area -->
    <div v-loading="loading" class="flex-1 glass-card overflow-hidden glow-border">
      <div ref="chartRef" class="w-full h-full min-h-[500px]"></div>
    </div>

    <!-- Entity detail drawer -->
    <el-drawer v-model="entityDrawer" :title="selectedEntity?.name || '实体详情'" size="450px">
      <div v-if="selectedEntity">
        <div class="flex items-center gap-2 mb-5">
          <span
            class="px-2.5 py-0.5 rounded-pill text-xs font-medium text-white shadow-sm"
            :style="{ background: `linear-gradient(135deg, ${colorMap[selectedEntity.label] || '#95a5a6'}, ${colorMap[selectedEntity.label] || '#95a5a6'}dd)` }"
          >{{ selectedEntity.label }}</span>
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
          <div v-if="selectedEntity.symptoms?.length" class="mt-4">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">相关症状</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="s in selectedEntity.symptoms" :key="s" type="success" effect="plain" size="small">{{ s }}</el-tag>
            </div>
          </div>
          <div v-if="selectedEntity.drugs?.length" class="mt-4">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">常用药品</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="d in selectedEntity.drugs" :key="d" type="primary" effect="plain" size="small">{{ d }}</el-tag>
            </div>
          </div>
          <div v-if="selectedEntity.foods?.length" class="mt-4">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">宜吃食物</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="f in selectedEntity.foods" :key="f" type="warning" effect="plain" size="small">{{ f }}</el-tag>
            </div>
          </div>
          <div v-if="selectedEntity.checks?.length" class="mt-4">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">相关检查</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="c in selectedEntity.checks" :key="c" type="info" effect="plain" size="small">{{ c }}</el-tag>
            </div>
          </div>
        </template>
        <template v-else-if="selectedEntity.label === 'Symptom'">
          <div v-if="selectedEntity.diseases?.length">
            <h4 class="text-sm font-semibold font-display text-surface-700 mb-2">可能疾病</h4>
            <div class="flex flex-wrap gap-1.5">
              <el-tag v-for="d in selectedEntity.diseases" :key="d" type="danger" effect="plain" size="small">{{ d }}</el-tag>
            </div>
          </div>
        </template>
        <template v-else>
          <el-descriptions :column="1" border>
            <el-descriptions-item v-for="(v,k) in selectedEntity.properties" :key="k" :label="k">{{ v }}</el-descriptions-item>
          </el-descriptions>
        </template>
        <div class="mt-5 flex gap-2">
          <el-button type="primary" @click="expandRelated(selectedEntity.name)">
            <el-icon class="mr-1"><Connection /></el-icon>展开关联
          </el-button>
          <el-button @click="consultAI(selectedEntity)">
            <el-icon class="mr-1"><ChatDotRound /></el-icon>咨询AI
          </el-button>
        </div>
      </div>
    </el-drawer>

    <!-- Path discovery dialog -->
    <el-dialog v-model="pathDialog" title="路径发现" width="600px">
      <div class="flex gap-3 items-center mb-4">
        <el-input v-model="pathSource" placeholder="起始实体" clearable class="flex-1" />
        <span class="text-surface-300 text-lg">&rarr;</span>
        <el-input v-model="pathTarget" placeholder="目标实体" clearable class="flex-1" />
        <el-button type="primary" :loading="loading" @click="findPath">查找</el-button>
      </div>
      <div v-if="pathResult.length">
        <h4 class="font-semibold font-display text-sm text-surface-700 mb-3">找到 {{ pathResult.length }} 条路径：</h4>
        <div v-for="(p,i) in pathResult" :key="i" class="glass-card p-3 mb-2 animate-stagger-in" :style="{ animationDelay: `${i * 60}ms` }">
          <div class="flex flex-wrap gap-1 items-center">
            <template v-for="(node,j) in p.nodes" :key="j">
              <el-tag size="small" :type="j===0?'primary':j===p.nodes.length-1?'success':'info'">{{ node }}</el-tag>
              <span v-if="j<p.edges.length" class="text-[11px] text-surface-400 mx-1">&rarr;{{ p.edges[j] }}&rarr;</span>
            </template>
          </div>
          <p class="text-xs text-surface-400 mt-2">{{ p.description }}</p>
        </div>
      </div>
    </el-dialog>
  </div>
</template>
