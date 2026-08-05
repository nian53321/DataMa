<template>
  <div class="page-container">
    <div class="page-header">
      <span class="title">数据标注</span>
      <el-space wrap>
        <el-radio-group v-model="taskFilter" size="small" @change="onFilterChange">
          <el-radio-button value="">全部任务</el-radio-button>
          <el-radio-button value="mine">我的任务</el-radio-button>
          <el-radio-button value="pending">待预标注</el-radio-button>
          <el-radio-button value="pre_annotated">待标注</el-radio-button>
          <el-radio-button value="annotated">待复核</el-radio-button>
          <el-radio-button value="rejected">已驳回</el-radio-button>
        </el-radio-group>
        <el-select v-model="dataTypeFilter" placeholder="数据类型" clearable size="small" style="width: 120px" @change="onFilterChange">
          <el-option label="视频" value="video" />
          <el-option label="音频" value="audio" />
          <el-option label="脑电" value="eeg" />
          <el-option label="心电" value="ecg" />
          <el-option label="眼动" value="eye" />
          <el-option label="步态" value="gait" />
          <el-option label="量表" value="scale" />
          <el-option label="认知任务" value="task" />
        </el-select>
        <el-button v-if="canAnnotate || isAdmin" type="success" :icon="Document" @click="openMyTasks">我的标注工作台</el-button>
        <el-button v-if="canAnnotate" type="primary" :icon="Plus" @click="openCreateDialog">新建标注任务</el-button>
      </el-space>
    </div>

    <!-- 质量看板 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="8">
        <el-card shadow="hover">
          <el-statistic title="完成率" :value="quality.completion_rate * 100" suffix="%" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <el-statistic title="合格率" :value="quality.pass_rate * 100" suffix="%" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover">
          <el-statistic title="复核通过率" :value="quality.review_pass_rate * 100" suffix="%" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 可视化面板：任务状态分布 + 标注员贡献 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="12">
        <el-card>
          <template #header><span>任务状态分布</span></template>
          <div ref="statusPieRef" style="height: 260px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header><span>标注员任务负载</span></template>
          <div ref="annotatorBarRef" style="height: 260px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 批量操作模式选择 -->
    <div class="batch-bar">
      <el-radio-group v-model="batchMode" size="small" @change="onBatchModeChange">
        <el-radio-button value="">单条操作</el-radio-button>
        <el-radio-button value="assign">批量分配</el-radio-button>
        <el-radio-button value="annotate">批量标注</el-radio-button>
        <el-radio-button value="review">批量复核</el-radio-button>
        <el-radio-button value="delete">批量删除</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 任务列表 -->
    <el-card shadow="never" style="margin-top: 12px">
      <template v-if="batchMode" #header>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
          <span>标注任务列表</span>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <el-button type="primary" plain :icon="Check" :loading="selectAllLoading" @click="selectAllAcrossPages">
              跨页全选
            </el-button>
            <el-button :icon="CircleClose" :disabled="!selectedTasks.length" @click="clearTaskSelection">
              清空选择
            </el-button>
            <el-tag v-if="selectedTasks.length" type="warning" effect="plain">
              已选 {{ selectedTasks.length }} 个任务<span v-if="selectedTasks.length > displayTaskList.length">（含其他页）</span>
            </el-tag>
            <el-button v-if="batchMode === 'assign'" type="primary" :disabled="!selectedTasks.length" @click="openBatchAssignDialog">
              执行批量分配<span v-if="selectedTasks.length"> ({{ selectedTasks.length }})</span>
            </el-button>
            <el-button v-if="batchMode === 'annotate'" type="success" :disabled="!selectedTasks.length" @click="batchAnnotateMyTasks">
              执行批量标注<span v-if="selectedTasks.length"> ({{ selectedTasks.length }})</span>
            </el-button>
            <el-button v-if="batchMode === 'review'" type="warning" :disabled="!selectedTasks.length" @click="openBatchReviewDialog">
              执行批量复核<span v-if="selectedTasks.length"> ({{ selectedTasks.length }})</span>
            </el-button>
            <el-button v-if="batchMode === 'delete'" type="danger" plain :disabled="!selectedTasks.length" @click="batchDeleteTasks">
              执行批量删除<span v-if="selectedTasks.length"> ({{ selectedTasks.length }})</span>
            </el-button>
          </div>
        </div>
      </template>

    <el-table
      :data="displayTaskList"
      border
      stripe
      v-loading="tableLoading"
      @selection-change="onTaskSelectionChange"
      :row-key="(row) => row.id"
      ref="taskTableRef"
    >
      <el-table-column v-if="batchMode" type="selection" width="42" />
      <el-table-column prop="id" label="任务ID" width="70" align="center" header-align="center" />
      <el-table-column prop="data_asset_id" label="资产ID" width="70" align="center" header-align="center" />
      <el-table-column label="类型" width="80" align="center" header-align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="dataTypeTagType(row.data_type)">{{ dataTypeText(row.data_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="asset_file_name" label="文件名" min-width="120" show-overflow-tooltip header-align="center" />
      <el-table-column prop="status" label="状态" width="100" align="center" header-align="center">
        <template #default="{ row }">
          <el-tag :type="taskStatusType(row.status)">{{ taskStatusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="标注员" width="85" align="center" header-align="center" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.annotator_name">{{ row.annotator_name }}</span>
          <span v-else style="color: #909399">未分配</span>
        </template>
      </el-table-column>
      <el-table-column label="复核医生" width="90" align="center" header-align="center">
        <template #default="{ row }">
          <span v-if="row.reviewer_name">{{ row.reviewer_name }}</span>
          <span v-else style="color: #909399">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="100" show-overflow-tooltip header-align="center">
        <template #default="{ row }">
          <span>{{ row.remark || '—' }}</span>
          <el-tooltip v-if="row.status === 'rejected' && row.review_comment" :content="`驳回原因：${row.review_comment}`" placement="top">
            <el-tag size="small" type="danger" style="margin-left: 4px">驳回原因</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="145" show-overflow-tooltip header-align="center" />
      <el-table-column label="操作" width="330" fixed="right">
        <template #default="{ row }">
          <div class="task-actions">
            <el-button size="small" type="primary" @click="handleAssign(row.id)" v-if="['pending', 'pre_annotated', 'rejected'].includes(row.status)">
              {{ row.status === 'rejected' ? '重分配' : '分配' }}
            </el-button>
            <el-button size="small" type="primary" @click="handlePreAnnotate(row.id)" v-if="row.status === 'pending' && canAnnotate">
              预标注
            </el-button>
            <el-button size="small" type="success" @click="openWorkspace(row.id)" v-if="canAnnotateByStatus(row.status)">
              标注
            </el-button>
            <el-button size="small" type="warning" @click="openReview(row.id)" v-if="row.status === 'annotated' && canReview">
              复核
            </el-button>
            <el-button size="small" @click="openWorkspace(row.id)">详情</el-button>
            <el-dropdown trigger="click" @command="(cmd) => handleTaskAction(cmd, row)">
              <el-button size="small" link>更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="history">历史版本</el-dropdown-item>
                  <el-dropdown-item v-if="canEditTask" command="edit">编辑任务</el-dropdown-item>
                  <el-dropdown-item v-if="canDeleteTask" command="delete" divided>删除任务</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>
    </el-card>

    <!-- 新建任务弹窗：筛选 + 批量选择 -->
    <el-dialog v-model="createDialog" title="新建标注任务" width="780px">
      <!-- 筛选栏 -->
      <el-form :inline="true" style="margin-bottom: 12px">
        <el-form-item label="数据类型">
          <el-select v-model="assetFilter.data_type" placeholder="全部" clearable style="width: 130px" @change="filterAssets">
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
        <el-form-item label="受试者">
          <el-select v-model="assetFilter.subject_id" placeholder="全部" clearable filterable style="width: 160px" @change="filterAssets">
            <el-option
              v-for="s in subjectOptions"
              :key="s.id"
              :label="s.pseudo_id"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="assetFilter.keyword" placeholder="文件名" clearable style="width: 160px" @input="filterAssets" @clear="filterAssets" />
        </el-form-item>
      </el-form>

      <!-- 批量提示 -->
      <el-alert
        v-if="selectedAssets.length"
        type="success"
        :closable="false"
        :title="`已选 ${selectedAssets.length} 个数据资产，将创建为同一任务组（工作台可翻页依次标注）`"
        style="margin-bottom: 12px"
      />

      <!-- 任务备注 -->
      <el-form-item label="任务备注" style="margin-bottom: 12px">
        <el-input v-model="batchRemark" placeholder="可选，为这批任务添加备注" style="width: 100%" />
      </el-form-item>

      <!-- 分配标注员（可选，创建时直接分配） -->
      <el-form :inline="true" style="margin-bottom: 12px">
        <el-form-item label="分配标注员">
          <el-select v-model="batchAnnotator" clearable placeholder="创建后手动分配" filterable style="width: 200px">
            <el-option
              v-for="u in annotatorOptions"
              :key="u.id"
              :label="`${u.real_name || u.username}（${roleText(u.role)}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="复核医生">
          <el-select v-model="batchReviewer" clearable placeholder="可选，创建后由医生认领" filterable style="width: 200px">
            <el-option
              v-for="u in doctorOptions"
              :key="u.id"
              :label="`${u.real_name || u.username}`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <!-- 资产表格（多选） -->
      <el-table
        :data="filteredAssets"
        border
        stripe
        size="small"
        max-height="340"
        @selection-change="onSelectionChange"
        :row-key="(row) => row.id"
        ref="assetTableRef"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="data_type" label="类型" width="80">
          <template #default="{ row }">{{ dataTypeText(row.data_type) }}</template>
        </el-table-column>
        <el-table-column prop="subject_id" label="受试者" width="80" />
        <el-table-column prop="file_format" label="格式" width="70">
          <template #default="{ row }">{{ row.file_format || '—' }}</template>
        </el-table-column>
      </el-table>

      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button
          v-if="canAnnotate"
          type="primary"
          :loading="creating"
          :disabled="!selectedAssets.length"
          @click="handleBatchCreate"
        >
          批量创建（{{ selectedAssets.length }}）
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑任务弹窗 -->
    <el-dialog v-model="editDialog" title="编辑标注任务" width="480px">
      <el-alert
        type="info"
        :closable="false"
        title="状态变更请通过预标注/分配/标注/复核按钮操作，此处仅可调整分配与备注。"
        style="margin-bottom: 12px"
      />
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="任务ID">
          <el-input :model-value="editForm.id" disabled />
        </el-form-item>
        <el-form-item label="标注员">
          <el-select v-model="editForm.annotator_id" clearable placeholder="选择标注员" style="width: 100%">
            <el-option
              v-for="u in annotatorOptions"
              :key="u.id"
              :label="`${u.real_name || u.username}（${roleText(u.role)}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="复核医生">
          <el-select v-model="editForm.reviewer_id" clearable placeholder="选择复核医生（可留空，由医生主动复核时自动填充）" style="width: 100%">
            <el-option
              v-for="u in doctorOptions"
              :key="u.id"
              :label="`${u.real_name || u.username}`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="editForm.remark" type="textarea" :rows="3" placeholder="任务备注" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 分配任务弹窗 -->
    <el-dialog v-model="assignDialog" title="分配标注任务" width="480px">
      <el-form label-width="100px">
        <el-form-item label="任务ID">
          <el-input :model-value="currentTaskId" disabled />
        </el-form-item>
        <el-form-item label="标注员" required>
          <el-select v-model="assignForm.annotator_id" placeholder="选择标注员" style="width: 100%">
            <el-option
              v-for="u in annotatorOptions"
              :key="u.id"
              :label="`${u.real_name || u.username}（${roleText(u.role)}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleAssignSubmit">确认分配</el-button>
      </template>
    </el-dialog>

    <!-- 批量分配弹窗 -->
    <el-dialog v-model="batchAssignDialog" title="批量分配标注任务" width="480px">
      <el-alert
        type="info"
        :closable="false"
        :title="`将把 ${selectedTasks.length} 个任务分配给所选标注员，任务状态将变为「标注中」`"
        style="margin-bottom: 16px"
      />
      <el-form label-width="100px">
        <el-form-item label="标注员" required>
          <el-select v-model="batchAssignForm.annotator_id" placeholder="选择标注员" filterable style="width: 100%">
            <el-option
              v-for="u in annotatorOptions"
              :key="u.id"
              :label="`${u.real_name || u.username}（${roleText(u.role)}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchAssignDialog = false">取消</el-button>
        <el-button type="primary" :loading="batchAssignLoading" @click="handleBatchAssign">确认批量分配</el-button>
      </template>
    </el-dialog>

    <!-- 标注工作台 -->
    <el-dialog
      v-model="workspaceDialog"
      :title="`标注工作台 - 任务 #${currentTaskId}`"
      width="92%"
      top="2vh"
      :close-on-click-modal="false"
    >
      <div v-loading="wsLoading" class="workspace">
        <!-- 任务导航条 -->
        <div v-if="groupTasks.length > 1" class="group-nav">
          <el-tag size="small" :type="wsMode === 'my_tasks' ? 'success' : wsMode === 'batch' ? 'warning' : 'primary'" style="margin-right: 8px">
            {{ wsMode === 'my_tasks' ? '我的任务' : wsMode === 'batch' ? '批量标注' : '任务组' }}
          </el-tag>
          <el-button-group size="small">
            <el-button :icon="RefreshLeft" :disabled="groupIndex <= 0" @click="switchTask(-1)">上一个</el-button>
            <el-button disabled>{{ groupIndex + 1 }} / {{ groupTasks.length }}</el-button>
            <el-button :disabled="groupIndex >= groupTasks.length - 1" @click="switchTask(1)">下一个<el-icon class="el-icon--right"><RefreshLeft style="transform: scaleX(-1)" /></el-icon></el-button>
          </el-button-group>
          <el-tag v-if="groupTaskStatus" size="small" type="info" style="margin-left: 12px">
            当前任务状态：{{ taskStatusText(groupTaskStatus) }}
          </el-tag>
        </div>

        <!-- 顶部：数据信息 + 预标注结果 -->
        <el-descriptions :column="{ xs: 1, sm: 2, md: 4 }" border size="small" style="margin-bottom: 12px">
          <el-descriptions-item label="数据类型">{{ dataTypeText(assetInfo?.data_type) }}</el-descriptions-item>
          <el-descriptions-item label="文件名">{{ assetInfo?.file_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="采样率">{{ assetInfo?.sample_rate || '—' }} Hz</el-descriptions-item>
          <el-descriptions-item label="任务状态">
            <el-tag size="small" :type="taskStatusType(taskInfo?.status)">{{ taskStatusText(taskInfo?.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="预标注结果" :span="4">
            <span v-if="taskInfo?.pre_annotation?.labels?.length">
              <el-tag
                v-for="(lb, i) in taskInfo.pre_annotation.labels"
                :key="i"
                type="warning"
                size="small"
                style="margin-right: 6px"
              >{{ labelName(lb.label) }} (置信度 {{ (lb.confidence * 100).toFixed(0) }}%)</el-tag>
            </span>
            <span v-else style="color: #909399">暂无预标注</span>
          </el-descriptions-item>
        </el-descriptions>

        <el-row :gutter="12">
          <!-- 左：数据预览区 -->
          <el-col :span="16">
            <el-card shadow="never">
              <template #header>
                <div class="ws-card-title">
                  <span>数据预览</span>
                </div>
              </template>
              <!-- 视频预览 -->
              <div v-if="assetInfo?.data_type === 'video' && assetInfo?.file_path" class="preview-media">
                <video
                  :src="playUrl(assetInfo)"
                  controls
                  controlslist="nodownload"
                  style="max-width: 100%; max-height: 320px"
                  @error="onMediaError"
                />
              </div>
              <!-- 音频预览 -->
              <div v-else-if="assetInfo?.data_type === 'audio' && assetInfo?.file_path" class="preview-media">
                <audio :src="playUrl(assetInfo)" controls controlslist="nodownload" style="width: 100%" />
              </div>
              <!-- 脑电/心电预览（共用 ECharts 容器） -->
              <div v-else-if="assetInfo?.data_type === 'eeg' || assetInfo?.data_type === 'ecg'" class="preview-media">
                <div ref="wsEegRef" style="width: 100%; height: 320px"></div>
              </div>
              <!-- 其他类型：文件信息 -->
              <div v-else class="preview-box">
                <el-icon size="48"><component :is="dataTypeIcon" /></el-icon>
                <p>{{ dataTypeText(assetInfo?.data_type) }} 数据</p>
                <p class="sub">{{ assetInfo?.file_name || '—' }}</p>
                <p class="sub" v-if="assetInfo?.file_format">格式：{{ assetInfo.file_format }}</p>
              </div>
            </el-card>
          </el-col>

          <!-- 右：标注工具 -->
          <el-col :span="8">
            <el-card shadow="never">
              <template #header>添加标签</template>
              <el-form label-width="70px" size="small">
                <el-form-item label="标签">
                  <el-select v-model="labelForm.label" placeholder="选择预设标签" filterable style="width: 100%">
                    <el-option
                      v-for="p in presetLabels"
                      :key="p.value"
                      :label="p.label"
                      :value="p.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="备注">
                  <el-input v-model="labelForm.note" type="textarea" :rows="2" />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :icon="Plus" @click="addLabel">添加</el-button>
                </el-form-item>
              </el-form>
            </el-card>
          </el-col>
        </el-row>

        <!-- 已标注列表 -->
        <el-card shadow="never" style="margin-top: 12px">
          <template #header>
            <div class="ws-card-title">
              <span>已标注标签（{{ annotations.length }}）</span>
              <el-space>
                <el-button
                  v-if="canAnnotateByStatus(taskInfo?.status) && canAnnotate"
                  type="success"
                  :icon="CircleCheck"
                  :loading="submitting"
                  @click="handleSubmit"
                >提交标注</el-button>
                <el-button
                  v-if="canAnnotateByStatus(taskInfo?.status) && canAnnotate"
                  type="warning"
                  :icon="RefreshLeft"
                  @click="handleReAnnotate"
                >重标当前任务</el-button>
              </el-space>
            </div>
          </template>
          <el-table :data="annotations" border size="small" empty-text="暂无标注">
            <el-table-column label="标签" min-width="140">
              <template #default="{ row }">
                <el-tag size="small">{{ labelName(row.label) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="note" label="备注" min-width="160">
              <template #default="{ row }">{{ row.note || '—' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ $index }">
                <el-button size="small" type="primary" link @click="editLabel($index)">编辑</el-button>
                <el-button size="small" type="danger" link @click="annotations.splice($index, 1)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </el-dialog>

    <!-- 历史版本弹窗 -->
    <VersionHistoryDialog
      v-model:visible="historyVisible"
      :model-type="historyModel.modelType"
      :model-id="historyModel.modelId"
      :model-label="historyModel.modelLabel"
      @rollback-success="loadTasks"
    />

    <!-- 复核弹窗 -->
    <el-dialog v-model="reviewDialog" title="医生复核" width="800px" top="5vh">
      <div v-loading="wsLoading">
        <el-descriptions :column="2" border size="small" style="margin-bottom: 12px">
          <el-descriptions-item label="任务ID">#{{ currentTaskId }}</el-descriptions-item>
          <el-descriptions-item label="已标注数量">{{ annotations.length }} 个标签</el-descriptions-item>
        </el-descriptions>

        <!-- 标注列表（可编辑） -->
        <div style="margin-bottom: 12px">
          <div class="ws-card-title" style="margin-bottom: 8px">
            <span>标注内容（可修改）</span>
            <el-button size="small" type="primary" :icon="Plus" @click="addReviewLabel">添加标签</el-button>
          </div>
          <el-table :data="annotations" border size="small" empty-text="暂无标注">
            <el-table-column label="标签" min-width="160">
              <template #default="{ row }">
                <el-select v-model="row.label" size="small" filterable style="width: 100%">
                  <el-option v-for="p in presetLabels" :key="p.value" :label="p.label" :value="p.value" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="备注" min-width="200">
              <template #default="{ row }">
                <el-input v-model="row.note" size="small" placeholder="备注" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70">
              <template #default="{ $index }">
                <el-button size="small" type="danger" link @click="annotations.splice($index, 1)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <el-form label-width="90px">
          <el-form-item label="复核结果" required>
            <el-radio-group v-model="reviewForm.result">
              <el-radio value="approved">通过</el-radio>
              <el-radio value="rejected">驳回重标</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="复核意见">
            <el-input v-model="reviewForm.comment" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item label="电子签字" required>
            <el-input v-model="reviewForm.signature" placeholder="请输入签字确认" />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="reviewDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleReview">确认复核</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import {
  Plus, RefreshLeft, CircleCheck, Document, ArrowDown,
  VideoCamera, Microphone, DataLine, TrendCharts, Aim, ScaleToOriginal,
  Check, CircleClose,
} from '@element-plus/icons-vue'
import { getEegAssetApi, getEcgAssetApi } from '@/api/visualization'
import {
  getTasksApi, createTaskApi, batchCreateTasksApi, updateTaskApi, deleteTaskApi, getTaskDetailApi,
  preAnnotateApi, submitAnnotationApi, reviewTaskApi, getQualityDashboardApi,
  getLabelsApi, getAnnotatorsApi, assignTaskApi,
  getAvailableAssetsApi, getTaskGroupApi, getMyTasksApi,
  batchAssignApi, batchDeleteTasksApi,
} from '@/api/annotation'
import { getSubjectsApi } from '@/api/data'
import { useUserStore } from '@/stores/user'
import VersionHistoryDialog from '@/components/VersionHistoryDialog.vue'

const userStore = useUserStore()
const token = computed(() => userStore.token || '')

const canReview = computed(() => ['admin', 'doctor'].includes(userStore.role))
const canAnnotate = computed(() => ['admin', 'annotator'].includes(userStore.role))
const isAdmin = computed(() => userStore.role === 'admin')
const canEditTask = computed(() => ['admin', 'annotator', 'doctor'].includes(userStore.role))
const canDeleteTask = computed(() => userStore.role === 'admin')

// 历史版本弹窗
const historyVisible = ref(false)
const historyModel = reactive({ modelType: '', modelId: null, modelLabel: '' })
const openHistory = (row, modelType, label) => {
  historyModel.modelType = modelType
  historyModel.modelId = row.id
  historyModel.modelLabel = label
  historyVisible.value = true
}

const taskList = ref([])
const tableLoading = ref(false)
const taskTableRef = ref()
const selectedTasks = ref([])
const batchMode = ref('')
const batchAssignDialog = ref(false)
const batchAssignForm = reactive({ annotator_id: null })
const batchAssignLoading = ref(false)
const quality = ref({ completion_rate: 0, pass_rate: 0, review_pass_rate: 0 })

// 任务过滤：'' 全部 / mine 我的 / pending 待预标注 / annotated 待复核
const taskFilter = ref('')
const dataTypeFilter = ref('')
const onFilterChange = () => {
  loadTasks()
}

// 标注员/医生选项
const annotatorOptions = ref([])
const doctorOptions = ref([])
const roleText = (r) => ({
  admin: '管理员', doctor: '医生', annotator: '标注员',
  nurse: '护理人员', engineer: '数据工程师',
}[r] || r || '')

const loadAnnotators = async () => {
  try {
    const res = await getAnnotatorsApi()
    const all = res.data || []
    annotatorOptions.value = all.filter((u) => u.role === 'annotator' || u.role === 'admin')
    doctorOptions.value = all.filter((u) => u.role === 'doctor')
  } catch (e) { /* ignore */ }
}

// 可视化面板
const statusPieRef = ref()
const annotatorBarRef = ref()
let statusPieChart = null
let annotatorBarChart = null

// 新建任务（批量）
const createDialog = ref(false)
const creating = ref(false)
const assetOptions = ref([])
const subjectOptions = ref([])
const filteredAssets = ref([])
const selectedAssets = ref([])
const assetTableRef = ref()
const assetFilter = reactive({ data_type: '', subject_id: null, keyword: '' })
const batchRemark = ref('')
const batchAnnotator = ref(null)
const batchReviewer = ref(null)

const dataTypeText = (t) => ({
  video: '视频', audio: '音频', eeg: '脑电', ecg: '心电',
  eye: '眼动', gait: '步态', scale: '量表', task: '认知任务',
}[t] || t || '—')

const dataTypeTagType = (t) => ({
  video: '', audio: 'success', eeg: 'warning', ecg: 'danger',
  eye: 'info', gait: 'info', scale: '', task: 'success',
}[t] || 'info')

const filterAssets = () => {
  filteredAssets.value = assetOptions.value.filter((a) => {
    if (assetFilter.data_type && a.data_type !== assetFilter.data_type) return false
    if (assetFilter.subject_id && a.subject_id !== assetFilter.subject_id) return false
    if (assetFilter.keyword) {
      const kw = assetFilter.keyword.toLowerCase()
      if (!(a.file_name || '').toLowerCase().includes(kw)) return false
    }
    return true
  })
}

const onSelectionChange = (rows) => {
  selectedAssets.value = rows
}

// 编辑任务
const editDialog = ref(false)
const saving = ref(false)
const editForm = reactive({
  id: null, annotator_id: null, reviewer_id: null, remark: '',
})

// 工作台
const workspaceDialog = ref(false)
const wsLoading = ref(false)
const currentTaskId = ref(null)
const taskInfo = ref(null)
const assetInfo = ref(null)
const annotations = ref([])
const presetLabels = ref([])
const submitting = ref(false)
const labelForm = ref({ label: '', note: '' })
// 任务组导航
const groupTasks = ref([])
const groupIndex = ref(0)
const groupTaskStatus = ref('')
const wsMode = ref('group') // 'group' | 'my_tasks'
// 工作台 EEG 预览
const wsEegRef = ref()
let wsEegChart = null

const playUrl = (asset) => {
  if (!asset?.id) return ''
  // 使用 /play 端点（所有登录用户可访问，支持 ?access_token=xxx 查询参数认证）
  return `/api/data/assets/${asset.id}/play?access_token=${token.value}`
}

const onMediaError = () => {
  ElMessage.warning('媒体文件加载失败，可能格式不支持或文件损坏')
}

// 复核
const reviewDialog = ref(false)
const reviewForm = ref({ result: 'approved', comment: '', signature: '' })

const statusTextMap = {
  pending: '待预标注', pre_annotated: '已预标注', assigning: '分配中',
  annotating: '标注中', annotated: '已标注', reviewing: '复核中',
  rejected: '已驳回', approved: '已通过',
}
const statusTypeMap = {
  pending: 'info', pre_annotated: 'warning', annotating: 'warning',
  annotated: '', reviewing: 'warning', rejected: 'danger', approved: 'success',
}
const taskStatusText = (s) => statusTextMap[s] || s || '—'
const taskStatusType = (s) => statusTypeMap[s] || 'info'

const canAnnotateByStatus = (s) => ['pending', 'pre_annotated', 'annotating', 'rejected'].includes(s)

const iconMap = {
  video: VideoCamera, audio: Microphone, eeg: DataLine, ecg: TrendCharts,
  eye: Aim, gait: ScaleToOriginal, scale: ScaleToOriginal,
}
const dataTypeIcon = computed(() => iconMap[assetInfo.value?.data_type] || DataLine)

const labelName = (val) => {
  const found = presetLabels.value.find((p) => p.value === val)
  return found ? found.label : val
}

const loadTasks = async () => {
  tableLoading.value = true
  try {
    const params = { page: 1, page_size: 50 }
    if (taskFilter.value === 'mine') {
      params.mine = 1
    } else if (taskFilter.value) {
      params.status = taskFilter.value
    }
    if (dataTypeFilter.value) {
      params.data_type = dataTypeFilter.value
    }
    const res = await getTasksApi(params)
    taskList.value = res.data.items || []
    updateCharts()
    // 翻页后同步当前页勾选状态
    nextTick(() => _syncTaskTableSelection())
  } catch (e) { /* 接口未就绪 */ }
  finally { tableLoading.value = false }
}

const loadQuality = async () => {
  try {
    const res = await getQualityDashboardApi()
    quality.value = res.data
  } catch (e) { /* 接口未就绪 */ }
}

// 可视化面板渲染
const STATUS_LABELS = {
  pending: '待处理', pre_annotated: '已预标注', assigning: '分配中',
  annotating: '标注中', annotated: '已标注', reviewing: '复核中',
  rejected: '已驳回', approved: '已通过',
}
const STATUS_COLORS = {
  pending: '#909399', pre_annotated: '#e6a23c', assigning: '#9c27b0',
  annotating: '#409eff', annotated: '#67c23a', reviewing: '#00bcd4',
  rejected: '#f56c6c', approved: '#19be6b',
}

const updateCharts = () => {
  // 任务状态分布饼图
  if (statusPieChart) {
    const counts = {}
    taskList.value.forEach((t) => { counts[t.status] = (counts[t.status] || 0) + 1 })
    const data = Object.entries(counts).map(([k, v]) => ({
      name: STATUS_LABELS[k] || k, value: v, itemStyle: { color: STATUS_COLORS[k] || '#909399' },
    }))
    statusPieChart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        type: 'pie', radius: ['40%', '70%'], center: ['50%', '45%'],
        label: { formatter: '{b}\n{c}' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e9ecef' } }],
      }],
    }, true)
  }
  // 标注员任务负载柱图
  if (annotatorBarChart) {
    const counts = {}
    taskList.value.forEach((t) => {
      const k = t.annotator_id ? `用户#${t.annotator_id}` : '未分配'
      counts[k] = (counts[k] || 0) + 1
    })
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 10)
    annotatorBarChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 16, top: 24, bottom: 32 },
      xAxis: { type: 'category', data: entries.map((e) => e[0]), axisLabel: { interval: 0, rotate: entries.length > 5 ? 30 : 0 } },
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

const initCharts = () => {
  if (statusPieRef.value && !statusPieChart) statusPieChart = echarts.init(statusPieRef.value)
  if (annotatorBarRef.value && !annotatorBarChart) annotatorBarChart = echarts.init(annotatorBarRef.value)
}

const handleResize = () => {
  statusPieChart?.resize()
  annotatorBarChart?.resize()
}

const openCreateDialog = async () => {
  // 重置筛选与选择
  assetFilter.data_type = ''
  assetFilter.subject_id = null
  assetFilter.keyword = ''
  selectedAssets.value = []
  batchRemark.value = ''
  batchAnnotator.value = null
  batchReviewer.value = null
  // 确保人员列表已加载
  if (!annotatorOptions.value.length) loadAnnotators()
  try {
    const [assetRes, subRes] = await Promise.all([
      getAvailableAssetsApi({ page: 1, page_size: 500 }),
      getSubjectsApi({ page: 1, page_size: 200 }),
    ])
    assetOptions.value = assetRes.data.items || []
    subjectOptions.value = subRes.data.items || []
    filterAssets()
  } catch (e) { /* 接口未就绪 */ }
  createDialog.value = true
  // 清空表格选择
  nextTick(() => assetTableRef.value?.clearSelection())
}

const handleBatchCreate = async () => {
  if (!selectedAssets.value.length) {
    ElMessage.warning('请至少选择一个数据资产')
    return
  }
  creating.value = true
  try {
    const ids = selectedAssets.value.map((a) => a.id)
    const payload = { data_asset_ids: ids, remark: batchRemark.value }
    if (batchAnnotator.value) payload.annotator_id = batchAnnotator.value
    if (batchReviewer.value) payload.reviewer_id = batchReviewer.value
    const res = await batchCreateTasksApi(payload)
    ElMessage.success(res.message || `成功创建 ${ids.length} 个标注任务`)
    createDialog.value = false
    loadTasks()
  } finally {
    creating.value = false
  }
}

// 编辑任务
const openEditDialog = (row) => {
  // 进入编辑前确保人员列表已加载
  if (!annotatorOptions.value.length) loadAnnotators()
  Object.assign(editForm, {
    id: row.id,
    annotator_id: row.annotator_id || null,
    reviewer_id: row.reviewer_id || null,
    remark: row.remark || '',
  })
  editDialog.value = true
}

const handleSave = async () => {
  saving.value = true
  try {
    await updateTaskApi(editForm.id, {
      annotator_id: editForm.annotator_id || null,
      reviewer_id: editForm.reviewer_id || null,
      remark: editForm.remark,
    })
    ElMessage.success('任务已更新')
    editDialog.value = false
    loadTasks()
  } finally {
    saving.value = false
  }
}

// 分配任务
const assignDialog = ref(false)
const assignForm = reactive({ annotator_id: null })
const handleAssign = (taskId) => {
  currentTaskId.value = taskId
  if (!annotatorOptions.value.length) loadAnnotators()
  // 默认预选第一个标注员
  assignForm.annotator_id = annotatorOptions.value[0]?.id || null
  assignDialog.value = true
}
const handleAssignSubmit = async () => {
  if (!assignForm.annotator_id) {
    ElMessage.warning('请选择标注员')
    return
  }
  saving.value = true
  try {
    await assignTaskApi(currentTaskId.value, { annotator_id: assignForm.annotator_id })
    ElMessage.success('已分配，开始标注')
    assignDialog.value = false
    loadTasks()
  } finally {
    saving.value = false
  }
}

const removeTask = (row) => {
  ElMessageBox.confirm(
    `确认删除标注任务 #${row.id}？该操作会级联删除其所有标注结果与版本记录，不可恢复。`,
    '危险操作',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      await deleteTaskApi(row.id)
      ElMessage.success('任务已删除')
      loadTasks()
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}

const handleTaskAction = (cmd, row) => {
  if (cmd === 'history') {
    openHistory(row, 'annotation_task', `任务 #${row.id}`)
  } else if (cmd === 'edit') {
    openEditDialog(row)
  } else if (cmd === 'delete') {
    removeTask(row)
  }
}

// ==================== 批量操作 ====================
const ASSIGNABLE = ['pending', 'pre_annotated', 'rejected', 'annotating']
const ANNOTATABLE = ['pre_annotated', 'annotating', 'rejected']
const REVIEWABLE = ['annotated']

const isTaskSelectable = () => true  // 过滤后展示的都是可选的

// 批量模式下按状态过滤显示的任务列表
const displayTaskList = computed(() => {
  if (!batchMode.value) return taskList.value
  if (batchMode.value === 'assign') {
    return taskList.value.filter((r) => ASSIGNABLE.includes(r.status))
  }
  if (batchMode.value === 'annotate') {
    return taskList.value.filter((r) => ANNOTATABLE.includes(r.status))
  }
  if (batchMode.value === 'review') {
    return taskList.value.filter((r) => REVIEWABLE.includes(r.status))
  }
  // delete 模式显示全部
  return taskList.value
})

const onBatchModeChange = () => {
  clearTaskSelection()
}

// 跨页选中：selectedTasks 作为单一数据源，ID 集合驱动
let _suppressTaskSelectionChange = false

const onTaskSelectionChange = (rows) => {
  if (_suppressTaskSelectionChange) return
  const currentPageIds = new Set(displayTaskList.value.map((r) => r.id))
  const kept = selectedTasks.value.filter((r) => !currentPageIds.has(r.id))
  const pageRows = rows.map((r) => ({ ...r, _placeholder: false }))
  const seen = new Set(kept.map((r) => r.id))
  const merged = [...kept]
  for (const r of pageRows) {
    if (!seen.has(r.id)) {
      seen.add(r.id)
      merged.push(r)
    }
  }
  selectedTasks.value = merged
}

const _syncTaskTableSelection = () => {
  if (!taskTableRef.value) return
  _suppressTaskSelectionChange = true
  try {
    const selectedIds = new Set(selectedTasks.value.map((r) => r.id))
    displayTaskList.value.forEach((row) => {
      taskTableRef.value.toggleRowSelection(row, selectedIds.has(row.id))
    })
  } finally {
    _suppressTaskSelectionChange = false
  }
}

// 跨页全选按钮：取当前筛选条件下所有页的任务 ID，全部加入 selectedTasks
const selectAllLoading = ref(false)
const selectAllAcrossPages = async () => {
  selectAllLoading.value = true
  try {
    const params = { page: 1, page_size: 1, ids_only: true }
    if (taskFilter.value === 'mine') {
      params.mine = 1
    } else if (taskFilter.value) {
      params.status = taskFilter.value
    }
    if (dataTypeFilter.value) {
      params.data_type = dataTypeFilter.value
    }
    const res = await getTasksApi(params)
    const allIds = (res.data?.items || []).map((x) => x.id)
    if (!allIds.length) {
      ElMessage.warning('当前筛选条件下无可选任务')
      return
    }
    const allIdSet = new Set(allIds)
    const existingNotInFilter = selectedTasks.value.filter((r) => !allIdSet.has(r.id))
    const newSelected = allIds.map((id) => {
      const existing = selectedTasks.value.find((r) => r.id === id)
      return existing || { id, _placeholder: true }
    })
    selectedTasks.value = [...existingNotInFilter, ...newSelected]
    ElMessage.success(`已跨页全选 ${allIds.length} 个任务`)
    nextTick(() => _syncTaskTableSelection())
  } catch (e) {
    // 接口失败时静默
  } finally {
    selectAllLoading.value = false
  }
}
const clearTaskSelection = () => {
  selectedTasks.value = []
  taskTableRef.value?.clearSelection()
}
const openBatchAssignDialog = () => {
  if (!selectedTasks.value.length) return
  batchAssignForm.annotator_id = null
  if (!annotatorOptions.value.length) loadAnnotators()
  batchAssignDialog.value = true
}
const handleBatchAssign = async () => {
  if (!batchAssignForm.annotator_id) {
    ElMessage.warning('请选择标注员')
    return
  }
  batchAssignLoading.value = true
  try {
    const ids = selectedTasks.value.map((t) => t.id)
    const res = await batchAssignApi({ task_ids: ids, annotator_id: batchAssignForm.annotator_id })
    ElMessage.success(res.message || '批量分配成功')
    batchAssignDialog.value = false
    clearTaskSelection()
    loadTasks()
  } catch (e) { /* 拦截器已提示 */ }
  finally { batchAssignLoading.value = false }
}
const batchDeleteTasks = () => {
  if (!selectedTasks.value.length) return
  ElMessageBox.confirm(
    `确认批量删除 ${selectedTasks.value.length} 个标注任务？此操作不可恢复。`,
    '危险操作',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      const ids = selectedTasks.value.map((t) => t.id)
      const res = await batchDeleteTasksApi({ task_ids: ids })
      ElMessage.success(res.message || '批量删除成功')
      clearTaskSelection()
      loadTasks()
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}
const batchAnnotateMyTasks = async () => {
  // 批量标注：以"我的任务"模式打开工作台，从选中的第一个任务开始
  const tasks = selectedTasks.value
  if (!tasks.length) return
  // 将选中任务构造为导航列表
  groupTasks.value = tasks.map((t) => ({ id: t.id, data_asset_id: t.data_asset_id, status: t.status, remark: t.remark }))
  groupIndex.value = 0
  wsMode.value = 'batch'
  openWorkspace(tasks[0].id, 'batch')
}
const openBatchReviewDialog = () => {
  // 批量复核：筛选出已标注状态的任务，以第一个打开复核
  const reviewable = selectedTasks.value.filter((t) => t.status === 'annotated')
  if (!reviewable.length) {
    ElMessage.info('选中的任务中没有待复核的任务（状态需为"已标注"）')
    return
  }
  openReview(reviewable[0].id)
}

const handlePreAnnotate = async (id) => {
  await preAnnotateApi(id)
  ElMessage.success('预标注完成')
  loadTasks()
}

const openWorkspace = async (id, mode = 'group') => {
  currentTaskId.value = id
  workspaceDialog.value = true
  wsLoading.value = true
  groupTasks.value = []
  groupIndex.value = 0
  groupTaskStatus.value = ''
  wsMode.value = mode
  try {
    const res = await getTaskDetailApi(id)
    taskInfo.value = res.data.task
    assetInfo.value = res.data.asset
    annotations.value = res.data.annotations.map((a) => ({ ...a }))
    let labels = res.data.preset_labels || []
    if (!labels.length) {
      try {
        const all = await getLabelsApi()
        const grouped = all.data || {}
        labels = Object.values(grouped).flat()
      } catch (e) { /* ignore */ }
    }
    presetLabels.value = labels

    // 加载导航任务列表
    if (mode === 'batch') {
      // 批量模式：groupTasks 已在调用前设置，只需定位索引
      groupIndex.value = groupTasks.value.findIndex((t) => t.id === id)
      groupTaskStatus.value = taskInfo.value?.status || ''
    } else if (mode === 'my_tasks') {
      // 我的任务模式：加载当前用户所有标注任务
      try {
        const mres = await getMyTasksApi({})
        groupTasks.value = mres.data.tasks || []
        groupIndex.value = groupTasks.value.findIndex((t) => t.id === id)
        groupTaskStatus.value = taskInfo.value?.status || ''
      } catch (e) { /* ignore */ }
    } else {
      // 分组模式：加载同组任务
      const gid = taskInfo.value?.group_id
      if (gid) {
        try {
          const gres = await getTaskGroupApi(gid)
          groupTasks.value = gres.data.tasks || []
          groupIndex.value = groupTasks.value.findIndex((t) => t.id === id)
          groupTaskStatus.value = taskInfo.value?.status || ''
        } catch (e) { /* ignore */ }
      }
    }

    // EEG/ECG 数据预览（延迟等待对话框动画完成）
    const dtype = assetInfo.value?.data_type
    if ((dtype === 'eeg' || dtype === 'ecg') && assetInfo.value?.id) {
      setTimeout(() => loadWsEeg(assetInfo.value.id, dtype), 300)
    }
  } catch (e) {
    ElMessage.error('任务详情加载失败')
  } finally {
    wsLoading.value = false
  }
}

const openMyTasks = async () => {
  // 打开"我的任务"工作台：加载当前用户所有标注任务，从第一个开始
  try {
    const res = await getMyTasksApi({})
    const tasks = res.data?.tasks || []
    if (!tasks.length) {
      ElMessage.info('您当前没有标注任务')
      return
    }
    openWorkspace(tasks[0].id, 'my_tasks')
  } catch (e) {
    ElMessage.error('加载任务列表失败')
  }
}

const loadWsEeg = async (assetId, dataType = 'eeg') => {
  if (!wsEegRef.value) return
  // 容器可能因对话框动画未完成而尺寸为 0，等待后重试
  if (wsEegRef.value.offsetWidth === 0 || wsEegRef.value.offsetHeight === 0) {
    setTimeout(() => loadWsEeg(assetId, dataType), 200)
    return
  }
  if (!wsEegChart) wsEegChart = echarts.init(wsEegRef.value)
  wsEegChart.resize()
  wsEegChart.showLoading()
  try {
    // 根据数据类型选择对应 API（EEG 返回多通道，ECG 返回单通道）
    const res = dataType === 'ecg'
      ? await getEcgAssetApi(assetId)
      : await getEegAssetApi(assetId)
    if (res.code === 200 && res.data) {
      // 统一转换为 channels 数组格式
      let channels = []
      let deviceLabel = ''
      const meta = res.data.meta || {}
      if (res.data.channels && res.data.channels.length) {
        // EEG 多通道格式
        channels = res.data.channels
        deviceLabel = meta.device || ''
      } else if (Array.isArray(res.data.data) && res.data.data.length) {
        // ECG 单通道格式：[{time, value}, ...] 或 [[time, value], ...]
        const rawData = res.data.data
        const values = rawData.map(d => Array.isArray(d) ? d[1] : (typeof d === 'object' ? d.value : d))
        channels = [{ name: 'ECG', data: values }]
        deviceLabel = meta.device || 'CSV'
      }
      if (!channels.length) {
        wsEegChart.hideLoading()
        wsEegChart.setOption({ title: { text: '无有效数据', left: 'center', top: 'center', textStyle: { color: '#909399', fontSize: 14 } } }, true)
        return
      }
      const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272',
        '#fc8452', '#9a60fd', '#ea7ccc', '#5d7092', '#c9db70', '#00c2ff',
        '#ff7d00', '#8d98b3', '#e0c3fc', '#fbbf00']
      let gMin = Infinity, gMax = -Infinity
      channels.forEach(ch => { for (const v of ch.data) { if (v < gMin) gMin = v; if (v > gMax) gMax = v } })
      const xData = Array.from({ length: channels[0]?.data.length || 0 }, (_, i) => i)
      const series = channels.map((ch, idx) => ({
        name: ch.name.replace('EXG Channel ', 'CH'),
        type: 'line', showSymbol: false, data: ch.data,
        lineStyle: { width: 1, color: colors[idx % colors.length] },
        itemStyle: { color: colors[idx % colors.length] },
      }))
      // 保存通道数据用于 legend 切换时重新计算 Y 轴范围
      const wsChannelsData = channels.map(ch => ({
        name: ch.name.replace('EXG Channel ', 'CH'),
        data: ch.data,
      }))
      const yName = dataType === 'ecg' ? 'mV' : 'μV'
      wsEegChart.hideLoading()
      wsEegChart.setOption({
        title: { text: '', left: 'center', top: 0, textStyle: { fontSize: 12, color: '#606266' } },
        tooltip: { trigger: 'axis', axisPointer: { type: 'line' } },
        legend: { top: dataType === 'ecg' ? 5 : 0, textStyle: { fontSize: 9 }, data: series.map(s => s.name) },
        grid: { left: 80, right: 16, top: 50, bottom: 28 },
        xAxis: { type: 'category', data: xData },
        yAxis: { type: 'value', min: gMin, max: gMax, name: yName, nameLocation: 'end', nameGap: 10, nameTextStyle: { fontSize: 11 } },
        dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 4 }],
        series,
      }, true)
      // legend 切换时重新计算可见通道的 Y 轴范围
      wsEegChart.off('legendselectchanged')
      wsEegChart.on('legendselectchanged', (params) => {
        const selected = params.selected || {}
        let visMin = Infinity, visMax = -Infinity
        let hasVisible = false
        wsChannelsData.forEach(ch => {
          if (selected[ch.name] !== false) {
            hasVisible = true
            for (const v of ch.data) {
              if (v < visMin) visMin = v
              if (v > visMax) visMax = v
            }
          }
        })
        if (hasVisible) {
          wsEegChart.setOption({ yAxis: [{ min: visMin, max: visMax }] })
        }
      })
    } else {
      wsEegChart.hideLoading()
      wsEegChart.setOption({ title: { text: res.message || '解析失败', left: 'center', top: 'center', textStyle: { color: '#909399', fontSize: 14 } } }, true)
    }
  } catch (e) {
    wsEegChart?.hideLoading()
    const errText = dataType === 'ecg' ? '心电数据加载失败' : '脑电数据加载失败'
    wsEegChart?.setOption({ title: { text: errText, left: 'center', top: 'center', textStyle: { color: '#909399', fontSize: 14 } } }, true)
  }
}

// 任务组翻页
const switchTask = async (dir) => {
  const newIdx = groupIndex.value + dir
  if (newIdx < 0 || newIdx >= groupTasks.value.length) return
  // 保存当前标注到内存（不提交）
  groupTasks.value[groupIndex.value]._savedAnnotations = JSON.parse(JSON.stringify(annotations.value))
  groupIndex.value = newIdx
  const targetId = groupTasks.value[newIdx].id
  currentTaskId.value = targetId
  wsLoading.value = true
  try {
    const res = await getTaskDetailApi(targetId)
    taskInfo.value = res.data.task
    assetInfo.value = res.data.asset
    // 恢复已保存的标注（如果有）
    const saved = groupTasks.value[newIdx]._savedAnnotations
    if (saved) {
      annotations.value = JSON.parse(JSON.stringify(saved))
    } else {
      annotations.value = res.data.annotations.map((a) => ({ ...a }))
    }
    groupTaskStatus.value = taskInfo.value?.status || ''
    // 预设标签可能不同（不同模态）
    let labels = res.data.preset_labels || []
    if (!labels.length) {
      const all = await getLabelsApi()
      const grouped = all.data || {}
      labels = Object.values(grouped).flat()
    }
    presetLabels.value = labels
    // EEG/ECG 预览
    const dtype2 = assetInfo.value?.data_type
    if ((dtype2 === 'eeg' || dtype2 === 'ecg') && assetInfo.value?.id) {
      setTimeout(() => loadWsEeg(assetInfo.value.id, dtype2), 100)
    } else if (wsEegChart) {
      wsEegChart.setOption({ series: [] }, true)
    }
  } catch (e) {
    ElMessage.error('切换任务失败，请重试')
  } finally {
    wsLoading.value = false
  }
}

// 重标当前任务
const handleReAnnotate = () => {
  ElMessageBox.confirm(
    '重标将清空当前标注内容，重新开始标注。确认继续？',
    '重标确认',
    { type: 'warning' }
  ).then(() => {
    annotations.value = []
    labelForm.value = { label: '', note: '' }
    ElMessage.success('已清空标注，请重新标注')
  }).catch(() => {})
}

// 编辑已有标签
const editLabel = (idx) => {
  const a = annotations.value[idx]
  labelForm.value = { ...a }
  annotations.value.splice(idx, 1)
}

// 复核时添加标签
const addReviewLabel = () => {
  annotations.value.push({ label: presetLabels.value[0]?.value || '', note: '' })
}

const addLabel = () => {
  if (!labelForm.value.label) {
    ElMessage.warning('请选择标签')
    return
  }
  annotations.value.push({ ...labelForm.value })
  labelForm.value = { label: '', note: '' }
  ElMessage.success('已添加标签')
}

const handleSubmit = async () => {
  // 若已选标签但未点添加，自动并入提交
  if (!annotations.value.length && labelForm.value.label) {
    annotations.value.push({ ...labelForm.value })
    labelForm.value = { label: '', note: '' }
  }
  if (!annotations.value.length) {
    ElMessage.warning('请至少添加一个标签')
    return
  }
  submitting.value = true
  try {
    await submitAnnotationApi(currentTaskId.value, { annotations: annotations.value })
    ElMessage.success('标注已提交')
    workspaceDialog.value = false
    loadTasks()
  } finally {
    submitting.value = false
  }
}

const openReview = async (id) => {
  currentTaskId.value = id
  reviewDialog.value = true
  wsLoading.value = true
  reviewForm.value = { result: 'approved', comment: '', signature: '' }
  try {
    const res = await getTaskDetailApi(id)
    taskInfo.value = res.data.task
    assetInfo.value = res.data.asset
    annotations.value = res.data.annotations.map((a) => ({ ...a }))
    let labels = res.data.preset_labels || []
    if (!labels.length) {
      try {
        const all = await getLabelsApi()
        const grouped = all.data || {}
        labels = Object.values(grouped).flat()
      } catch (e) { /* ignore */ }
    }
    presetLabels.value = labels
  } catch (e) {
    ElMessage.error('任务详情加载失败')
  } finally {
    wsLoading.value = false
  }
}

const handleReview = async () => {
  if (!reviewForm.value.signature) {
    ElMessage.warning('请填写电子签字')
    return
  }
  submitting.value = true
  try {
    // 提交复核时附带修改后的标注
    await reviewTaskApi(currentTaskId.value, {
      ...reviewForm.value,
      annotations: annotations.value.map((a) => ({
        label: a.label,
        label_type: a.label_type,
        confidence: a.confidence,
        note: a.note,
      })),
    })
    ElMessage.success('复核完成')
    reviewDialog.value = false
    loadTasks()
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await loadTasks()
  await nextTick()
  initCharts()
  updateCharts()
  loadQuality()
  loadAnnotators()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  statusPieChart?.dispose()
  statusPieChart = null
  annotatorBarChart?.dispose()
  annotatorBarChart = null
  wsEegChart?.dispose()
  wsEegChart = null
})
</script>

<style scoped lang="scss">
.workspace {
  min-height: 400px;
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
.task-actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
/* 防止状态标签文字被截断 */
.el-table .el-tag {
  white-space: nowrap;
}
.group-nav {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f0f2f5;
  border-radius: 4px;
}
.preview-media {
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 12px;
}
.ws-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.preview-box {
  height: 180px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: #f5f7fa;
  border-radius: 4px;
  color: #909399;
  p { margin: 0; font-size: 14px; }
  .sub { font-size: 12px; }
}
</style>

<style lang="scss">
/* 禁用行样式（非 scoped 以覆盖 el-table 内部样式） */
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
  .el-checkbox__input.is-disabled.is-checked .el-checkbox__inner {
    background-color: #a0cfff;
    border-color: #a0cfff;
  }
  td {
    color: #909399;
  }
}
</style>
