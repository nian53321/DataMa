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
        <el-radio-button v-if="canClean" value="desensitize">批量脱敏</el-radio-button>
      </el-radio-group>
      <span v-if="batchMode === 'desensitize'" class="batch-bar-tip">
        把数据提前加工成脱敏形态并落盘留档；导出时勾选「优先复用已存脱敏文件」即可直接取用
      </span>
    </div>

    <!-- 后台脱敏任务条：任务"挂到后台"（关弹窗 / 切页 / 刷新 / 重开浏览器）后仍常驻显示。
         进度以服务端任务状态为准（存在 Redis），所以本地关掉界面不会丢进度。 -->
    <div
      v-if="desensProgress"
      class="desens-taskbar"
      :class="`is-${desensProgress.status || 'running'}`"
    >
      <el-icon class="desens-taskbar-icon" :class="{ spinning: desensRunning }">
        <Loading v-if="desensRunning" />
        <CircleCheck v-else-if="desensProgress.status === 'success'" />
        <CircleClose v-else />
      </el-icon>
      <span class="desens-taskbar-title">
        批量脱敏{{ desensRunning ? '进行中' : (desensProgress.status === 'failed' ? '失败' : '完成') }}
      </span>
      <el-progress
        class="desens-taskbar-bar"
        :percentage="desensProgress.percent || 0"
        :status="desensProgress.status === 'failed'
          ? 'exception'
          : (desensProgress.status === 'success' ? 'success' : undefined)"
        :stroke-width="8"
        :show-text="false"
      />
      <span class="desens-taskbar-text" :title="desensProgress.status_text">
        {{ desensProgress.status_text }}
      </span>
      <el-tag v-if="desensProgress.stale" size="small" type="warning" effect="dark">
        进度可能已中断
      </el-tag>
      <div class="desens-taskbar-actions">
        <el-button size="small" text type="primary" @click="viewDesensProgress">详情</el-button>
        <el-button v-if="!desensRunning" size="small" text @click="dismissDesensBar">收起</el-button>
      </div>
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
      <!-- 数据接入：以受试者为单位（列表放受试者，展开查看该受试者文件，可单独勾选子文件） -->
      <el-tab-pane label="数据接入" name="access">
        <!-- 筛选栏（筛选条件作用于文件：只展示含匹配文件的受试者） -->
        <el-form :inline="true" style="margin-bottom: 12px">
          <el-form-item label="模态类型">
            <el-select v-model="filter.data_type" placeholder="全部" clearable style="width: 130px" @change="onFilterChange">
              <el-option label="视频" value="video" />
              <el-option label="音频" value="audio" />
              <el-option label="脑电" value="eeg" />
              <el-option label="心电" value="ecg" />
              <el-option label="眼动" value="eye" />
              <el-option label="步态" value="gait" />
              <el-option label="量表" value="scale" />
              <el-option label="认知任务" value="task" />
              <!-- userInfo 资产在库里是 json：平时被清洗流程忽略，批量脱敏要用到 -->
              <el-option label="受试者信息" value="json" />
            </el-select>
          </el-form-item>
          <el-form-item label="分层">
            <el-select v-model="filter.layer" placeholder="全部" clearable style="width: 100px" @change="onFilterChange">
              <el-option label="原始" value="raw" />
              <el-option label="清洗" value="cleaned" />
              <el-option label="特征" value="feature" />
              <el-option label="标注" value="annotation" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="filter.status" placeholder="全部" clearable style="width: 120px" @change="onFilterChange">
              <el-option label="已上传" value="uploaded" />
              <el-option label="清洗中" value="cleaning" />
              <el-option label="已清洗" value="cleaned" />
              <el-option label="已标准化" value="standardized" />
            </el-select>
          </el-form-item>
          <el-form-item label="关键词">
            <el-input v-model="filter.keyword" placeholder="文件名" clearable style="width: 160px" @keyup.enter="onFilterChange" @clear="onFilterChange" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="loadData">查询</el-button>
            <el-button :icon="RefreshLeft" @click="onResetFilter">重置</el-button>
          </el-form-item>
        </el-form>

        <!-- 受试者分组表格 -->
        <el-card shadow="never" style="margin-top: 12px">
          <template #header>
            <div class="table-head">
              <span class="table-head-title">
                受试者列表：共 {{ groupList.length }} 位 / {{ filteredAssets.length }} 个文件
                <el-tag v-if="batchModeHint" size="small" type="info" style="margin-left: 6px">
                  {{ batchModeHint }}
                </el-tag>
              </span>
              <div v-if="batchMode || selectedAssets.length" class="table-head-actions">
                <el-button :icon="Select" :disabled="!groupList.length" @click="selectAllInFilter">
                  全选当前筛选结果
                </el-button>
                <el-button :icon="CircleClose" :disabled="!selectedAssets.length" @click="clearSelection">
                  清空选择
                </el-button>
                <el-tag v-if="selectedAssets.length" type="warning" effect="plain">
                  已选 {{ selectedAssets.length }} 个文件（{{ selectedSubjectCount }} 位受试者）
                </el-tag>
                <el-button v-if="batchMode === 'clean'" type="primary" :icon="MagicStick" :disabled="!selectedAssets.length" @click="openCleanDialog">
                  执行清洗<span v-if="selectedAssets.length"> ({{ selectedAssets.length }})</span>
                </el-button>
                <el-button v-if="batchMode === 'standard'" type="success" :icon="Check" :disabled="!selectedAssets.length" @click="openStdDialog">
                  执行标准化<span v-if="selectedAssets.length"> ({{ selectedAssets.length }})</span>
                </el-button>
                <el-button
                  v-if="batchMode === 'desensitize'"
                  type="warning"
                  :icon="Lock"
                  :disabled="!selectedAssets.length || desensRunning"
                  @click="openDesensDialog"
                >
                  {{ desensRunning ? '脱敏任务进行中' : '执行批量脱敏' }}<span
                    v-if="!desensRunning && selectedAssets.length"
                  > ({{ selectedAssets.length }})</span>
                </el-button>
                <span v-if="!batchMode" class="table-head-tip">已勾选文件，请切换到「批量清洗 / 批量标准化 / 批量脱敏」后执行</span>
              </div>
              <span v-else class="table-head-tip">行首复选框可不展开直接整选受试者文件；展开受试者可单独勾选子文件</span>
            </div>
          </template>

          <el-table
            :data="pagedGroups"
            border
            stripe
            v-loading="loading"
            empty-text="暂无匹配的数据资产，请前往数据管理页接入或调整筛选条件"
            :row-key="(row) => row.subject_id"
          >
            <!-- 受试者级复选框：不展开即可整选/取消该受试者名下的全部文件 -->
            <el-table-column width="46" align="center">
              <template #default="{ row }">
                <el-checkbox
                  :model-value="groupAllSelected(row)"
                  :indeterminate="groupPartialSelected(row)"
                  @change="(v) => toggleGroupAll(row, v)"
                />
              </template>
            </el-table-column>
            <el-table-column label="受试者" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="subject-pseudo">{{ row.pseudo_id }}</span>
                <span v-if="row.real_name" class="subject-name">（{{ privacy.mask('real_name', row.real_name) }}）</span>
              </template>
            </el-table-column>
            <!-- 展开行（置于受试者 ID 之后）：该受试者下的文件，可单独勾选子文件 -->
            <el-table-column type="expand" width="46">
              <template #default="{ row }">
                <div class="group-panel">
                  <div class="group-panel-head">
                    <span>该受试者共 {{ row.assets.length }} 个文件</span>
                    <el-button link type="primary" size="small" @click="selectAllInGroup(row)">全选该受试者文件</el-button>
                    <el-button link size="small" @click="clearGroupSelection(row)">清空该受试者</el-button>
                  </div>
                  <el-table :data="row.assets" size="small" border :row-key="(a) => a.id">
                    <el-table-column width="46" align="center">
                      <template #default="{ row: a }">
                        <el-checkbox :model-value="selectedIdSet.has(a.id)" @change="(v) => toggleAsset(a, v)" />
                      </template>
                    </el-table-column>
                    <el-table-column prop="file_name" label="文件名" min-width="240" show-overflow-tooltip />
                    <el-table-column prop="data_type" label="模态类型" width="100" align="center">
                      <template #default="{ row: a }">
                        <el-tag size="small" :type="dataTypeTagType(a.data_type)">{{ dataTypeMap[a.data_type] || a.data_type }}</el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column prop="layer" label="分层" width="80" align="center">
                      <template #default="{ row: a }">{{ layerMap[a.layer] || a.layer }}</template>
                    </el-table-column>
                    <el-table-column prop="status" label="状态" width="100" align="center">
                      <template #default="{ row: a }">
                        <el-tag size="small" :type="statusType(a.status)">{{ statusMap[a.status] || a.status }}</el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="质量" width="80" align="center">
                      <template #default="{ row: a }">
                        <el-tag :type="a.is_qualified ? 'success' : 'danger'" size="small">
                          {{ a.is_qualified ? '合格' : '不合格' }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <!-- 已有脱敏产物标记：产物挂在原资产下（不新增行），只在这里体现 -->
                    <el-table-column label="脱敏" width="96" align="center">
                      <template #default="{ row: a }">
                        <el-tag
                          v-if="existingArtifactsOf(a).length"
                          size="small"
                          type="success"
                          effect="plain"
                          :title="'已有脱敏文件：' + existingArtifactsOf(a).map((x) => x.modality_label).join('、')"
                        >
                          已脱敏<span v-if="existingArtifactsOf(a).length > 1"> ×{{ existingArtifactsOf(a).length }}</span>
                        </el-tag>
                        <span v-else class="desens-none">—</span>
                      </template>
                    </el-table-column>
                    <el-table-column prop="file_format" label="格式" width="80" align="center">
                      <template #default="{ row: a }">{{ a.file_format || '—' }}</template>
                    </el-table-column>
                    <el-table-column label="大小" width="100" align="right">
                      <template #default="{ row: a }">{{ formatFileSize(a.file_size) }}</template>
                    </el-table-column>
                    <el-table-column prop="created_at" label="创建时间" width="170" header-align="center" />
                  </el-table>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="文件数" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" type="info">{{ row.assets.length }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="模态分布" min-width="240">
              <template #default="{ row }">
                <el-tag
                  v-for="(cnt, t) in groupTypeCounts(row)"
                  :key="t"
                  size="small"
                  :type="dataTypeTagType(t)"
                  style="margin: 2px 4px 2px 0"
                >
                  {{ dataTypeMap[t] || t }} × {{ cnt }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="已选" width="70" align="center">
              <template #default="{ row }">
                <span :class="['sel-count', { 'sel-count-on': groupSelectedCount(row) > 0 }]">{{ groupSelectedCount(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="collection_batch" label="采集批次" width="110" align="center">
              <template #default="{ row }">{{ row.collection_batch || '—' }}</template>
            </el-table-column>
            <!-- 采集时间：2026-10-06 用户决定「统一用创建时间」—— 不再区分 userInfo 里的
                 真实采集时间。故本列直接展示受试者 created_at（后端 collection_time 原值保留不动）。
                 表头加 tooltip，避免列名与实际口径不一致造成误解。 -->
            <el-table-column width="170" header-align="center">
              <template #header>
                <span title="统一按受试者创建时间显示">采集时间</span>
              </template>
              <template #default="{ row }">{{ row.collection_time || '—' }}</template>
            </el-table-column>
          </el-table>

          <!-- 分页（按受试者分页） -->
          <el-pagination
            style="margin-top: 12px; justify-content: flex-end"
            v-model:current-page="pagination.page"
            v-model:page-size="pagination.page_size"
            :total="pagination.total"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next"
            @size-change="onPageSizeChange"
          />
        </el-card>
      </el-tab-pane>

      <!-- 清洗配置 -->
      <el-tab-pane label="清洗配置" name="cleaning">
        <el-form label-width="160px" style="max-width: 600px">
          <el-divider content-position="left">缺失值处理</el-divider>
          <el-form-item label="缺失值处理策略">
            <el-select v-model="cleanConfig.missing" style="width: 100%">
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
        <el-table-column label="所属受试者" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ subjectName(row.subject_id) }}</template>
        </el-table-column>
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
        <el-table-column label="所属受试者" width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ subjectName(row.subject_id) }}</template>
        </el-table-column>
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

    <!-- 批量脱敏确认弹窗 -->
    <el-dialog
      v-model="desensDialog"
      title="批量脱敏"
      width="820px"
      @closed="onDesensDialogClosed"
    >
      <el-alert
        type="warning"
        :closable="false"
        style="margin-bottom: 12px"
        title="脱敏产物会落盘留档（仍是加密存储），数据导出时可勾选「优先复用已存脱敏文件」直接取用，无需再临时处理。"
        description="视频人脸脱敏为不可逆变换且会移除音轨；视频属分钟级任务，提交后可随时关闭本窗口 —— 任务在服务端继续跑。"
      />

      <!-- 运行中：只看进度 -->
      <template v-if="desensRunning || desensProgress">
        <el-alert
          v-if="desensRunning"
          type="success"
          :closable="false"
          style="margin-bottom: 12px"
          title="任务已在服务端后台执行，现在就可以关闭本窗口 / 切到别的页面 / 刷新浏览器 —— 任务不会中断，列表上方的任务条会继续显示进度，回来时自动恢复。"
        />
        <el-alert
          v-if="desensProgress?.stale"
          type="warning"
          :closable="false"
          style="margin-bottom: 12px"
          :title="`进度已 ${desensIdleMinutes} 分钟没有更新，任务可能因服务重启而中断。可稍后刷新查看，或重新发起脱敏。`"
        />
        <el-progress
          :percentage="desensProgress?.percent || 0"
          :status="desensProgress?.status === 'failed'
            ? 'exception'
            : (desensProgress?.status === 'success' ? 'success' : undefined)"
          :stroke-width="14"
        />
        <div class="desens-progress-text">
          {{ desensProgress?.status_text || '正在准备...' }}
        </div>
        <el-descriptions v-if="desensProgress && desensProgress.status === 'success'" :column="2" border size="small" style="margin-top: 12px">
          <el-descriptions-item label="新生成">{{ desensProgress.done }} 个</el-descriptions-item>
          <el-descriptions-item label="复用已有">{{ desensProgress.reused }} 个</el-descriptions-item>
          <el-descriptions-item label="跳过">{{ desensProgress.skipped }} 个</el-descriptions-item>
          <el-descriptions-item label="失败">{{ desensProgress.failed }} 个</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="desensProgress?.errors?.length"
          type="error"
          :closable="false"
          style="margin-top: 12px"
          title="以下文件未能脱敏（已跳过，绝不会回退为原始数据）"
        >
          <div v-for="(e, i) in desensProgress.errors" :key="i" class="desens-err">
            {{ e.file_name }}：{{ e.reason }}
          </div>
        </el-alert>
        <div v-if="!desensRunning" style="margin-top: 14px">
          <el-button size="small" @click="resetDesensDialog">继续脱敏其他文件</el-button>
        </div>
      </template>

      <!-- 待提交：配置 + 计划 -->
      <template v-else>
        <el-form label-width="110px" v-loading="desensPreviewLoading">
          <el-form-item label="脱敏模态">
            <el-checkbox-group v-model="desensChecked" @change="refreshDesensPreview">
              <el-checkbox v-for="m in DESENS_MODALITIES" :key="m" :value="m">
                {{ desensModalityLabel[m] }}
                <span v-if="selectedModalityCounts[m]" class="desens-cnt">
                  （已选 {{ selectedModalityCounts[m] }}）
                </span>
              </el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="视频马赛克">
            <el-radio-group v-model="desensConfig.video_strength" @change="refreshDesensPreview">
              <el-radio-button v-for="o in VIDEO_STRENGTH_OPTIONS" :key="o.value" :value="o.value">
                {{ o.label }}
              </el-radio-button>
            </el-radio-group>
            <div class="desens-hint">
              {{ currentStrengthHint }}
              <br />
              实际块边长按"向上取整"选级，会比目标更粗（不会更细）；人脸越近越粗。
            </div>
          </el-form-item>
          <el-form-item label="已有产物">
            <el-switch v-model="desensForce" active-text="忽略已有产物，全部重做" @change="refreshDesensPreview" />
          </el-form-item>
        </el-form>

        <el-alert
          v-if="desensPreview"
          :type="desensPreview.to_process ? 'info' : 'success'"
          :closable="false"
          style="margin-bottom: 12px"
          :title="desensPreviewSummary"
        />

        <el-table :data="desensPreviewRows" border size="small" max-height="280">
          <el-table-column prop="file_name" label="文件名" min-width="220" show-overflow-tooltip />
          <el-table-column label="所属受试者" width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ row.subject_pseudo_id }}</template>
          </el-table-column>
          <el-table-column prop="modality_label" label="模态" width="90" />
          <el-table-column label="处置" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="desensActionTagType(row.action)">
                {{ desensActionLabel[row.action] || row.action }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="说明" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.reason || '—' }}</template>
          </el-table-column>
        </el-table>
        <div v-if="desensPreview?.truncated" class="desens-hint">
          仅展示前 {{ desensPreviewRows.length }} 条，另有 {{ desensPreview.truncated }} 条未展示（不影响执行）。
        </div>
      </template>

      <template #footer>
        <el-button @click="desensDialog = false">
          {{ desensRunning ? '转入后台并关闭' : '关闭' }}
        </el-button>
        <el-button
          v-if="!desensProgress"
          type="warning"
          :icon="Lock"
          :loading="submitting"
          :disabled="!desensPreview?.to_process"
          @click="confirmDesens"
        >
          执行批量脱敏<span v-if="desensPreview?.to_process">（{{ desensPreview.to_process }} 个）</span>
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Refresh, RefreshLeft, Search, MagicStick, Check, Select, DataAnalysis, Clock, CircleCheck, Warning, CircleClose, Lock, Loading } from '@element-plus/icons-vue'
import { ElMessage, ElNotification } from 'element-plus'
import * as echarts from 'echarts'
import {
  getAssetsApi, getSubjectsApi, triggerCleaningApi, triggerStandardizationApi,
  desensitizeExistingApi, desensitizePreviewApi, desensitizeStartApi,
  desensitizeProgressApi, desensitizeTasksApi,
} from '@/api/data'
import { getDashboardStatsApi } from '@/api/dashboard'
import { fetchAllPages } from '@/utils/fetchAll'
import { useUserStore } from '@/stores/user'
import { usePrivacyStore } from '@/stores/privacy'

