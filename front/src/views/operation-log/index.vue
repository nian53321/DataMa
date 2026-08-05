<template>
  <div class="page-container">
    <!-- 权限拦截：仅 admin 可访问（与原 system 页面操作日志按钮 v-if="isAdmin" 对齐） -->
    <el-alert
      v-if="!isAdmin"
      type="warning"
      :closable="false"
      title="无权限访问"
      description="仅管理员可查看操作日志，请联系管理员授权。"
      show-icon
    />
    <template v-else>
      <div class="page-header">
        <span class="title">操作日志</span>
        <div style="display: flex; align-items: center; gap: 12px">
          <el-radio-group v-model="statsDays" size="small" @change="loadStats">
            <el-radio-button :value="7">近7天</el-radio-button>
            <el-radio-button :value="30">近30天</el-radio-button>
            <el-radio-button :value="90">近90天</el-radio-button>
          </el-radio-group>
          <el-button :icon="RefreshRight" @click="reloadAll">刷新</el-button>
        </div>
      </div>

      <!-- 统计卡片 -->
      <el-row :gutter="16" v-loading="statsLoading">
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

      <!-- 图表区 第一行：操作类型 / 对象类型 / 角色分布 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="8">
          <el-card shadow="hover">
            <template #header><span>操作类型分布</span></template>
            <div ref="actionChartRef" style="height: 280px"></div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="hover">
            <template #header><span>对象类型分布</span></template>
            <div ref="targetChartRef" style="height: 280px"></div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="hover">
            <template #header><span>角色分布</span></template>
            <div ref="roleChartRef" style="height: 280px"></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 图表区 第二行：趋势 / Top 用户 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="14">
          <el-card shadow="hover">
            <template #header>
              <span>操作趋势（{{ statsDays }} 天）</span>
            </template>
            <div ref="trendChartRef" style="height: 300px"></div>
          </el-card>
        </el-col>
        <el-col :span="10">
          <el-card shadow="hover">
            <template #header><span>Top 活跃用户</span></template>
            <div ref="topUsersChartRef" style="height: 300px"></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 筛选表单 -->
      <el-card shadow="hover" style="margin-top: 16px">
        <template #header><span>日志查询</span></template>
        <el-form :inline="true" style="margin-bottom: 4px">
          <el-form-item label="用户">
            <el-input
              v-model="logFilters.username"
              placeholder="用户名"
              clearable
              style="width: 140px"
              @keyup.enter="onLogSearch"
              @clear="onLogSearch"
            />
          </el-form-item>
          <el-form-item label="操作">
            <el-select
              v-model="logFilters.action"
              placeholder="全部"
              clearable
              style="width: 130px"
              @change="onLogSearch"
            >
              <el-option label="创建" value="create" />
              <el-option label="更新" value="update" />
              <el-option label="删除" value="delete" />
              <el-option label="上传" value="upload" />
              <el-option label="批量导入" value="batch_import" />
              <el-option label="预标注" value="pre_annotate" />
              <el-option label="分配" value="assign" />
              <el-option label="标注" value="annotate" />
              <el-option label="复核" value="review" />
              <el-option label="清洗" value="clean" />
              <el-option label="标准化" value="standardize" />
              <el-option label="下载" value="download" />
              <el-option label="轮换" value="rotate" />
              <el-option label="验证" value="verify" />
              <el-option label="扫描" value="scan" />
            </el-select>
          </el-form-item>
          <el-form-item label="对象">
            <el-select
              v-model="logFilters.target_type"
              placeholder="全部"
              clearable
              style="width: 160px"
              @change="onLogSearch"
            >
              <el-option label="受试者" value="subject" />
              <el-option label="数据资产" value="asset" />
              <el-option label="数据资产" value="data_asset" />
              <el-option label="标注任务" value="annotation_task" />
              <el-option label="标签" value="label" />
              <el-option label="用户" value="user" />
              <el-option label="角色菜单" value="role_menu" />
              <el-option label="角色定义" value="role_def" />
              <el-option label="脱敏配置" value="desensitize_config" />
              <el-option label="数据快照" value="snapshot" />
              <el-option label="规范" value="standard" />
              <el-option label="外部密钥" value="external_key" />
              <el-option label="扫描配置" value="scan_config" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="onLogSearch">查询</el-button>
            <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
          </el-form-item>
        </el-form>

        <!-- 日志列表 -->
        <el-table :data="logList" border stripe size="small" v-loading="logLoading">
          <el-table-column prop="created_at" label="时间" width="170">
            <template #default="{ row }">{{ row.created_at || '—' }}</template>
          </el-table-column>
          <el-table-column prop="username" label="用户名" width="120" />
          <el-table-column prop="role" label="身份" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="roleTagType(row.role)">{{ roleText(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="action" label="操作" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="actionTagType(row.action)">{{ actionText(row.action) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="target_type" label="对象" width="110">
            <template #default="{ row }">{{ logTypeText(row.target_type) }}</template>
          </el-table-column>
          <el-table-column prop="target_id" label="ID" width="70" />
          <el-table-column prop="detail" label="详情" min-width="280" show-overflow-tooltip />
          <el-table-column prop="ip" label="IP" width="120">
            <template #default="{ row }">{{ row.ip || '—' }}</template>
          </el-table-column>
        </el-table>

        <el-pagination
          style="margin-top: 12px; justify-content: flex-end"
          v-model:current-page="logPagination.page"
          v-model:page-size="logPagination.page_size"
          :total="logPagination.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="loadLogs"
          @size-change="loadLogs"
        />
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { Search, RefreshLeft, RefreshRight } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { getOperationLogsApi, getOperationLogStatsApi } from '@/api/data'
import { getRolesApi } from '@/api/system'

const userStore = useUserStore()
const isAdmin = computed(() => userStore.role === 'admin')

// ===== 统计数据 =====
const statsLoading = ref(false)
const statsDays = ref(7)
const stats = ref({
  total: 0,
  today: 0,
  active_users: 0,
  last_n_days: [],
  action_distribution: [],
  target_distribution: [],
  role_distribution: [],
  top_users: [],
  days: 7,
  is_admin: false,
})

// 统计卡片
const recentDaysTotal = computed(() =>
  (stats.value.last_n_days || []).reduce((sum, d) => sum + (d.count || 0), 0)
)
const statCards = computed(() => [
  { title: '操作总数', value: stats.value.total, icon: 'Document', color: '#409eff' },
  { title: '今日操作', value: stats.value.today, icon: 'Calendar', color: '#67c23a' },
  { title: '活跃用户', value: stats.value.active_users, icon: 'UserFilled', color: '#e6a23c' },
  { title: `${stats.value.days} 天操作数`, value: recentDaysTotal.value, icon: 'TrendCharts', color: '#9254de' },
])

// ===== 日志数据 =====
const logLoading = ref(false)
const logList = ref([])
const logFilters = reactive({ username: '', action: '', target_type: '' })
const logPagination = reactive({ page: 1, page_size: 20, total: 0 })

// ===== 角色选项（用于 roleText 显示角色中文名） =====
const roleOptions = ref([])
const systemRoleMap = {
  admin: '管理员', doctor: '医生', annotator: '标注员',
  nurse: '护理人员', engineer: '数据工程师',
}
const loadRoleOptions = async () => {
  try {
    const res = await getRolesApi()
    roleOptions.value = (res.data || []).map((r) => ({
      role_key: r.role_key,
      role_label: r.role_label,
    }))
  } catch (e) {
    roleOptions.value = []
  }
}

// ===== 文本与样式映射 =====
const actionText = (a) => ({
  create: '创建', update: '更新', delete: '删除',
  upload: '上传', batch_import: '批量导入',
  pre_annotate: '预标注', assign: '分配', annotate: '标注', review: '复核',
  clean: '清洗', standardize: '标准化',
  download: '下载', rotate: '轮换', verify: '验证', scan: '扫描',
  import: '导入', backup: '备份', rollback: '回滚', replace: '替换',
  submit: '提交', batch_create: '批量创建', login: '登录',
  unknown: '未知',
}[a] || a || '—')
const actionTagType = (a) => ({
  create: 'success', update: 'warning', delete: 'danger',
  upload: 'primary', batch_import: 'info',
  pre_annotate: 'info', assign: 'primary', annotate: 'success', review: 'warning',
  clean: 'primary', standardize: 'success',
  download: 'info', rotate: 'danger', verify: 'warning', scan: 'info',
  import: 'primary', backup: 'info', rollback: 'danger', replace: 'warning',
  submit: 'success', batch_create: 'success', login: 'info',
}[a] || 'info')
const roleText = (r) => {
  if (!r) return '—'
  const opt = roleOptions.value.find((x) => x.role_key === r)
  if (opt) return opt.role_label
  return systemRoleMap[r] || r
}
const roleTagType = (r) => ({
  admin: 'danger', doctor: 'warning', annotator: 'primary',
  nurse: 'info', engineer: 'success',
}[r] || 'info')
const logTypeText = (t) => ({
  subject: '受试者', asset: '数据资产', data_asset: '数据资产',
  user: '用户', standard: '规范',
  annotation_task: '标注任务', label: '标签',
  role_menu: '角色菜单', role_def: '角色定义',
  desensitize_config: '脱敏配置', snapshot: '数据快照',
  external_key: '外部密钥', scan_config: '扫描配置',
  unknown: '未知',
}[t] || t || '—')

// ===== 图表实例 =====
const actionChartRef = ref()
const targetChartRef = ref()
const roleChartRef = ref()
const trendChartRef = ref()
const topUsersChartRef = ref()
let actionChart, targetChart, roleChart, trendChart, topUsersChart

const initCharts = () => {
  if (actionChartRef.value && !actionChart) actionChart = echarts.init(actionChartRef.value)
  if (targetChartRef.value && !targetChart) targetChart = echarts.init(targetChartRef.value)
  if (roleChartRef.value && !roleChart) roleChart = echarts.init(roleChartRef.value)
  if (trendChartRef.value && !trendChart) trendChart = echarts.init(trendChartRef.value)
  if (topUsersChartRef.value && !topUsersChart) topUsersChart = echarts.init(topUsersChartRef.value)
}

// 操作类型颜色映射（与表格 tag 对齐）
const actionColorMap = {
  create: '#67c23a', update: '#e6a23c', delete: '#f56c6c',
  upload: '#409eff', batch_import: '#909399',
  pre_annotate: '#909399', assign: '#409eff', annotate: '#67c23a', review: '#e6a23c',
  clean: '#409eff', standardize: '#67c23a',
  download: '#909399', rotate: '#f56c6c', verify: '#e6a23c', scan: '#909399',
  import: '#409eff', backup: '#909399', rollback: '#f56c6c', replace: '#e6a23c',
  submit: '#67c23a', batch_create: '#67c23a', login: '#909399',
}

const updateCharts = () => {
  // 操作类型分布 - 玫瑰图
  const actionData = (stats.value.action_distribution || []).map((d) => ({
    name: actionText(d.action),
    value: d.count,
    itemStyle: { color: actionColorMap[d.action] || '#909399' },
  }))
  actionChart?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['25%', '70%'],
      roseType: 'radius',
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: actionData.length ? actionData : [{ name: '暂无数据', value: 1, itemStyle: { color: '#ccc' } }],
    }],
  })

  // 对象类型分布 - 柱状图
  const targetData = (stats.value.target_distribution || []).map((d) => ({
    name: logTypeText(d.target_type),
    value: d.count,
  }))
  targetChart?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 20, bottom: 60 },
    xAxis: {
      type: 'category',
      data: targetData.length ? targetData.map((d) => d.name) : ['暂无数据'],
      axisLabel: { interval: 0, rotate: targetData.length > 4 ? 25 : 0, fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: targetData.length ? targetData.map((d) => d.value) : [0],
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

  // 角色分布 - 环形图
  const roleColorMap = {
    admin: '#f56c6c', doctor: '#e6a23c', annotator: '#409eff',
    nurse: '#909399', engineer: '#67c23a', unknown: '#c0c4cc',
  }
  const roleData = (stats.value.role_distribution || []).map((d) => ({
    name: roleText(d.role),
    value: d.count,
    itemStyle: { color: roleColorMap[d.role] || '#c0c4cc' },
  }))
  roleChart?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['45%', '70%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: roleData.length ? roleData : [{ name: '暂无数据', value: 1, itemStyle: { color: '#ccc' } }],
    }],
  })

  // 操作趋势 - 折线图
  const trendData = stats.value.last_n_days || []
  trendChart?.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 30, top: 30, bottom: 60 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: trendData.map((d) => d.date.slice(5)),
      axisLabel: { rotate: trendData.length > 12 ? 35 : 0, fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      name: '操作数',
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      data: trendData.map((d) => d.count),
      lineStyle: { width: 2, color: '#409eff' },
      itemStyle: { color: '#409eff' },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(64,158,255,0.35)' },
          { offset: 1, color: 'rgba(64,158,255,0.02)' },
        ]),
      },
    }],
  })

  // Top 活跃用户 - 横向条形图
  const topData = (stats.value.top_users || []).slice().reverse()
  topUsersChart?.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 40, top: 20, bottom: 30 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: topData.length ? topData.map((d) => d.username) : ['暂无数据'],
      axisLabel: { fontSize: 11 },
    },
    series: [{
      type: 'bar',
      data: topData.length ? topData.map((d) => d.count) : [0],
      barWidth: '60%',
      itemStyle: {
        color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
          { offset: 0, color: '#9254de' },
          { offset: 1, color: '#13c2c2' },
        ]),
        borderRadius: [0, 4, 4, 0],
      },
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  })
}

