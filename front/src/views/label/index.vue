<template>
  <div class="page-container">
    <div class="page-header">
      <span class="title">标签管理</span>
    </div>

    <el-tabs v-model="activeTab" type="card">
      <!-- 标签库管理 -->
      <el-tab-pane label="标签库" name="library">
        <el-form :inline="true" style="margin-bottom: 12px">
          <el-form-item label="模态">
            <el-select v-model="filters.data_type" placeholder="全部" clearable style="width: 140px" @change="loadLabels">
              <el-option v-for="(t, k) in dataTypeMap" :key="k" :label="t" :value="k" />
            </el-select>
          </el-form-item>
          <el-form-item label="关键词">
            <el-input
              v-model="filters.keyword"
              placeholder="名称/标识"
              clearable
              style="width: 180px"
              @keyup.enter="loadLabels"
              @clear="loadLabels"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="loadLabels">查询</el-button>
            <el-button :icon="RefreshLeft" @click="onResetFilters">重置</el-button>
            <el-button v-if="isAdmin" type="success" :icon="Plus" @click="openCreateDialog">新增标签</el-button>
          </el-form-item>
        </el-form>

        <el-table :data="labelList" border stripe v-loading="tableLoading">
          <el-table-column prop="data_type" label="模态" width="100">
            <template #default="{ row }">
              <el-tag size="small">{{ dataTypeMap[row.data_type] || row.data_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="标签名称" min-width="140" />
          <el-table-column prop="value" label="标识" min-width="160" />
          <el-table-column label="颜色" width="100" align="center">
            <template #default="{ row }">
              <span v-if="row.color" class="color-dot" :style="{ background: row.color }"></span>
              <span v-else style="color: #909399">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.description || '—' }}</template>
          </el-table-column>
          <el-table-column prop="is_active" label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                {{ row.is_active ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="usage_count" label="使用次数" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="row.usage_count > 0 ? 'warning' : 'info'" size="small">{{ row.usage_count || 0 }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="isAdmin" label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" link type="warning" @click="openEditDialog(row)">编辑</el-button>
              <el-button v-if="row.usage_count > 0" size="small" link type="primary" @click="openReplaceDialog(row)">替换</el-button>
              <el-button size="small" link type="danger" @click="removeLabel(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 样本标签查看 -->
      <el-tab-pane label="样本标签" name="sample">
        <el-form :inline="true" style="margin-bottom: 12px">
          <el-form-item label="受试者">
            <el-select
              v-model="sampleFilters.subject_id"
              placeholder="全部"
              clearable
              filterable
              style="width: 180px"
              @change="loadSampleLabels"
            >
              <el-option v-for="s in subjectOptions" :key="s.id" :label="s.pseudo_id" :value="s.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="模态">
            <el-select v-model="sampleFilters.data_type" placeholder="全部" clearable style="width: 140px" @change="onSampleDataTypeChange">
              <el-option v-for="(t, k) in dataTypeMap" :key="k" :label="t" :value="k" />
            </el-select>
          </el-form-item>
          <el-form-item label="标签">
            <el-select v-model="sampleFilters.label" placeholder="全部" clearable filterable style="width: 200px" @change="loadSampleLabels">
              <el-option
                v-for="l in filteredLabelOptions"
                :key="l.value"
                :label="`${l.name} (${l.value})`"
                :value="l.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="loadSampleLabels">查询</el-button>
            <el-button :icon="RefreshLeft" @click="onResetSampleFilters">重置</el-button>
          </el-form-item>
        </el-form>

        <el-table :data="sampleList" border stripe v-loading="sampleLoading">
          <el-table-column prop="annotation_id" label="标注ID" width="90" />
          <el-table-column prop="label" label="标签" min-width="140">
            <template #default="{ row }">
              <el-tag size="small" :type="labelTagType(row.label)">{{ labelDisplayName(row.label) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="subject_pseudo_id" label="受试者" width="130">
            <template #default="{ row }">{{ row.subject_pseudo_id || '—' }}</template>
          </el-table-column>
          <el-table-column prop="asset_name" label="数据资产" min-width="180" show-overflow-tooltip />
          <el-table-column prop="data_type" label="模态" width="90">
            <template #default="{ row }">{{ dataTypeMap[row.data_type] || row.data_type }}</template>
          </el-table-column>
          <el-table-column prop="confidence" label="置信度" width="100">
            <template #default="{ row }">
              <span v-if="row.confidence != null">{{ (row.confidence * 100).toFixed(0) }}%</span>
              <span v-else style="color: #909399">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="task_status" label="任务状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="taskStatusType(row.task_status)">{{ taskStatusText(row.task_status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" min-width="170" />
        </el-table>

        <el-pagination
          style="margin-top: 12px; justify-content: flex-end"
          v-model:current-page="samplePagination.page"
          v-model:page-size="samplePagination.page_size"
          :total="samplePagination.total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="loadSampleLabels"
          @size-change="loadSampleLabels"
        />
      </el-tab-pane>
    </el-tabs>

    <!-- 新增/编辑标签弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑标签' : '新增标签'" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="模态" required>
          <el-select v-model="form.data_type" placeholder="选择模态" style="width: 100%" :disabled="!!editingId">
            <el-option v-for="(t, k) in dataTypeMap" :key="k" :label="t" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签名称" required>
          <el-input v-model="form.name" placeholder="如 人脸关键点" />
        </el-form-item>
        <el-form-item label="标识" required>
          <el-input v-model="form.value" placeholder="如 face_keypoint" :disabled="!!editingId" />
          <div class="form-tip" v-if="!editingId">英文标识，创建后不可修改（修改标识会级联更新已有标注）</div>
        </el-form-item>
        <el-form-item label="颜色">
          <el-color-picker v-model="form.color" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="标签描述（可选）" />
        </el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 编辑标识提示弹窗（编辑时允许改标识，但需提示级联） -->
    <!-- 标签替换弹窗 -->
    <el-dialog v-model="replaceDialog" title="批量替换标签" width="480px">
      <el-alert
        type="warning"
        :closable="false"
        :title="`将把所有使用「${replaceSource.name} (${replaceSource.value})」的标注替换为目标标签，此操作不可撤销。`"
        style="margin-bottom: 12px"
      />
      <el-form label-width="100px">
        <el-form-item label="原标签">
          <el-input :model-value="`${replaceSource.name} (${replaceSource.value})`" disabled />
        </el-form-item>
        <el-form-item label="目标标签" required>
          <el-select v-model="replaceForm.target_value" placeholder="选择目标标签" style="width: 100%">
            <el-option
              v-for="l in replaceTargetOptions"
              :key="l.value"
              :label="`${l.name} (${l.value})`"
              :value="l.value"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="replaceDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleReplace">确认替换</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, RefreshLeft } from '@element-plus/icons-vue'
import {
  getLabelListApi, createLabelApi, updateLabelApi, deleteLabelApi,
  getLabelUsageApi, replaceLabelApi, getSampleLabelsApi,
} from '@/api/label'
import { getSubjectsApi } from '@/api/data'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const isAdmin = computed(() => userStore.role === 'admin')

const dataTypeMap = {
  video: '视频', audio: '音频', eeg: '脑电', ecg: '心电',
  eye: '眼动', gait: '步态', scale: '量表', task: '认知任务',
}

const activeTab = ref('library')

// ===== 标签库 =====
const labelList = ref([])
const tableLoading = ref(false)
const filters = reactive({ data_type: '', keyword: '' })

const loadLabels = async () => {
  tableLoading.value = true
  try {
    const [listRes, usageRes] = await Promise.all([
      getLabelListApi({ data_type: filters.data_type, keyword: filters.keyword }),
      getLabelUsageApi(),
    ])
    const usageMap = {}
    for (const u of (usageRes.data || [])) {
      usageMap[u.value + '|' + u.data_type] = u.usage_count || 0
    }
    labelList.value = (listRes.data || []).map((l) => ({
      ...l,
      usage_count: usageMap[l.value + '|' + l.data_type] || 0,
    }))
  } catch (e) {
    /* ignore */
  } finally {
    tableLoading.value = false
  }
}

const onResetFilters = () => {
  filters.data_type = ''
  filters.keyword = ''
  loadLabels()
}

// 新增/编辑
const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const defaultForm = () => ({
  data_type: 'video',
  name: '',
  value: '',
  color: '#409EFF',
  description: '',
  is_active: true,
})
const form = reactive(defaultForm())

const openCreateDialog = () => {
  editingId.value = null
  Object.assign(form, defaultForm())
  dialogVisible.value = true
}

const openEditDialog = (row) => {
  editingId.value = row.id
  Object.assign(form, {
    data_type: row.data_type,
    name: row.name,
    value: row.value,
    color: row.color || '',
    description: row.description || '',
    is_active: row.is_active,
  })
  dialogVisible.value = true
}

const handleSave = async () => {
  if (!form.name.trim() || !form.value.trim() || !form.data_type) {
    ElMessage.warning('模态、名称、标识均不能为空')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateLabelApi(editingId.value, { ...form })
      ElMessage.success('标签已更新')
    } else {
      await createLabelApi({ ...form })
      ElMessage.success('标签已创建')
    }
    dialogVisible.value = false
    loadLabels()
  } catch (e) {
    /* ignore */
  } finally {
    saving.value = false
  }
}

const removeLabel = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确认删除标签「${row.name} (${row.value})」？`,
      '删除确认',
      { type: 'warning' },
    )
    await deleteLabelApi(row.id)
    ElMessage.success('标签已删除')
    loadLabels()
  } catch (e) {
    /* ignore */
  }
}

// 标签替换
const replaceDialog = ref(false)
const replaceSource = reactive({ id: null, name: '', value: '', data_type: '' })
const replaceForm = reactive({ target_value: '' })
const replaceTargetOptions = ref([])

const openReplaceDialog = (row) => {
  replaceSource.id = row.id
  replaceSource.name = row.name
  replaceSource.value = row.value
  replaceSource.data_type = row.data_type
  replaceForm.target_value = ''
  // 同模态的其他标签作为替换目标
  replaceTargetOptions.value = labelList.value.filter(
    (l) => l.data_type === row.data_type && l.id !== row.id,
  )
  replaceDialog.value = true
}

const handleReplace = async () => {
  if (!replaceForm.target_value) {
    ElMessage.warning('请选择目标标签')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认将所有「${replaceSource.name}」标签替换为所选目标标签？此操作不可撤销。`,
      '替换确认',
      { type: 'warning' },
    )
    saving.value = true
    const res = await replaceLabelApi(replaceSource.id, { target_value: replaceForm.target_value })
    ElMessage.success(`已替换 ${res.data.affected} 条标注`)
    replaceDialog.value = false
    loadLabels()
  } catch (e) {
    /* ignore */
  } finally {
    saving.value = false
  }
}

