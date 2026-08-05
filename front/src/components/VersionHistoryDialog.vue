<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="$emit('update:visible', $event)"
    :title="`${modelLabel || modelType} 历史版本`"
    width="820px"
    @open="loadSnapshots"
  >
    <el-alert
      type="info"
      :closable="false"
      title="每次修改/删除都会自动留档。点击「回滚」可将数据恢复到该版本（回滚前会自动留档当前状态，可再次回滚）。"
      style="margin-bottom: 12px"
    />
    <el-table :data="snapshots" border v-loading="loading" size="small" empty-text="暂无历史版本">
      <el-table-column prop="version_no" label="版本" width="70" align="center">
        <template #default="{ row }">v{{ row.version_no }}</template>
      </el-table-column>
      <el-table-column prop="action" label="操作" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="actionTagType(row.action)">{{ actionText(row.action) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="operator_name" label="操作人" width="110">
        <template #default="{ row }">{{ row.operator_name || '—' }}</template>
      </el-table-column>
      <el-table-column prop="change_summary" label="变更摘要" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ row.change_summary || '—' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="留档时间" width="160" />
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link @click="viewDetail(row)">查看快照</el-button>
          <el-button
            v-if="isAdmin"
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
        :title="`版本 v${detailData.version_no} · ${detailData.action} · ${detailData.operator_name || '—'} · ${detailData.created_at}`"
        style="margin-bottom: 12px"
      />
      <el-descriptions v-if="detailData" :column="1" border size="small">
        <el-descriptions-item
          v-for="(val, key) in detailData.snapshot || {}"
          :key="key"
          :label="fieldLabel(key)"
        >
          <span style="word-break: break-all">{{ formatVal(val) }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="暂无数据" />
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSnapshotsApi, rollbackSnapshotApi } from '@/api/data'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  visible: { type: Boolean, default: false },
  modelType: { type: String, required: true },
  modelId: { type: [Number, String], default: null },
  modelLabel: { type: String, default: '' },
  // 业务唯一标识（如 subject 的 pseudo_id），用于过滤因 id 复用导致的脏快照
  modelKey: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'rollback-success'])

const userStore = useUserStore()
const isAdmin = computed(() => userStore.role === 'admin')

const snapshots = ref([])
const loading = ref(false)
const detailVisible = ref(false)
const detailData = ref(null)

const loadSnapshots = async () => {
  if (!props.modelId) {
    snapshots.value = []
    return
  }
  loading.value = true
  try {
    const res = await getSnapshotsApi({
      model_type: props.modelType,
      model_id: props.modelId,
      page: 1,
      page_size: 50,
    })
    let items = res.data.items || []
    // 业务标识二次过滤：避免数据库 id 复用导致显示其他记录的历史
    // subject 用 pseudo_id，data_asset 用 file_name
    if (props.modelKey) {
      const keyField = props.modelType === 'subject' ? 'pseudo_id'
        : props.modelType === 'data_asset' ? 'file_name'
        : ''
      if (keyField) {
        items = items.filter(s => {
          const snap = s.snapshot || {}
          const snapKey = snap[keyField]
          // 创建快照的 action=create 可能没有 snapshot 数据，保留
          if (!snapKey) return true
          return String(snapKey) === String(props.modelKey)
        })
      }
    }
    snapshots.value = items
  } catch (e) {
    snapshots.value = []
  } finally {
    loading.value = false
  }
}

const viewDetail = (row) => {
  detailData.value = row
  detailVisible.value = true
}

const handleRollback = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确认将数据回滚到 v${row.version_no}（${row.created_at} 的状态）？回滚前当前状态会自动留档，可再次回滚。`,
      '回滚确认',
      { type: 'warning' }
    )
  } catch (e) {
    return
  }
  try {
    await rollbackSnapshotApi(row.id)
    ElMessage.success('已回滚到历史版本')
    emit('rollback-success')
    loadSnapshots()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

const actionText = (a) => ({
  create: '创建', update: '修改', delete: '删除', rollback: '回滚前留档',
}[a] || a || '—')

const actionTagType = (a) => ({
  create: 'success', update: 'warning', delete: 'danger', rollback: 'info',
}[a] || 'info')

const formatVal = (val) => {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

const fieldLabel = (key) => {
  const labels = detailData.value?.field_labels || {}
  return labels[key] || String(key)
}
</script>