const userStore = useUserStore()
const privacy = usePrivacyStore()
const canClean = computed(() => ['admin', 'engineer'].includes(userStore.role))

const activeTab = ref('access')
// 全量资产（一次拉取，筛选/分组/分页均在前端完成）
// 含 json 的原始列表：清洗/标准化流程忽略 json 模态（userInfo 备份等辅助文件），
// 但**批量脱敏需要它**（受试者信息脱敏的对象正是 userInfo.json）→ 仅脱敏模式纳入
const rawAssets = ref([])

// 原始深度序列资产（RealSense DZST，.zst）：不是可解码的视频，
// 而是需由后端 depth-video 端点实时转码才能播放的深度帧序列（见可视化页）。
// 它在库里 data_type='video'，若不排除会被当普通视频纳入清洗/标准化/脱敏，
// 而这些流程解不开 DZST 封装 → 必然失败。故与可视化页同口径，在源头滤掉。
// 判定双保险：扩展名 .zst 或 metadata.depth_raw（realsense._upload_raw_depth 写入）。
const isDepthRawAsset = (a) => {
  if (!a) return false
  const m = a.metadata || {}
  return (a.file_name || '').toLowerCase().endsWith('.zst') || m.depth_raw === true
}

const allAssets = computed(() => {
  const list = rawAssets.value.filter((a) => !isDepthRawAsset(a))
  return batchMode.value === 'desensitize'
    ? list
    : list.filter((a) => a.data_type !== 'json')
})
// 全量受试者（用于受试者分组展示 pseudo_id / 姓名）
const allSubjects = ref([])
const loading = ref(false)
const submitting = ref(false)
const selectedAssets = ref([])
const batchMode = ref('')

