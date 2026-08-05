<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="$emit('update:visible', $event)"
    title="版本管理 - 全局修改历史"
    width="960px"
    @open="loadSnapshots"
  >
    <el-alert
      type="info"
      :closable="false"
      title="所有数据的修改/删除/回滚均会自动留档。可按类型筛选，点击「回滚」恢复数据（回滚前会自动留档当前状态）。"
      style="margin-bottom: 12px"
    />

    <!-- 筛选栏 -->
    <el-form :inline="true" style="margin-bottom: 12px">
      <el-form-item label="数据类型">
        <el-select v-model="filters.model_type" placeholder="全部" clearable style="width: 150px" @change="loadSnapshots">
          <el-option v-for="(t, k) in modelTypeMap" :key="k" :label="t" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="操作">
        <el-select v-model="filters.action" placeholder="全部" clearable style="width: 120px" @change="loadSnapshots">
          <el-option label="新增" value="create" />
          <el-option label="修改" value="update" />
          <el-option label="删除" value="delete" />
          <el-option label="回滚" value="rollback" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :icon="Search" @click="loadSnapshots">查询</el-button>
        <el-button :icon="RefreshLeft" @click="onReset">重置</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="snapshots" border v-loading="loading" size="small" empty-text="暂无历史记录">
      <el-table-column prop="model_type" label="数据类型" width="120">
        <template #default="{ row }">
          <el-tag size="small">{{ modelTypeMap[row.model_type] || row.model_type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="model_id" label="记录ID" width="80" align="center" />
      <el-table-column prop="version_no" label="版本" width="70" align="center">
        <template #default="{ row }">v{{ row.version_no }}</template>
      </el-table-column>
      <el-table-column prop="action" label="操作" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="actionTagType(row.action)">{{ actionText(row.action) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="operator_name" label="操作人" width="100">
        <template #default="{ row }">{{ row.operator_name || '—' }}</template>
      </el-table-column>
      <el-table-column prop="change_summary" label="变更摘要" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.change_summary || '—' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="留档时间" width="160" />
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link @click="viewDetail(row)">查看</el-button>
          <el-button
            v-if="isAdmin && row.action !== 'delete'"
            size="small"
            link
            type="warning"
            @click="handleRollback(row)"
          >
            回滚
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top: 12px; justify-content: flex-end"
      v-model:current-page="pagination.page"
      v-model:page-size="pagination.page_size"
      :total="pagination.total"
      :page-sizes="[10, 20, 50]"
      layout="total, sizes, prev, pager, next"
      @current-change="loadSnapshots"
      @size-change="loadSnapshots"
    />

    <!-- 快照详情弹窗 -->
    <el-dialog
      v-model="detailVisible"
      title="快照数据详情"
      width="640px"
      append-to-body
    >
      <el-alert
        v-if="detailData"
        type="info"
        :closable="false"
        :title="`${modelTypeMap[detailData.model_type] || detailData.model_type} #${detailData.model_id} · v${detailData.version_no} · ${actionText(detailData.action)} · ${detailData.operator_name || '—'} · ${detailData.created_at}`"
        style="margin-bottom: 12px"
      />
      <el-descriptions v-if="detailData" :column="2" border size="small">
        <el-descriptions-item
          v-for="(val, key) in detailData.snapshot"
          :key="key"
          :label="fieldLabel(key)"
          :span="isLongVal(val) ? 2 : 1"
        >
          <span class="snap-val">{{ formatVal(val) }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft } from '@element-plus/icons-vue'
import { getSnapshotsApi, getSnapshotApi, rollbackSnapshotApi } from '@/api/data'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'rollback-success'])

const userStore = useUserStore()
const isAdmin = computed(() => userStore.role === 'admin')

const modelTypeMap = {
  subject: '受试者',
  data_asset: '数据资产',
  user: '用户',
  data_standard: '数据规范',
  annotation_task: '标注任务',
}

const snapshots = ref([])
const loading = ref(false)
const filters = reactive({ model_type: '', action: '' })
const pagination = reactive({ page: 1, page_size: 20, total: 0 })

const loadSnapshots = async () => {
  loading.value = true
  try {
    const res = await getSnapshotsApi({
      model_type: filters.model_type || undefined,
      action: filters.action || undefined,
      page: pagination.page,
      page_size: pagination.page_size,
    })
    snapshots.value = res.data?.items || res.data || []
    pagination.total = res.data?.total || snapshots.value.length
  } catch (e) {
    /* ignore */
  } finally {
    loading.value = false
  }
}

const onReset = () => {
  filters.model_type = ''
  filters.action = ''
  pagination.page = 1
  loadSnapshots()
}

// 详情
const detailVisible = ref(false)
const detailData = ref(null)
const viewDetail = async (row) => {
  try {
    const res = await getSnapshotApi(row.id)
    detailData.value = res.data || row
    detailVisible.value = true
  } catch (e) {
    detailData.value = row
    detailVisible.value = true
  }
}

// 回滚
const handleRollback = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确认将「${modelTypeMap[row.model_type] || row.model_type} #${row.model_id}」回滚到 v${row.version_no}？回滚前会自动留档当前状态。`,
      '回滚确认',
      { type: 'warning' },
    )
    await rollbackSnapshotApi(row.id)
    ElMessage.success('回滚成功')
    emit('rollback-success')
    loadSnapshots()
  } catch (e) {
    /* ignore */
  }
}

// 辅助
const actionText = (a) => ({ create: '新增', update: '修改', delete: '删除', rollback: '回滚' }[a] || a)
const actionTagType = (a) => ({ create: 'success', update: 'warning', delete: 'danger', rollback: 'primary' }[a] || 'info')
const isLongVal = (v) => typeof v === 'string' && v.length > 30
const formatVal = (v) => {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

const fieldLabel = (key) => {
  const labels = detailData.value?.field_labels || {}
  return labels[key] || String(key)
}
</script>

<style scoped>
.snap-val {
  word-break: break-all;
  white-space: pre-wrap;
}
</style>
