<template>
  <div class="page-container">
    <div class="page-header">
      <span class="title">数据清洗及标准化</span>
      <el-space>
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </el-space>
    </div>

    <!-- 批量操作模式选择 -->
    <div class="batch-bar">
      <el-radio-group v-model="batchMode" size="small" @change="onBatchModeChange">
        <el-radio-button value="">单条查看</el-radio-button>
        <el-radio-button v-if="canClean" value="clean">批量清洗</el-radio-button>
        <el-radio-button v-if="canClean" value="standard">批量标准化</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-icon :size="28" color="#409eff"><DataAnalysis /></el-icon>
          <div class="stat-info">
            <div class="stat-value">{{ statFailed ? '—' : stat.total }}</div>
            <div class="stat-label">数据资产总数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-icon :size="28" color="#e6a23c"><Clock /></el-icon>
          <div class="stat-info">
            <div class="stat-value">{{ statFailed ? '—' : stat.pending }}</div>
            <div class="stat-label">待清洗</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-icon :size="28" color="#67c23a"><CircleCheck /></el-icon>
          <div class="stat-info">
            <div class="stat-value">{{ statFailed ? '—' : stat.cleaned }}</div>
            <div class="stat-label">已清洗</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-icon :size="28" color="#f56c6c"><Warning /></el-icon>
          <div class="stat-info">
            <div class="stat-value">{{ statFailed ? '—' : stat.unqualified }}</div>
            <div class="stat-label">质量不合格</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 可视化图表 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="12">
        <el-card>
          <template #header><span>模态类型分布</span></template>
          <div ref="typePieRef" style="height: 240px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header><span>清洗状态分布</span></template>
          <div ref="statusBarRef" style="height: 240px"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" type="card">
      <!-- 数据接入 -->
      <el-tab-pane label="数据接入" name="access">
        <!-- 筛选栏 -->
        <el-form :inline="true" style="margin-bottom: 12px">
          <el-form-item label="模态类型">
            <el-select v-model="filter.data_type" placeholder="全部" clearable style="width: 130px" @change="onSearch">
              <el-option label="视频" value="video" />
              <el-option label="音频" value="audio" />
              <el-option label="脑电" value="eeg" />
              <el-option label="心电" value="ecg" />
              <el-option label="眼动" value="eye" />
              <el-option label="步态" value="gait" />
              <el-option label="量表" value="scale" />
              <el-option label="认知任务" value="task" />
            </el-select>
          </el-form-item>
          <el-form-item label="分层">
            <el-select v-model="filter.layer" placeholder="全部" clearable style="width: 100px" @change="onSearch">
              <el-option label="原始" value="raw" />
              <el-option label="清洗" value="cleaned" />
              <el-option label="特征" value="feature" />
              <el-option label="标注" value="annotation" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="filter.status" placeholder="全部" clearable style="width: 120px" @change="onSearch">
              <el-option label="已上传" value="uploaded" />
              <el-option label="清洗中" value="cleaning" />
              <el-option label="已清洗" value="cleaned" />
              <el-option label="已标准化" value="standardized" />
            </el-select>
          </el-form-item>
          <el-form-item label="关键词">
            <el-input v-model="filter.keyword" placeholder="文件名" clearable style="width: 160px" @keyup.enter="onSearch" @clear="onSearch" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="onSearch">查询</el-button>
            <el-button :icon="RefreshLeft" @click="onResetFilter">重置</el-button>
          </el-form-item>
        </el-form>

        <!-- 资产表格（多选） -->
        <el-card shadow="never" style="margin-top: 12px">
          <template v-if="batchMode" #header>
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
              <span>数据资产列表</span>
              <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
                <el-button type="primary" plain :icon="Check" :loading="selectAllLoading" @click="selectAllAcrossPages">
                  跨页全选
                </el-button>
                <el-button :icon="CircleClose" :disabled="!selectedAssets.length" @click="clearSelection">
                  清空选择
                </el-button>
                <el-tag v-if="selectedAssets.length" type="warning" effect="plain">
                  已选 {{ selectedAssets.length }} 个文件<span v-if="selectedAssets.length > displayAssetList.length">（含其他页）</span>
                </el-tag>
                <el-button v-if="batchMode === 'clean'" type="primary" :icon="MagicStick" :disabled="!selectedAssets.length" @click="openCleanDialog">
                  执行清洗<span v-if="selectedAssets.length"> ({{ selectedAssets.length }})</span>
                </el-button>
                <el-button v-if="batchMode === 'standard'" type="success" :icon="Check" :disabled="!selectedAssets.length" @click="openStdDialog">
                  执行标准化<span v-if="selectedAssets.length"> ({{ selectedAssets.length }})</span>
                </el-button>
              </div>
            </div>
          </template>

        <el-table
          :data="displayAssetList"
          border
          stripe
          v-loading="loading"
          empty-text="暂无数据资产，请前往数据管理页接入"
          @selection-change="onSelectionChange"
          :row-key="(row) => row.id"
          ref="assetTableRef"
        >
          <el-table-column v-if="batchMode" type="selection" width="42" />
          <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip header-align="center" />
          <el-table-column prop="data_type" label="模态类型" width="100" align="center" header-align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="dataTypeTagType(row.data_type)">{{ dataTypeMap[row.data_type] || row.data_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="layer" label="分层" width="80" align="center" header-align="center">
            <template #default="{ row }">{{ layerMap[row.layer] || row.layer }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100" align="center" header-align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="statusType(row.status)">{{ statusMap[row.status] || row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="is_qualified" label="质量" width="80" align="center" header-align="center">
            <template #default="{ row }">
              <el-tag :type="row.is_qualified ? 'success' : 'danger'" size="small">
                {{ row.is_qualified ? '合格' : '不合格' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="file_format" label="格式" width="80" align="center" header-align="center">
            <template #default="{ row }">{{ row.file_format || '—' }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="170" header-align="center" />
        </el-table>

        <!-- 分页 -->
        <el-pagination
          style="margin-top: 12px; justify-content: flex-end"
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :total="pagination.total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="loadData"
          @size-change="loadData"
        />
        </el-card>
      </el-tab-pane>

      <!-- 清洗配置 -->
      <el-tab-pane label="清洗配置" name="cleaning">
        <el-form label-width="160px" style="max-width: 600px">
          <el-divider content-position="left">缺失值处理</el-divider>
          <el-form-item label="缺失值处理策略">
            <el-select v-model="cleanConfig.missing" style="width: 100%">
              <el-option label="必填缺失触发重采" value="resample" />
              <el-option label="KNN 填补" value="knn" />
              <el-option label="均值填补" value="mean" />
              <el-option label="中位数填补" value="median" />
            </el-select>
          </el-form-item>
          <el-divider content-position="left">异常值检测</el-divider>
          <el-form-item label="异常值检测算法">
            <el-checkbox-group v-model="cleanConfig.outlier">
              <el-checkbox value="3sigma">3σ 准则</el-checkbox>
              <el-checkbox value="iqr">IQR 四分位距</el-checkbox>
              <el-checkbox value="isolation_forest">孤立森林</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-divider content-position="left">信号去噪</el-divider>
          <el-form-item label="去噪模态">
            <el-checkbox-group v-model="cleanConfig.denoise">
              <el-checkbox value="eeg">脑电（陷波+ICA）</el-checkbox>
              <el-checkbox value="ecg">心电（基线漂移）</el-checkbox>
              <el-checkbox value="eye">眼动（小波）</el-checkbox>
              <el-checkbox value="gait">步态（小波）</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleSaveCleanConfig">保存配置</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 标准化配置 -->
      <el-tab-pane label="标准化配置" name="standardization">
        <el-form label-width="160px" style="max-width: 600px">
          <el-divider content-position="left">采样率统一</el-divider>
          <el-form-item label="脑电 EEG">
            <el-input-number v-model="stdConfig.eeg_rate" :min="1" /> Hz
          </el-form-item>
          <el-form-item label="心电 ECG">
            <el-input-number v-model="stdConfig.ecg_rate" :min="1" /> Hz
          </el-form-item>
          <el-form-item label="眼动">
            <el-input-number v-model="stdConfig.eye_rate" :min="1" /> Hz
          </el-form-item>
          <el-form-item label="步态">
            <el-input-number v-model="stdConfig.gait_rate" :min="1" /> Hz
          </el-form-item>
          <el-divider content-position="left">时间戳与格式</el-divider>
          <el-form-item label="时间戳格式">
            <el-tag>UTC ISO8601 毫秒级</el-tag>
          </el-form-item>
          <el-form-item label="视频编码">
            <el-tag>H.264 / MP4 / 1080P / 25fps</el-tag>
          </el-form-item>
          <el-form-item label="音频编码">
            <el-tag>AAC / WAV / 16kHz / 单声道 / 16bit</el-tag>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleSaveStdConfig">保存配置</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- 清洗确认弹窗 -->
    <el-dialog v-model="cleanDialog" title="确认清洗" width="640px">
      <el-alert
        type="info"
        :closable="false"
        title="请勾选需要清洗的数据资产，点击下方按钮执行清洗任务"
        style="margin-bottom: 12px"
      />
      <el-table :data="selectedAssets" border size="small" max-height="320">
        <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column prop="data_type" label="类型" width="80">
          <template #default="{ row }">{{ dataTypeMap[row.data_type] || row.data_type }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">{{ statusMap[row.status] || row.status }}</template>
        </el-table-column>
      </el-table>
      <el-form :inline="true" style="margin-top: 12px">
        <el-form-item label="缺失值策略">
          <el-select v-model="cleanConfig.missing" style="width: 200px">
            <el-option label="必填缺失触发重采" value="resample" />
            <el-option label="KNN 填补" value="knn" />
            <el-option label="均值填补" value="mean" />
            <el-option label="中位数填补" value="median" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cleanDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="confirmClean">执行清洗</el-button>
      </template>
    </el-dialog>

    <!-- 标准化确认弹窗 -->
    <el-dialog v-model="stdDialog" title="确认标准化" width="640px">
      <el-alert
        type="info"
        :closable="false"
        title="请勾选需要标准化的数据资产，点击下方按钮执行标准化任务"
        style="margin-bottom: 12px"
      />
      <el-table :data="selectedAssets" border size="small" max-height="320">
        <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column prop="data_type" label="类型" width="80">
          <template #default="{ row }">{{ dataTypeMap[row.data_type] || row.data_type }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">{{ statusMap[row.status] || row.status }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="stdDialog = false">取消</el-button>
        <el-button type="success" :loading="submitting" @click="confirmStd">执行标准化</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Refresh, RefreshLeft, Search, MagicStick, Check, DataAnalysis, Clock, CircleCheck, Warning, CircleClose } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getAssetsApi, triggerCleaningApi, triggerStandardizationApi } from '@/api/data'
import { getDashboardStatsApi } from '@/api/dashboard'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canClean = computed(() => ['admin', 'engineer'].includes(userStore.role))

const activeTab = ref('access')
const assetList = ref([])
const loading = ref(false)
const submitting = ref(false)
const selectedAssets = ref([])
const batchMode = ref('')
const assetTableRef = ref()

// 批量操作模式：可选性判断（保留给 el-table-column :selectable，避免误选）
const isAssetSelectable = () => true  // 过滤后展示的都是可选的，直接返回 true
// 批量模式下按状态过滤显示的资产列表
const displayAssetList = computed(() => {
  if (!batchMode.value) return assetList.value
  if (batchMode.value === 'clean') {
    return assetList.value.filter((r) => r.status === 'uploaded')
  }
  if (batchMode.value === 'standard') {
    return assetList.value.filter((r) => r.status === 'cleaned')
  }
  return assetList.value
})
const onBatchModeChange = () => {
  clearSelection()
}
const clearSelection = () => {
  selectedAssets.value = []
  assetTableRef.value?.clearSelection()
}

// 筛选与分页
const filter = reactive({ data_type: '', layer: '', status: '', keyword: '' })
const pagination = reactive({ page: 1, page_size: 20, total: 0 })

// 统计
const stat = reactive({ total: 0, pending: 0, cleaned: 0, unqualified: 0 })
// 全量统计（来自 dashboard stats，避免当前页数据失真）
const dashboardStat = reactive({ asset_status_distribution: {}, qualified_rate: 0, asset_total: 0 })
// 统计加载失败标记：失败时卡片显示"—"而不是误导性的 0
const statFailed = ref(false)

const loadDashboardStat = async () => {
  try {
    const res = await getDashboardStatsApi()
    const d = res.data || {}
    dashboardStat.asset_status_distribution = d.asset_status_distribution || {}
    dashboardStat.qualified_rate = d.qualified_rate || 0
    dashboardStat.asset_total = d.asset_total || 0
    statFailed.value = false
    updateStat()
  } catch (e) {
    statFailed.value = true
    // 请求失败已由全局拦截器统一提示；此处补充说明统计不可用
    ElMessage.warning('全局统计加载失败，统计卡片暂不可用')
  }
}

// 可视化
const typePieRef = ref()
const statusBarRef = ref()
let typePieChart = null
let statusBarChart = null

const cleanConfig = ref({
  missing: 'knn',
  outlier: ['3sigma', 'iqr'],
  denoise: ['eeg', 'ecg'],
})

const stdConfig = ref({
  eeg_rate: 250,
  ecg_rate: 1000,
  eye_rate: 100,
  gait_rate: 50,
})

const layerMap = {
  raw: '原始', cleaned: '清洗', feature: '特征', annotation: '标注',
}

const statusMap = {
  uploaded: '已上传', cleaning: '清洗中', cleaned: '已清洗', standardized: '已标准化',
}

const dataTypeMap = {
  video: '视频', audio: '音频', eeg: '脑电', ecg: '心电',
  eye: '眼动', gait: '步态', scale: '量表', task: '认知任务',
}

const statusType = (status) => {
  const map = { uploaded: 'info', cleaning: 'warning', cleaned: 'success', standardized: 'success' }
  return map[status] || 'info'
}

const dataTypeTagType = (t) => {
  const map = { video: '', audio: 'success', eeg: 'warning', ecg: 'danger', eye: 'info', gait: '', scale: 'success', task: 'warning' }
  return map[t] || ''
}

// 跨页选中：selectedAssets 作为单一数据源，ID 集合驱动
let _suppressSelectionChange = false

const onSelectionChange = (rows) => {
  if (_suppressSelectionChange) return
  const currentPageIds = new Set(displayAssetList.value.map((r) => r.id))
  const kept = selectedAssets.value.filter((r) => !currentPageIds.has(r.id))
  const pageRows = rows.map((r) => ({ ...r, _placeholder: false }))
  const seen = new Set(kept.map((r) => r.id))
  const merged = [...kept]
  for (const r of pageRows) {
    if (!seen.has(r.id)) {
      seen.add(r.id)
      merged.push(r)
    }
  }
  selectedAssets.value = merged
}

const _syncTableSelection = () => {
  if (!assetTableRef.value) return
  _suppressSelectionChange = true
  try {
    const selectedIds = new Set(selectedAssets.value.map((r) => r.id))
    displayAssetList.value.forEach((row) => {
      assetTableRef.value.toggleRowSelection(row, selectedIds.has(row.id))
    })
  } finally {
    _suppressSelectionChange = false
  }
}

// 跨页全选按钮：取当前筛选条件下所有页的 ID，全部加入 selectedAssets
const selectAllLoading = ref(false)
const selectAllAcrossPages = async () => {
  selectAllLoading.value = true
  try {
    const res = await getAssetsApi({
      page: 1,
      page_size: 1,
      data_type: filter.data_type,
      layer: filter.layer,
      keyword: filter.keyword,
      ids_only: true,
    })
    const allIds = (res.data?.items || [])
      .filter((x) => x.data_type !== 'json')
      .map((x) => x.id)
    if (!allIds.length) {
      ElMessage.warning('当前筛选条件下无可选数据')
      return
    }
    const allIdSet = new Set(allIds)
    const existingNotInFilter = selectedAssets.value.filter((r) => !allIdSet.has(r.id))
    const newSelected = allIds.map((id) => {
      const existing = selectedAssets.value.find((r) => r.id === id)
      return existing || { id, _placeholder: true }
    })
    selectedAssets.value = [...existingNotInFilter, ...newSelected]
    ElMessage.success(`已跨页全选 ${allIds.length} 个文件`)
    nextTick(() => _syncTableSelection())
  } catch (e) {
    // 接口失败时静默
  } finally {
    selectAllLoading.value = false
  }
}

const onSearch = () => {
  pagination.page = 1
  loadData()
}

const onResetFilter = () => {
  filter.data_type = ''
  filter.layer = ''
  filter.status = ''
  filter.keyword = ''
  pagination.page = 1
  loadData()
}

const loadData = async () => {
  loading.value = true
  try {
    const res = await getAssetsApi({
      page: pagination.page,
      page_size: pagination.page_size,
      data_type: filter.data_type,
      layer: filter.layer,
      keyword: filter.keyword,
    })
    // 清洗流程忽略 json 模态（userInfo 备份等辅助文件）
    assetList.value = (res.data.items || []).filter((a) => a.data_type !== 'json')
    pagination.total = res.data.total || 0
    updateStat()
    updateCharts()
    // 翻页后同步当前页勾选状态
    nextTick(() => _syncTableSelection())
  } catch (e) {
    assetList.value = []
  } finally {
    loading.value = false
  }
}

const updateStat = () => {
  const dist = dashboardStat.asset_status_distribution || {}
  const total = dashboardStat.asset_total || 0
  const qualified = total ? Math.round(dashboardStat.qualified_rate * total) : 0
  stat.total = total
  stat.pending = dist.uploaded || 0
  stat.cleaned = (dist.cleaned || 0) + (dist.standardized || 0)
  stat.unqualified = Math.max(0, total - qualified)
}

const initCharts = () => {
  if (typePieRef.value && !typePieChart) typePieChart = echarts.init(typePieRef.value)
  if (statusBarRef.value && !statusBarChart) statusBarChart = echarts.init(statusBarRef.value)
}

const updateCharts = () => {
  // 模态类型分布饼图
  if (typePieChart) {
    const counts = {}
    assetList.value.forEach((a) => {
      const t = dataTypeMap[a.data_type] || a.data_type || '未知'
      counts[t] = (counts[t] || 0) + 1
    })
    const data = Object.entries(counts).map(([name, value]) => ({ name, value }))
    typePieChart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        type: 'pie', radius: ['40%', '70%'], center: ['50%', '45%'],
        label: { formatter: '{b}\n{c}' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e9ecef' } }],
      }],
    }, true)
  }
  // 清洗状态分布柱图
  if (statusBarChart) {
    const counts = {}
    assetList.value.forEach((a) => {
      const s = statusMap[a.status] || a.status || '未知'
      counts[s] = (counts[s] || 0) + 1
    })
    const entries = Object.entries(counts)
    statusBarChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 16, top: 24, bottom: 32 },
      xAxis: { type: 'category', data: entries.map((e) => e[0]) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{
        type: 'bar', barWidth: '50%',
        data: entries.map((e) => e[1]),
        itemStyle: { color: '#409eff' },
        label: { show: true, position: 'top' },
      }],
    }, true)
  }
}