// ===== 筛选（作用于文件：只展示含匹配文件的受试者）=====
const filter = reactive({ data_type: '', layer: '', status: '', keyword: '' })

// 筛选后的文件集合（供图表统计与分组使用）
const filteredAssets = computed(() => {
  const kw = (filter.keyword || '').trim().toLowerCase()
  return allAssets.value.filter((a) => {
    if (filter.data_type && a.data_type !== filter.data_type) return false
    if (filter.layer && a.layer !== filter.layer) return false
    if (filter.status && a.status !== filter.status) return false
    if (kw && !(a.file_name || '').toLowerCase().includes(kw)) return false
    return true
  })
})

const subjectMap = computed(() => {
  const m = {}
  allSubjects.value.forEach((s) => { m[s.id] = s })
  return m
})

const subjectName = (subjectId) => {
  const s = subjectMap.value[subjectId]
  if (!s) return `受试者#${subjectId}`
  return s.pseudo_id || `受试者#${subjectId}`
}

// ===== 按受试者分组 =====
// 批量模式下只保留「该状态下可处理」的文件（清洗看 uploaded，标准化看 cleaned）
const groupList = computed(() => {
  const bySubject = new Map()
  for (const a of filteredAssets.value) {
    let g = bySubject.get(a.subject_id)
    if (!g) {
      const s = subjectMap.value[a.subject_id] || {}
      g = {
        subject_id: a.subject_id,
        pseudo_id: s.pseudo_id || `受试者#${a.subject_id}`,
        real_name: s.real_name || '',
        collection_batch: s.collection_batch || '',
        // 采集时间：统一用受试者创建时间（2026-10-06 用户决定，替换掉原「有真值用真值、
        // 没有才回退创建时间」的混合口径）。
        // ⚠️ 只改展示、不动库：subjects.collection_time 仍保存 userInfo 解析出的真实值
        // （UTC 口径见 app/services/subject_service.py::_parse_collection_time），
        // 本列不再采用它 —— 否则「页面口径」与「审计字段」会互相打架。
        collection_time: s.created_at || '',
        assets: [],
        newest_at: '',
      }
      bySubject.set(a.subject_id, g)
    }
    g.assets.push(a)
    if ((a.created_at || '') > g.newest_at) g.newest_at = a.created_at || ''
  }
  let groups = Array.from(bySubject.values())
  if (batchMode.value === 'clean' || batchMode.value === 'standard') {
    const want = batchMode.value === 'clean' ? 'uploaded' : 'cleaned'
    groups = groups
      .map((g) => ({ ...g, assets: g.assets.filter((a) => a.status === want) }))
      .filter((g) => g.assets.length)
  } else if (batchMode.value === 'desensitize') {
    // 只列「可脱敏」文件（视频/音频/脑电/心电/受试者信息）；
    // 已脱敏的**仍然列出**并打标 —— 否则用户想把已有产物按新档位重做就无从下手
    groups = groups
      .map((g) => ({ ...g, assets: g.assets.filter((a) => assetModality(a)) }))
      .filter((g) => g.assets.length)
  }
  // 组内文件按创建时间倒序；受试者按最近文件时间倒序
  groups.forEach((g) => {
    g.assets.sort((x, y) => (y.created_at || '').localeCompare(x.created_at || ''))
  })
  groups.sort((x, y) => (y.newest_at || '').localeCompare(x.newest_at || ''))
  return groups
})

