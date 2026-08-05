<template>
  <div class="page-container">
    <div class="page-header">
      <span class="title">工作台</span>
      <el-button :icon="Refresh" circle @click="loadStats" />
    </div>

    <div v-loading="loading">
      <!-- 统计卡片 -->
      <el-row :gutter="16">
        <el-col :span="6" v-for="card in statCards" :key="card.title">
          <el-card shadow="hover" :body-style="{ padding: '20px' }">
            <div class="stat-card">
              <el-icon :size="40" :color="card.color">
                <component :is="card.icon" />
              </el-icon>
              <div class="stat-info">
                <div class="stat-value">{{ card.value }}</div>
                <div class="stat-title">{{ card.title }}</div>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 图表区 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><span>标注任务状态分布</span></template>
            <div ref="statusChartRef" style="height: 300px"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><span>数据资产模态类型分布</span></template>
            <div ref="typeChartRef" style="height: 300px"></div>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16" style="margin-top: 16px" class="recent-row">
        <el-col :span="12">
          <el-card shadow="hover" class="recent-card">
            <template #header><span>数据湖分层概览</span></template>
            <div ref="layerChartRef" style="height: 280px"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="hover" class="recent-card">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center">
                <span>最近接入受试者</span>
                <el-button text type="primary" @click="router.push('/management')">查看全部</el-button>
              </div>
            </template>
            <el-table :data="recentSubjects" border size="small" empty-text="暂无数据">
              <el-table-column prop="pseudo_id" label="伪ID" width="120" />
              <el-table-column prop="age" label="年龄" width="70" />
              <el-table-column prop="gender" label="性别" width="70" />
              <el-table-column prop="status" label="状态">
                <template #default="{ row }">
                  <el-tag size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="创建时间" min-width="150" />
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <!-- 最近标注任务 + 最近操作动态 -->
      <el-row :gutter="16" style="margin-top: 16px" class="recent-row">
        <el-col :span="14">
          <el-card shadow="hover" class="recent-card recent-card--tasks">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center">
                <span>最近标注任务</span>
                <el-button text type="primary" @click="router.push('/annotation')">查看全部</el-button>
              </div>
            </template>
            <el-table :data="recentTasks" border size="small" empty-text="暂无标注任务" class="recent-table">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="file_name" label="数据资产" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ row.file_name || '—' }}</template>
              </el-table-column>
              <el-table-column prop="data_type" label="模态" width="80">
                <template #default="{ row }">{{ typeTextMap[row.data_type] || row.data_type || '—' }}</template>
              </el-table-column>
              <el-table-column prop="annotator_name" label="标注员" width="100">
                <template #default="{ row }">{{ row.annotator_name || '未分配' }}</template>
              </el-table-column>
              <el-table-column prop="reviewer_name" label="复核医生" width="100">
                <template #default="{ row }">{{ row.reviewer_name || '未分配' }}</template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag size="small" :type="taskStatusTag(row.status)">{{ taskStatusText(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="创建时间" min-width="150" />
            </el-table>
          </el-card>
        </el-col>
        <el-col :span="10">
          <el-card shadow="hover" class="recent-card recent-card--logs">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
                <span>最近操作动态</span>
                <div style="display: flex; align-items: center; gap: 8px">
                  <el-select
                    v-if="logsIsAdmin"
                    v-model="logsFilterUsername"
                    placeholder="全部账号"
                    clearable
                    filterable
                    size="small"
                    style="width: 160px"
                    @change="loadStats"
                  >
                    <el-option
                      v-for="u in logsUserList"
                      :key="u.username"
                      :label="u.real_name === u.username ? u.username : `${u.real_name}（${u.username}）`"
                      :value="u.username"
                    />
                  </el-select>
                  <el-button text type="primary" @click="router.push('/management')">查看全部</el-button>
                </div>
              </div>
            </template>
            <el-timeline v-if="recentLogs.length" style="padding: 4px 0 0 0" class="recent-timeline">
              <el-timeline-item
                v-for="log in recentLogs"
                :key="log.id"
                :timestamp="log.created_at"
                placement="top"
                :type="logActionTag(log.action)"
              >
                <div style="font-size: 13px">
                  <el-tag size="small" :type="logActionTag(log.action)" style="margin-right: 6px">
                    {{ logActionText(log.action) }}
                  </el-tag>
                  <span style="color: #606266">{{ log.username || '—' }}</span>
                  <span style="color: #909399; margin: 0 4px">·</span>
                  <span style="color: #909399">{{ logTargetText(log.target_type) }}</span>
                </div>
                <div style="font-size: 12px; color: #909399; margin-top: 2px; line-height: 1.5">
                  {{ log.detail || '—' }}
                </div>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else description="暂无操作动态" :image-size="80" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 模块入口 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="8" v-for="m in modules" :key="m.path">
          <el-card shadow="hover" class="module-card" @click="router.push(m.path)">
            <el-icon :size="28" color="#409eff"><component :is="m.icon" /></el-icon>
            <div class="module-title">{{ m.title }}</div>
            <div class="module-desc">{{ m.desc }}</div>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { Refresh } from '@element-plus/icons-vue'