// ===== 加载统计 =====
const loadStats = async () => {
  statsLoading.value = true
  try {
    const res = await getOperationLogStatsApi({ days: statsDays.value })
    stats.value = res.data
    await nextTick()
    if (!actionChart) initCharts()
    updateCharts()
  } catch (e) {
    ElMessage.error('加载操作统计失败')
  } finally {
    statsLoading.value = false
  }
}

// ===== 加载日志 =====
const loadLogs = async () => {
  logLoading.value = true
  try {
    const res = await getOperationLogsApi({
      page: logPagination.page,
      page_size: logPagination.page_size,
      username: logFilters.username,
      action: logFilters.action,
      target_type: logFilters.target_type,
    })
    logList.value = res.data.items || []
    logPagination.total = res.data.total || 0
  } catch (e) {
    logList.value = []
    ElMessage.error('加载操作日志失败')
  } finally {
    logLoading.value = false
  }
}

const onLogSearch = () => {
  logPagination.page = 1
  loadLogs()
}

const resetFilters = () => {
  logFilters.username = ''
  logFilters.action = ''
  logFilters.target_type = ''
  logPagination.page = 1
  loadLogs()
}

const reloadAll = () => {
  loadStats()
  loadLogs()
}

// ===== 响应式 =====
const handleResize = () => {
  actionChart?.resize()
  targetChart?.resize()
  roleChart?.resize()
  trendChart?.resize()
  topUsersChart?.resize()
}

// ===== 初始化 =====
onMounted(async () => {
  if (isAdmin.value) {
    loadRoleOptions()
    await loadStats()
    loadLogs()
    window.addEventListener('resize', handleResize)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  actionChart?.dispose()
  targetChart?.dispose()
  roleChart?.dispose()
  trendChart?.dispose()
  topUsersChart?.dispose()
})
</script>

<style scoped>
.page-container {
  padding: 16px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-header .title {
  font-size: 18px;
  font-weight: 600;
}
.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
}
.stat-info {
  flex: 1;
}
.stat-value {
  font-size: 26px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}
.stat-title {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
</style>