// 分页（按受试者）
const pagination = reactive({ page: 1, page_size: 20, total: 0 })

const pagedGroups = computed(() => {
  const start = (pagination.page - 1) * pagination.page_size
  return groupList.value.slice(start, start + pagination.page_size)
})

watch(groupList, (list) => {
  pagination.total = list.length
  const maxPage = Math.max(1, Math.ceil(list.length / pagination.page_size))
  if (pagination.page > maxPage) pagination.page = maxPage
}, { immediate: true })

// ===== 选择（以文件为单位，跨受试者 / 跨页聚合）=====
const selectedIdSet = computed(() => new Set(selectedAssets.value.map((a) => a.id)))
const selectedSubjectCount = computed(() => new Set(selectedAssets.value.map((a) => a.subject_id)).size)

const clearSelection = () => {
  selectedAssets.value = []
}

const toggleAsset = (asset, checked) => {
  if (checked) {
    if (!selectedIdSet.value.has(asset.id)) selectedAssets.value = [...selectedAssets.value, asset]
  } else {
    selectedAssets.value = selectedAssets.value.filter((a) => a.id !== asset.id)
  }
}

const groupSelectedCount = (group) =>
  group.assets.reduce((n, a) => n + (selectedIdSet.value.has(a.id) ? 1 : 0), 0)