import { getDashboardStatsApi } from '@/api/dashboard'

const router = useRouter()
const loading = ref(false)

const stats = ref({
  subject_total: 0,
  asset_total: 0,
  task_total: 0,
  pending_pre_annotate: 0,
  pending_review: 0,
  qualified_rate: 0,
  status_distribution: {},
  type_distribution: {},
  layer_distribution: {},
  recent_subjects: [],
  recent_tasks: [],
  recent_logs: [],
  logs_is_admin: false,
  logs_filter_username: '',
  logs_user_list: [],
})

// 操作动态权限视图与筛选
const logsIsAdmin = computed(() => !!stats.value.logs_is_admin)
const logsFilterUsername = ref('')
const logsUserList = computed(() => stats.value.logs_user_list || [])

const statCards = computed(() => [
  { title: '受试者总数', value: stats.value.subject_total, icon: 'User', color: '#409eff' },
  { title: '数据资产', value: stats.value.asset_total, icon: 'Files', color: '#67c23a' },
  { title: '待预标注任务', value: stats.value.pending_pre_annotate, icon: 'EditPen', color: '#e6a23c' },
  { title: '待复核任务', value: stats.value.pending_review, icon: 'View', color: '#f56c6c' },
])

const recentSubjects = computed(() => stats.value.recent_subjects || [])
const recentTasks = computed(() => stats.value.recent_tasks || [])
const recentLogs = computed(() => stats.value.recent_logs || [])

const taskStatusText = (s) => ({
  pending: '待预标注', pre_annotated: '已预标注', annotating: '标注中',
  annotated: '已标注', reviewing: '复核中', rejected: '已驳回', approved: '已通过',
}[s] || s || '—')
const taskStatusTag = (s) => ({
  pending: 'info', pre_annotated: 'warning', annotating: 'primary',
  annotated: 'success', reviewing: 'warning', rejected: 'danger', approved: 'success',
}[s] || 'info')

const logActionText = (a) => ({
  create: '创建', update: '更新', delete: '删除',
  upload: '上传', batch_import: '批量导入',
  pre_annotate: '预标注', assign: '分配', annotate: '标注', review: '复核',
  clean: '清洗', standardize: '标准化', login: '登录',
}[a] || a || '—')
const logActionTag = (a) => ({
  create: 'success', update: 'warning', delete: 'danger',
  upload: 'primary', batch_import: 'info',
  pre_annotate: 'info', assign: 'primary', annotate: 'success', review: 'warning',
  clean: 'primary', standardize: 'success', login: 'info',
}[a] || 'info')
const logTargetText = (t) => ({
  subject: '受试者', asset: '数据资产', annotation_task: '标注任务',
  user: '用户', standard: '规范',
}[t] || t || '—')

const modules = [
  { path: '/data', title: '数据清洗及标准化', desc: '缺失值处理、异常值剔除、信号去噪、音视频质量评估', icon: 'Brush' },
  { path: '/annotation', title: '数据标注', desc: '自动预标注、人工标注、医生复核三级流程', icon: 'EditPen' },
  { path: '/visualization', title: '数据对齐与可视化', desc: '多模态时间轴对齐、同步可视化展示', icon: 'DataLine' },
]

// 图表实例
const statusChartRef = ref()
const typeChartRef = ref()
const layerChartRef = ref()
let statusChart, typeChart, layerChart

const statusTextMap = {
  pending: '待预标注', pre_annotated: '已预标注', annotating: '标注中',
  annotated: '已标注', reviewing: '复核中', rejected: '已驳回', approved: '已通过',
}

const typeTextMap = {
  video: '视频', audio: '音频', eeg: '脑电', ecg: '心电', eye: '眼动', gait: '步态', scale: '量表', task: '任务',
}

const layerTextMap = {
  raw: '原始数据', cleaned: '清洗数据', feature: '特征数据', annotation: '标注数据',
}