// ===== 样本标签查看 =====
const sampleList = ref([])
const sampleLoading = ref(false)
const subjectOptions = ref([])
const sampleFilters = reactive({ subject_id: null, data_type: '', label: '' })
const samplePagination = reactive({ page: 1, page_size: 20, total: 0 })

// 用于样本标签筛选的标签下拉（按所选模态过滤）
const filteredLabelOptions = computed(() => {
  if (!sampleFilters.data_type) return labelList.value
  return labelList.value.filter((l) => l.data_type === sampleFilters.data_type)
})

const loadSubjects = async () => {
  try {
    const res = await getSubjectsApi({ page: 1, page_size: 100 })
    subjectOptions.value = res.data?.items || []
  } catch (e) { /* ignore */ }
}

const loadSampleLabels = async () => {
  sampleLoading.value = true
  try {
    const res = await getSampleLabelsApi({
      subject_id: sampleFilters.subject_id || undefined,
      data_type: sampleFilters.data_type || undefined,
      label: sampleFilters.label || undefined,
      page: samplePagination.page,
      page_size: samplePagination.page_size,
    })
    sampleList.value = res.data?.items || []
    samplePagination.total = res.data?.total || 0
  } catch (e) {
    /* ignore */
  } finally {
    sampleLoading.value = false
  }
}