// 受试者行复选框：整选 / 半选（部分文件已选）
const groupAllSelected = (group) =>
  group.assets.length > 0 && groupSelectedCount(group) === group.assets.length
const groupPartialSelected = (group) => {
  const n = groupSelectedCount(group)
  return n > 0 && n < group.assets.length
}
const toggleGroupAll = (group, checked) => {
  if (checked) selectAllInGroup(group)
  else clearGroupSelection(group)
}

const groupTypeCounts = (group) => {
  const m = {}
  group.assets.forEach((a) => { m[a.data_type] = (m[a.data_type] || 0) + 1 })
  return m
}

const selectAllInGroup = (group) => {
  const have = selectedIdSet.value
  const add = group.assets.filter((a) => !have.has(a.id))
  if (!add.length) return
  selectedAssets.value = [...selectedAssets.value, ...add]
}

const clearGroupSelection = (group) => {
  const ids = new Set(group.assets.map((a) => a.id))
  selectedAssets.value = selectedAssets.value.filter((a) => !ids.has(a.id))
}

// 全选当前筛选结果（含批量模式下的状态约束），保留已选中的其他文件
const selectAllInFilter = () => {
  const have = new Set(selectedAssets.value.map((a) => a.id))
  const add = groupList.value.flatMap((g) => g.assets).filter((a) => !have.has(a.id))
  if (!add.length) {
    ElMessage.info('当前筛选结果已全部选中')
    return
  }
  selectedAssets.value = [...selectedAssets.value, ...add]
  ElMessage.success(`已选中 ${selectedAssets.value.length} 个文件`)
}

// 切换模式：按新模式下可见的文件裁剪已选（而非整表清空，避免"选完再切模式就白选"）
const onBatchModeChange = () => {
  const before = selectedAssets.value.length
  const visible = new Set(groupList.value.flatMap((g) => g.assets).map((a) => a.id))
  selectedAssets.value = selectedAssets.value.filter((a) => visible.has(a.id))
  const dropped = before - selectedAssets.value.length
  if (dropped > 0) {
    ElMessage.info(`已按当前模式过滤已选文件：保留 ${selectedAssets.value.length} 个，移除 ${dropped} 个`)
  }
  pagination.page = 1
}

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
  // userInfo 资产在库里是 json：平时不参与清洗，仅在批量脱敏模式下出现
  json: '受试者信息',
}

const statusType = (status) => {
  const map = { uploaded: 'info', cleaning: 'warning', cleaned: 'success', standardized: 'success' }
  return map[status] || 'info'
}

const dataTypeTagType = (t) => {
  const map = { video: '', audio: 'success', eeg: 'warning', ecg: 'danger', eye: 'info', gait: '', scale: 'success', task: 'warning' }
  return map[t] || ''
}