const handleResize = () => {
  typePieChart?.resize()
  statusBarChart?.resize()
}

// 清洗弹窗
const cleanDialog = ref(false)
const openCleanDialog = () => {
  if (!selectedAssets.value.length) {
    ElMessage.warning('请先在数据接入表格中勾选要清洗的数据')
    return
  }
  cleanDialog.value = true
}

const confirmClean = async () => {
  submitting.value = true
  try {
    const ids = selectedAssets.value.map((a) => a.id)
    const res = await triggerCleaningApi({ asset_ids: ids, ...cleanConfig.value })
    const count = res.data?.count ?? ids.length
    const skipped = res.data?.skipped ?? []
    let msg = `清洗完成，共处理 ${count} 个数据资产`
    if (skipped.length) msg += `，跳过 ${skipped.length} 个`
    ElMessage.success(msg)
    cleanDialog.value = false
    clearSelection()
    loadData()
    loadDashboardStat()
  } catch (e) { /* 拦截器已提示 */ }
  finally { submitting.value = false }
}

// 标准化弹窗
const stdDialog = ref(false)
const openStdDialog = () => {
  if (!selectedAssets.value.length) {
    ElMessage.warning('请先在数据接入表格中勾选要标准化的数据')
    return
  }
  stdDialog.value = true
}