const initCharts = () => {
  if (statusChartRef.value) {
    statusChart = echarts.init(statusChartRef.value)
  }
  if (typeChartRef.value) {
    typeChart = echarts.init(typeChartRef.value)
  }
  if (layerChartRef.value) {
    layerChart = echarts.init(layerChartRef.value)
  }
}

const updateCharts = () => {
  // 任务状态分布 - 环形图
  const statusData = Object.entries(stats.value.status_distribution || {}).map(([k, v]) => ({
    name: statusTextMap[k] || k,
    value: v,
  }))
  statusChart?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: statusData.length ? statusData : [{ name: '暂无数据', value: 1 }],
      color: ['#909399', '#e6a23c', '#409eff', '#67c23a', '#9254de', '#f56c6c', '#13c2c2'],
    }],
  })

  // 模态类型分布 - 柱状图
  const typeData = Object.entries(stats.value.type_distribution || {}).map(([k, v]) => ({
    name: typeTextMap[k] || k,
    value: v,
  }))
  typeChart?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: typeData.length ? typeData.map((d) => d.name) : ['暂无数据'],
      axisLabel: { interval: 0, rotate: typeData.length > 4 ? 20 : 0 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: typeData.length ? typeData.map((d) => d.value) : [0],
      barWidth: '50%',
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#409eff' },
          { offset: 1, color: '#67c23a' },
        ]),
        borderRadius: [4, 4, 0, 0],
      },
    }],
  })

  // 数据湖分层 - 横向条形图
  const layerData = Object.entries(stats.value.layer_distribution || {}).map(([k, v]) => ({
    name: layerTextMap[k] || k,
    value: v,
  }))
  layerChart?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 80, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: ['原始数据', '清洗数据', '特征数据', '标注数据'],
    },
    series: [{
      type: 'bar',
      data: [
        layerData.find((d) => d.name === '原始数据')?.value || 0,
        layerData.find((d) => d.name === '清洗数据')?.value || 0,
        layerData.find((d) => d.name === '特征数据')?.value || 0,
        layerData.find((d) => d.name === '标注数据')?.value || 0,
      ],
      barWidth: '50%',
      itemStyle: {
        color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
          { offset: 0, color: '#13c2c2' },
          { offset: 1, color: '#9254de' },
        ]),
        borderRadius: [0, 4, 4, 0],
      },
      label: { show: true, position: 'right' },
    }],
  })
}

const loadStats = async () => {
  loading.value = true
  try {
    const params = {}
    if (logsFilterUsername.value) params.username = logsFilterUsername.value
    const res = await getDashboardStatsApi(params)
    stats.value = res.data
    // 后端回传当前生效的筛选值（防止非 admin 被前端注入 username）
    if (typeof res.data.logs_filter_username === 'string') {
      logsFilterUsername.value = res.data.logs_filter_username
    }
    await nextTick()
    if (!statusChart) initCharts()
    updateCharts()
  } finally {
    loading.value = false
  }
}

const handleResize = () => {
  statusChart?.resize()
  typeChart?.resize()
  layerChart?.resize()
}

onMounted(async () => {
  await loadStats()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  statusChart?.dispose()
  typeChart?.dispose()
  layerChart?.dispose()
})
</script>

<style scoped lang="scss">
/* 最近任务与操作动态两列等高 */
.recent-row {
  display: flex;
  align-items: stretch;
  > .el-col {
    display: flex;
  }
}
.recent-card {
  width: 100%;
  height: 100%;
  :deep(.el-card__body) {
    height: 100%;
    overflow: auto;
  }
}
/* 最近任务/动态卡固定高度，让内部表格与时间轴滚动 */
.recent-card--tasks,
.recent-card--logs {
  height: 480px;
  display: flex;
  flex-direction: column;
  :deep(.el-card__body) {
    flex: 1;
    overflow: auto;
    padding: 12px;
  }
}
.recent-table {
  :deep(.el-table__inner-wrapper) {
    height: 100%;
  }
}
.recent-timeline {
  max-height: 100%;
  overflow-y: auto;
}
.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
}
.stat-info {
  .stat-value {
    font-size: 28px;
    font-weight: 600;
  }
  .stat-title {
    font-size: 13px;
    color: #909399;
    margin-top: 2px;
  }
}
.module-card {
  cursor: pointer;
  transition: transform 0.2s;
  &:hover {
    transform: translateY(-2px);
  }
  .module-title {
    font-size: 16px;
    font-weight: 600;
    margin: 8px 0 4px;
  }
  .module-desc {
    font-size: 12px;
    color: #909399;
    line-height: 1.6;
  }
}
</style>