// ===== 批量脱敏（复用导出界面的同一套脱敏能力）=====
//
// 前端只做「列表筛选 + 选项」，真正的模态判定与产物复用判定都在后端
// （`export_service._asset_modality` / `desens_artifact.artifact_reuse_blocker`）。
// 前端判据若与后端分叉，最坏后果是列表里看不见某些文件（可用"单条查看"绕过），
// **不会**造成"以为脱敏了其实没脱" —— 那种错误只能由后端守住。
const DESENS_MODALITIES = ['video', 'audio', 'eeg', 'ecg', 'userinfo']
// userInfo 文件识别标识：后端按**文件名**判定（metadata.original_filename 优先，
// 规范化文件名兜底），故这里两个名字都要看
const USERINFO_NAME_HINT = 'userinfo'

// 资产 → 脱敏模态（'' 表示不参与脱敏）；顺序与后端 _asset_modality 保持一致
const assetModality = (a) => {
  const t = a.data_type
  if (t === 'video') return 'video'
  if (t === 'audio') return 'audio'
  if (t === 'eeg') return 'eeg'
  if (t === 'ecg') return 'ecg'
  const names = [a.metadata?.original_filename, a.file_name]
  if (names.some((n) => n && String(n).toLowerCase().includes(USERINFO_NAME_HINT))) return 'userinfo'
  return ''
}

// 视频马赛克档位。**名称只描述方向与相对关系，不承诺字面比值** ——
// 选级是"向上取整"且阶梯步长 2.0，实际块/人脸比值落在 [目标, 目标×2)，
// 即「加强档」在大脸上实际会到约 1/3（比名字更粗）。详见 video_desensitize 常量区。
const VIDEO_STRENGTH_OPTIONS = [
  { value: 'standard', label: '标准', hint: '目标 ≈ 人脸短边 ÷ 8（历史口径）' },
  { value: 'strong', label: '加强', hint: '目标 ≈ 人脸短边 ÷ 6' },
  { value: 'strongest', label: '最强', hint: '目标 ≈ 人脸短边 ÷ 4（默认）' },
]

// 勾选的模态（单一数据源：弹窗复选框直接绑它，避免与 config 双份状态不同步）
const desensChecked = ref([...DESENS_MODALITIES])
// 只有「视频档位」保留在页面级配置里，其余每次都现勾
// ⚠️ 必须与导出页的默认值同为 strongest：两侧一旦分叉，导出复用就会静默失效
// （2026-10-07 报障：批量脱敏选 strongest / 导出停在 strong ⇒ 三个视频全被重做）。
const desensConfig = ref({ video_strength: 'strongest' })
const desensModalityLabel = {
  userinfo: '受试者信息', audio: '音频', video: '视频', eeg: '脑电', ecg: '心电',
}
const currentStrengthHint = computed(() => {
  const o = VIDEO_STRENGTH_OPTIONS.find((x) => x.value === desensConfig.value.video_strength)
  return o ? o.hint : ''
})

const batchModeHint = computed(() => ({
  clean: '仅列出待清洗文件',
  standard: '仅列出待标准化文件',
  desensitize: '仅列出可脱敏文件（视频 / 音频 / 脑电 / 心电 / 受试者信息）',
}[batchMode.value] || ''))

// 已有脱敏产物：{ asset_id: { modality: {...} } }，供列表打「已脱敏」标记
const existingMap = ref({})
const DESENS_EXISTING_CHUNK = 5000

const loadExistingArtifacts = async () => {
  // 非 admin/engineer 调该接口必然 403，直接跳过以免刷无谓的错误提示
  if (!canClean.value) return
  // 只查当前可见范围内的资产（allAssets 已排除深度序列等不可处理项），
  // 避免为界面上不存在的资产白跑一批查询
  const ids = allAssets.value.map((a) => a.id)
  if (!ids.length) { existingMap.value = {}; return }
  const merged = {}
  try {
    for (let i = 0; i < ids.length; i += DESENS_EXISTING_CHUNK) {
      const res = await desensitizeExistingApi(ids.slice(i, i + DESENS_EXISTING_CHUNK))
      Object.assign(merged, res.data || {})
    }
    existingMap.value = merged
  } catch (e) {
    existingMap.value = {}
  }
}

// 该文件已有的脱敏产物（数组，可能多模态）
const existingArtifactsOf = (a) => Object.values(existingMap.value[a.id] || {})

// 已选文件中各模态的数量（弹窗里提示"这次会动哪些文件"）
const selectedModalityCounts = computed(() => {
  const m = {}
  selectedAssets.value.forEach((a) => {
    const k = assetModality(a)
    if (k) m[k] = (m[k] || 0) + 1
  })
  return m
})