const onSampleDataTypeChange = () => {
  sampleFilters.label = ''
  loadSampleLabels()
}

const onResetSampleFilters = () => {
  sampleFilters.subject_id = null
  sampleFilters.data_type = ''
  sampleFilters.label = ''
  samplePagination.page = 1
  loadSampleLabels()
}

// 辅助函数
const labelNameMap = computed(() => {
  const m = {}
  for (const l of labelList.value) {
    m[l.value] = l.name
  }
  return m
})
const labelDisplayName = (val) => labelNameMap.value[val] || val

const labelTagType = (val) => {
  const colors = ['primary', 'success', 'warning', 'danger', 'info', 'primary']
  let hash = 0
  for (let i = 0; i < (val || '').length; i++) hash = (hash * 31 + val.charCodeAt(i)) & 0xffffffff
  return colors[Math.abs(hash) % colors.length]
}

const taskStatusMap = {
  pending: '待预标注', pre_annotated: '已预标注', assigning: '分配中',
  annotating: '标注中', annotated: '已标注', reviewing: '复核中',
  rejected: '已驳回', approved: '已通过',
}
const taskStatusText = (s) => taskStatusMap[s] || s || '—'
const taskStatusType = (s) => ({
  pending: 'info', pre_annotated: 'info', assigning: 'primary',
  annotating: 'warning', annotated: 'success', reviewing: 'warning',
  rejected: 'danger', approved: 'success',
}[s] || 'info')

onMounted(() => {
  loadLabels()
  loadSubjects()
  loadSampleLabels()
})
</script>

<style scoped>
.color-dot {
  display: inline-block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  vertical-align: middle;
  border: 1px solid #dcdfe6;
}
.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
  margin-top: 4px;
}
</style>