const confirmStd = async () => {
  submitting.value = true
  try {
    const ids = selectedAssets.value.map((a) => a.id)
    const res = await triggerStandardizationApi({ asset_ids: ids, ...stdConfig.value })
    const count = res.data?.count ?? ids.length
    const skipped = res.data?.skipped ?? []
    let msg = `标准化完成，共处理 ${count} 个数据资产`
    if (skipped.length) msg += `，跳过 ${skipped.length} 个`
    ElMessage.success(msg)
    stdDialog.value = false
    clearSelection()
    loadData()
    loadDashboardStat()
  } catch (e) { /* 拦截器已提示 */ }
  finally { submitting.value = false }
}

// 保存配置（仅提示，不执行清洗）
const handleSaveCleanConfig = () => {
  ElMessage.success('清洗配置已保存')
}
const handleSaveStdConfig = () => {
  ElMessage.success('标准化配置已保存')
}

onMounted(async () => {
  await loadData()
  loadDashboardStat()
  await nextTick()
  initCharts()
  updateCharts()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  typePieChart?.dispose()
  statusBarChart?.dispose()
})
</script>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
  padding: 12px 16px;
}
.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}
.stat-info {
  flex: 1;
}
.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}
.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #f5f7fa;
  border-radius: 4px;
  flex-wrap: wrap;
}
.batch-info {
  font-size: 13px;
  color: #409eff;
  font-weight: 500;
}
</style>

<style lang="scss">
/* 禁用行样式（数据清洗页面，非 scoped） */
.el-table .row-disabled {
  opacity: 0.45;
  background-color: #f5f5f5 !important;
  .el-checkbox__input.is-disabled .el-checkbox__inner {
    background-color: #dcdfe6;
    border-color: #c0c4cc;
  }
  .el-checkbox__input.is-disabled .el-checkbox__inner::after {
    border-color: #c0c4cc;
  }
  td {
    color: #909399;
  }
}
</style>