// 文件大小格式化：字节 → B/KB/MB/GB/TB
const formatFileSize = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`
}

const onFilterChange = () => {
  pagination.page = 1
}

const onPageSizeChange = () => {
  pagination.page = 1
}

const onResetFilter = () => {
  filter.data_type = ''
  filter.layer = ''
  filter.status = ''
  filter.keyword = ''
  pagination.page = 1
  loadData()
}

const loadAllSubjects = async () => {
  if (allSubjects.value.length) return
  try {
    allSubjects.value = await fetchAllPages(getSubjectsApi)
  } catch (e) {
    allSubjects.value = []
  }
}

const loadData = async () => {
  loading.value = true
  try {
    const [assets] = await Promise.all([
      fetchAllPages(getAssetsApi),
      loadAllSubjects(),
    ])
    // 清洗流程忽略 json 模态（userInfo 备份等辅助文件）——
    // 脱敏模式由 allAssets 计算属性单独放行，见其定义处注释
    rawAssets.value = assets || []
    updateCharts()
    loadExistingArtifacts()
  } catch (e) {
    rawAssets.value = []
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
  // 模态类型分布饼图（基于当前筛选结果的全量文件）
  if (typePieChart) {
    const counts = {}
    filteredAssets.value.forEach((a) => {
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
    filteredAssets.value.forEach((a) => {
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

watch(filteredAssets, () => {
  updateCharts()
})

const handleResize = () => {
  typePieChart?.resize()
  statusBarChart?.resize()
}

// 清洗弹窗
const cleanDialog = ref(false)
const openCleanDialog = () => {
  if (!selectedAssets.value.length) {
    ElMessage.warning('请先在数据接入表格中展开受试者并勾选要清洗的文件')
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
    ElMessage.warning('请先在数据接入表格中展开受试者并勾选要标准化的文件')
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

// ===== 批量脱敏弹窗 =====

const desensDialog = ref(false)
const desensForce = ref(false)
const desensPreview = ref(null)
const desensPreviewLoading = ref(false)
// 任务状态以**服务端**为准（存 Redis，见 back/app/utils/task_store.py）：
// 本地只负责展示与轮询，因此关弹窗 / 切页 / 刷新都不会丢进度
const desensProgress = ref(null)
const desensRunning = ref(false)
const desensTaskId = ref('')
let desensTimer = null
let desensFailCount = 0

// 「进度多久没更新」按分钟展示（stale 时才用得到）
const desensIdleMinutes = computed(() =>
  Math.max(1, Math.floor((desensProgress.value?.idle_seconds || 0) / 60)))

const desensActionLabel = {
  desensitize: '重新脱敏', reuse: '复用已有', skip: '跳过', fail: '无法处理',
}
const desensActionTagType = (action) => ({
  desensitize: 'warning', reuse: 'success', skip: 'info', fail: 'danger',
}[action] || 'info')

const desensPreviewRows = computed(() => desensPreview.value?.items || [])

const desensPreviewSummary = computed(() => {
  const p = desensPreview.value
  if (!p) return ''
  const parts = [`共 ${p.total} 个文件`]
  if (p.to_process) parts.push(`待新生成 ${p.to_process} 个`)
  if (p.reuse_count) parts.push(`复用已有 ${p.reuse_count} 个`)
  if (p.skip_count) parts.push(`跳过 ${p.skip_count} 个（未勾选或不支持）`)
  if (p.fail_count) parts.push(`无法处理 ${p.fail_count} 个`)
  return parts.join('，')
})

// 工具栏入口：**发起新一批**脱敏（清掉上一次的进度展示，进入配置视图）
// 任务进行中时按钮本身已禁用，所以这里不必担心打断在跑的任务
const openDesensDialog = () => {
  if (!selectedAssets.value.length) {
    ElMessage.warning('请先在数据接入表格中展开受试者并勾选要脱敏的文件')
    return
  }
  desensProgress.value = null
  desensTaskId.value = ''
  desensForce.value = false
  desensPreview.value = null
  desensDialog.value = true
  refreshDesensPreview()
}

// 任务条入口：只看当前/最近一次任务的进度（**不要求**重新选文件）
const viewDesensProgress = () => {
  if (!desensProgress.value) return
  desensPreview.value = null
  desensDialog.value = true
}

// 任务条「收起」：仅结束态可见 —— 进行中的任务条不提供收起，
// 免得用户以为"收起 = 停止"，实际服务端还在跑
const dismissDesensBar = () => {
  if (desensRunning.value) return
  desensProgress.value = null
}

// 进度视图 →「继续脱敏其他文件」：回到配置视图
const resetDesensDialog = () => {
  desensProgress.value = null
  desensTaskId.value = ''
  desensFailCount = 0
  refreshDesensPreview()
}

const desensPayload = () => ({
  asset_ids: selectedAssets.value.map((a) => a.id),
  desensitize: {
    enabled: true,
    userinfo: desensChecked.value.includes('userinfo'),
    audio: desensChecked.value.includes('audio'),
    video: desensChecked.value.includes('video'),
    eeg: desensChecked.value.includes('eeg'),
    ecg: desensChecked.value.includes('ecg'),
    video_strength: desensConfig.value.video_strength,
  },
  force: desensForce.value,
})

const refreshDesensPreview = async () => {
  if (!desensDialog.value || !selectedAssets.value.length) return
  desensPreviewLoading.value = true
  try {
    const res = await desensitizePreviewApi(desensPayload())
    desensPreview.value = res.data || null
  } catch (e) {
    desensPreview.value = null
  } finally {
    desensPreviewLoading.value = false
  }
}

const confirmDesens = async () => {
  if (!desensPreview.value?.to_process) {
    ElMessage.warning('没有需要新处理的文件：可勾选「忽略已有产物，全部重做」或增选模态后再试')
    return
  }
  submitting.value = true
  try {
    const res = await desensitizeStartApi(desensPayload())
    const taskId = res.data?.task_id
    if (!taskId) {
      ElMessage.error('未能启动脱敏任务')
      return
    }
    desensRunning.value = true
    desensTaskId.value = taskId
    desensFailCount = 0
    desensProgress.value = { status: 'pending', percent: 0, status_text: '任务已提交，正在后台执行...' }
    startDesensPolling(taskId)
  } catch (e) { /* 拦截器已提示 */ }
  finally { submitting.value = false }
}

// 轮询间隔：进度按**文件数**上报，1.5s 足够；页面切到后台时降到 8s
// —— 任务本来就"挂在后台"，没必要因为界面开着就持续占用服务端请求。
// （gunicorn worker 的请求配额也因此更耐用）
const desensPollDelay = () =>
  (typeof document !== 'undefined' && document.hidden) ? 8000 : 1500

// 递归 setTimeout 而非 setInterval：这样才能按页面可见性动态调间隔，
// 并且保证"上一次请求回来之后才发下一次"，不会堆积
const startDesensPolling = (taskId) => {
  stopDesensPolling()
  desensTaskId.value = taskId
  const tick = async () => {
    try {
      const res = await desensitizeProgressApi(taskId)
      const t = res.data || {}
      desensFailCount = 0
      desensProgress.value = t
      if (t.status === 'success' || t.status === 'failed') {
        desensRunning.value = false
        desensTaskId.value = ''
        // 用通知而不是 message：用户很可能已经关掉弹窗在看别处
        if (t.status === 'success') {
          ElNotification.success({
            title: '批量脱敏完成',
            message: t.status_text || '',
            duration: 8000,
          })
          // 重新拉产物标记，并刷新计划（此时应大量变成"复用已有"）
          loadData()
          if (desensDialog.value && !desensProgress.value) refreshDesensPreview()
        } else {
          ElNotification.error({
            title: '批量脱敏失败',
            message: t.error || '请稍后重试或联系管理员',
            duration: 0,
          })
        }
        return
      }
      desensTimer = setTimeout(tick, desensPollDelay())
    } catch (e) {
      // 单次网络抖动不该放弃：连续 3 次失败才收手
      desensFailCount += 1
      if (desensFailCount < 3) {
        desensTimer = setTimeout(tick, 3000)
        return
      }
      desensRunning.value = false
      desensTaskId.value = ''
      ElMessage.warning('脱敏进度查询已中断（任务可能已完成或已过期），可刷新列表查看产物情况')
    }
  }
  desensTimer = setTimeout(tick, desensPollDelay())
}

const stopDesensPolling = () => {
  if (desensTimer) {
    clearTimeout(desensTimer)
    desensTimer = null
  }
}

// 页面加载/回到本页时"接管"之前挂到后台的任务
const adoptDesensTask = async () => {
  if (desensRunning.value) return
  try {
    const res = await desensitizeTasksApi({ active: 1, limit: 1 })
    const t = (res.data?.items || [])[0]
    if (!t || !t.task_id) return
    desensProgress.value = t
    desensRunning.value = true
    desensFailCount = 0
    startDesensPolling(t.task_id)
    ElMessage.info('检测到后台仍在执行脱敏任务，已恢复进度显示')
  } catch (e) {
    // 没有进行中的任务不算错误
  }
}

const onDesensDialogClosed = () => {
  // 任务在**服务端**继续跑，关窗不打断它。
  // 进度保留在列表上方的任务条里（同一个 desensProgress），所以不清空状态 ——
  // 清掉反而会让任务条消失、用户失去唯一的进度入口。
  if (!desensRunning.value && desensTaskId.value) {
    desensTaskId.value = ''
  }
  desensPreview.value = null
}

// 页面重新可见时立刻补一次进度（后台时轮询降到 8s，不该让用户白等）
const onVisibilityChange = () => {
  if (!document.hidden && desensRunning.value && desensTaskId.value) {
    startDesensPolling(desensTaskId.value)
  }
}

onMounted(async () => {
  await loadData()
  loadDashboardStat()
  await nextTick()
  initCharts()
  updateCharts()
  // 姓名遮罩依赖脱敏规则；管理员若尚未加载则补一次（幂等）
  privacy.loadConfig()
  window.addEventListener('resize', handleResize)
  document.addEventListener('visibilitychange', onVisibilityChange)
  // 之前"挂到后台"的脱敏任务：回到本页自动接管，继续显示进度
  adoptDesensTask()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  // 别把轮询计时器留到组件销毁之后（后台任务在服务端继续跑，本地停止拉取即可）
  stopDesensPolling()
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
.batch-bar-tip {
  font-size: 12px;
  color: #909399;
}

/* ===== 后台脱敏任务条 =====
   任务"挂到后台"后仍常驻：进度来自服务端任务状态，关弹窗/切页/刷新都不会丢 */
.desens-taskbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: #fff9e6;
  border: 1px solid #faecd8;
  border-radius: 4px;
}
.desens-taskbar.is-success {
  background: #f0f9eb;
  border-color: #e1f3d8;
}
.desens-taskbar.is-failed {
  background: #fef0f0;
  border-color: #fde2e2;
}
.desens-taskbar-icon {
  font-size: 16px;
  color: #e6a23c;
}
.desens-taskbar.is-success .desens-taskbar-icon {
  color: #67c23a;
}
.desens-taskbar.is-failed .desens-taskbar-icon {
  color: #f56c6c;
}
.desens-taskbar-icon.spinning {
  animation: desens-spin 1.2s linear infinite;
}
@keyframes desens-spin {
  to { transform: rotate(360deg); }
}
.desens-taskbar-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
}
.desens-taskbar-bar {
  flex: 0 0 180px;
}
.desens-taskbar-text {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.desens-taskbar-actions {
  flex: 0 0 auto;
}
.desens-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.7;
  margin-top: 4px;
}
.desens-cnt {
  color: #e6a23c;
}
.desens-none {
  color: #c0c4cc;
}
.desens-progress-text {
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}
.desens-err {
  font-size: 12px;
  line-height: 1.8;
}
.table-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.table-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.table-head-tip {
  font-size: 12px;
  color: #909399;
}
.group-panel {
  padding: 8px 12px 12px;
  background: #fafcff;
}
.group-panel-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 13px;
  color: #606266;
}
.subject-pseudo {
  font-weight: 500;
}
.subject-name {
  color: #909399;
  margin-left: 2px;
}
.sel-count {
  color: #c0c4cc;
}
.sel-count-on {
  color: #e6a23c;
  font-weight: 600;
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
