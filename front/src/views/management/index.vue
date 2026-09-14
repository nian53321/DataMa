<template>
  <div class="page-container">
    <div class="page-header">
      <span class="title">数据管理</span>
      <el-space>
        <el-button v-if="canEdit" :icon="Plus" @click="openSubjectDialog">新增受试者</el-button>
        <el-button v-if="canEdit" type="primary" :icon="Upload" @click="openAccessDialog">数据接入</el-button>
        <el-button v-if="canEdit" :icon="RefreshLeft" @click="openScanConfigDialog">自动扫描配置</el-button>
        <el-button :icon="Document" @click="openLogDialog">操作日志</el-button>
      </el-space>
    </div>

    <el-radio-group v-model="viewMode" style="margin-bottom: 12px" @change="onViewModeChange">
      <el-radio-button value="subject">按受试者</el-radio-button>
      <el-radio-button value="type">按数据类型</el-radio-button>
    </el-radio-group>

    <!-- 功能卡片：点击进入对应操作（flex 自适应占满整行，不依赖卡片数量） -->
    <div class="feature-row">
      <div v-for="f in features" :key="f.title" class="feature-col">
        <el-card shadow="hover" class="feature-card" @click="handleFeature(f.key)">
          <el-icon :size="32" :color="f.color"><component :is="f.icon" /></el-icon>
          <div class="feature-title">{{ f.title }}</div>
          <div class="feature-desc">{{ f.desc }}</div>
        </el-card>
      </div>
    </div>

    <!-- 数据动态可视化区 -->
    <el-card shadow="hover" style="margin-top: 16px" v-loading="dataStatsLoading">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>数据动态</span>
          <div style="display: flex; align-items: center; gap: 12px">
            <el-radio-group v-model="dataStatsDays" size="small" @change="loadDataStats">
              <el-radio-button :value="7">近7天</el-radio-button>
              <el-radio-button :value="30">近30天</el-radio-button>
              <el-radio-button :value="90">近90天</el-radio-button>
            </el-radio-group>
            <el-button text :icon="Refresh" @click="loadDataStats">刷新</el-button>
          </div>
        </div>
      </template>

      <!-- 统计卡片 -->
      <el-row :gutter="16">
        <el-col :span="6" v-for="card in dataStatCards" :key="card.title">
          <div class="data-stat-card">
            <el-icon :size="36" :color="card.color">
              <component :is="card.icon" />
            </el-icon>
            <div class="data-stat-info">
              <div class="data-stat-value">{{ card.value }}</div>
              <div class="data-stat-title">{{ card.title }}</div>
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 图表区 第一行：接入趋势 / 模态分布 / 风险分级 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="10">
          <div class="chart-block">
            <div class="chart-block-title">数据接入趋势（{{ dataStatsDays }} 天）</div>
            <div ref="trendChartRef" style="height: 280px"></div>
          </div>
        </el-col>
        <el-col :span="7">
          <div class="chart-block">
            <div class="chart-block-title">数据资产模态分布</div>
            <div ref="typeChartRef" style="height: 280px"></div>
          </div>
        </el-col>
        <el-col :span="7">
          <div class="chart-block">
            <div class="chart-block-title">受试者认知风险分级</div>
            <div ref="riskChartRef" style="height: 280px"></div>
          </div>
        </el-col>
      </el-row>

      <!-- 图表区 第二行：性别分布 / 数据湖分层（替代原独立分层卡片，整合到动态区） -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="8">
          <div class="chart-block">
            <div class="chart-block-title">受试者性别分布</div>
            <div ref="genderChartRef" style="height: 240px"></div>
          </div>
        </el-col>
        <el-col :span="16">
          <div class="chart-block">
            <div class="chart-block-title">数据湖分层概览</div>
            <div ref="layerChartRef" style="height: 240px"></div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 受试者列表 -->
    <el-card v-if="viewMode === 'subject'" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
          <span>受试者列表</span>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <el-button type="primary" plain :icon="Check" @click="selectAllSubjectsAcrossPages" :loading="selectAllSubjectsLoading">
              跨页全选
            </el-button>
            <el-button :icon="CircleClose" :disabled="!selectedSubjects.length" @click="clearAllSubjectSelection">
              清空选择
            </el-button>
            <el-tag v-if="selectedSubjects.length" type="warning" effect="plain">
              已选 {{ selectedSubjects.length }} 个受试者<span v-if="selectedSubjects.length > subjectList.length">（含其他页）</span>
            </el-tag>
            <el-button v-if="canDelete" type="danger" plain :icon="Delete" :disabled="!selectedSubjects.length" @click="batchRemoveSubjects">
              批量删除<span v-if="selectedSubjects.length"> ({{ selectedSubjects.length }})</span>
            </el-button>
          </div>
        </div>
      </template>
      <!-- 筛选栏 -->
      <el-form :inline="true" style="margin-bottom: 12px">
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" placeholder="伪ID/备注" clearable style="width: 180px" @keyup.enter="onSearch" @clear="onSearch" />
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="filters.gender" placeholder="全部" clearable style="width: 100px" @change="onSearch">
            <el-option label="男" value="男" />
            <el-option label="女" value="女" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险分级">
          <el-select v-model="filters.riskLevel" placeholder="全部" clearable style="width: 140px" @change="onSearch">
            <el-option label="无" value="无" />
            <el-option label="轻度" value="轻度" />
            <el-option label="中度" value="中度" />
            <el-option label="重度" value="重度" />
            <el-option label="正常" value="normal" />
            <el-option label="轻度认知障碍" value="mci" />
            <el-option label="痴呆" value="dementia" />
            <el-option label="未评估" value="none" />
          </el-select>
        </el-form-item>
        <el-form-item label="批次">
          <el-select v-model="filters.batch" placeholder="全部" clearable filterable style="width: 160px" @change="onSearch">
            <el-option v-for="b in batchOptions" :key="b" :label="b" :value="b" />
          </el-select>
        </el-form-item>
        <el-form-item label="视频">
          <el-select v-model="filters.hasVideo" placeholder="全部" clearable style="width: 110px" @change="onSearch">
            <el-option label="已采集" value="true" />
            <el-option label="未采集" value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="onSearch">查询</el-button>
          <el-button :icon="RefreshLeft" @click="onResetFilters">重置</el-button>
        </el-form-item>
      </el-form>
      <el-table
        ref="subjectTableRef"
        :data="subjectList"
        border
        stripe
        v-loading="tableLoading"
        :row-key="(row) => row.id"
        @selection-change="onSubjectSelectionChange"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column prop="pseudo_id" label="伪ID" min-width="170" show-overflow-tooltip />
        <el-table-column prop="real_name" label="姓名" width="110" show-overflow-tooltip align="center" header-align="center">
          <template #default="{ row }">{{ row.real_name || '—' }}</template>
        </el-table-column>
        <el-table-column prop="age" label="年龄" width="60" align="center" header-align="center" />
        <el-table-column prop="gender" label="性别" width="60" align="center" header-align="center" />
        <el-table-column prop="phone" label="联系电话" min-width="100" show-overflow-tooltip align="center" header-align="center">
          <template #default="{ row }">{{ row.phone || '—' }}</template>
        </el-table-column>
        <el-table-column prop="id_card" label="身份证号" min-width="120" show-overflow-tooltip align="center" header-align="center">
          <template #default="{ row }">{{ row.id_card || '—' }}</template>
        </el-table-column>
        <el-table-column prop="cognitive_risk_level" label="认知风险分级" width="120" align="center" header-align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="riskTagType(row.cognitive_risk_level)">{{ riskText(row.cognitive_risk_level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="mmse_score" label="MMSE" width="70" align="center" header-align="center">
          <template #default="{ row }">{{ row.mmse_score ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="moca_score" label="MoCA" width="70" align="center" header-align="center">
          <template #default="{ row }">{{ row.moca_score ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="ad8_score" label="AD8" width="70" align="center" header-align="center">
          <template #default="{ row }">{{ row.ad8_score ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="collection_batch" label="批次" width="100" show-overflow-tooltip align="center" header-align="center">
          <template #default="{ row }">{{ row.collection_batch || '—' }}</template>
        </el-table-column>
        <el-table-column prop="collection_scene" label="场景" width="100" show-overflow-tooltip align="center" header-align="center">
          <template #default="{ row }">{{ row.collection_scene || '—' }}</template>
        </el-table-column>
        <el-table-column label="视频采集" width="120" align="center" header-align="center">
          <template #default="{ row }">
            <div style="display: flex; gap: 4px; justify-content: center; flex-wrap: wrap">
              <el-tag
                v-for="t in [
                  { value: 'face', label: '面' },
                  { value: 'body', label: '身' },
                  { value: 'gait', label: '步' },
                ]"
                :key="t.value"
                :type="(row.video_types || []).includes(t.value) ? 'success' : 'info'"
                size="small"
                effect="plain"
              >
                {{ t.label }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="150" show-overflow-tooltip align="center" header-align="center" />
        <el-table-column label="操作" min-width="360" fixed="right">
          <template #default="{ row }">
            <div style="white-space: nowrap">
            <el-button size="small" link type="primary" @click="viewAssets(row)">数据资产</el-button>
            <el-button size="small" link type="success" @click="goVisualization(row)">可视化</el-button>
            <el-button v-if="canEdit" size="small" link type="primary" @click="openVideoCapture(row)">视频采集</el-button>
            <el-button v-if="canEdit" size="small" link type="warning" @click="openEditDialog(row)">编辑</el-button>
            <el-button size="small" link type="info" @click="openHistory(row, 'subject', `受试者 ${row.pseudo_id}`)">历史</el-button>
            <el-button v-if="canDelete" size="small" link type="danger" @click="removeSubject(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 12px; justify-content: flex-end"
        v-model:current-page="pagination.page"
        v-model:page-size="pagination.page_size"
        :total="pagination.total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="loadSubjects"
        @size-change="loadSubjects"
      />
    </el-card>

    <!-- 按数据类型视图：数据资产管理 -->
    <el-card v-if="viewMode === 'type'" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
          <span>数据资产管理</span>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <el-button type="primary" plain :icon="Check" @click="selectAllAcrossPages" :loading="selectAllLoading">
              跨页全选
            </el-button>
            <el-button :icon="CircleClose" :disabled="!selectedAssets.length" @click="clearAllSelection">
              清空选择
            </el-button>
            <el-tag v-if="selectedAssets.length" type="warning" effect="plain">
              已选 {{ selectedAssets.length }} 个文件<span v-if="selectedAssets.length > assetList.length">（含其他页）</span>
            </el-tag>
            <el-button v-if="canDelete" type="danger" plain :icon="Delete" :disabled="!selectedAssets.length" @click="batchRemoveAssets">
              批量删除<span v-if="selectedAssets.length"> ({{ selectedAssets.length }})</span>
            </el-button>
          </div>
        </div>
      </template>
      <el-form :inline="true" style="margin-bottom: 12px">
        <el-form-item label="数据类型">
          <el-select v-model="assetFilters.data_type" placeholder="全部" clearable style="width: 140px" @change="onAssetSearch">
            <el-option label="视频" value="video" />
            <el-option label="音频" value="audio" />
            <el-option label="脑电 EEG" value="eeg" />
            <el-option label="心电 ECG" value="ecg" />
            <el-option label="眼动" value="eye" />
            <el-option label="步态" value="gait" />
            <el-option label="量表" value="scale" />
            <el-option label="认知任务" value="task" />
            <el-option label="辅助数据" value="json" />
          </el-select>
        </el-form-item>
        <el-form-item label="分层">
          <el-select v-model="assetFilters.layer" placeholder="全部" clearable style="width: 140px" @change="onAssetSearch">
            <el-option label="原始 raw" value="raw" />
            <el-option label="清洗 cleaned" value="cleaned" />
            <el-option label="特征 feature" value="feature" />
            <el-option label="标注 annotation" value="annotation" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="assetFilters.status" placeholder="全部" clearable style="width: 140px" @change="onAssetSearch">
            <el-option label="已上传" value="uploaded" />
            <el-option label="清洗中" value="cleaning" />
            <el-option label="已标准化" value="standardized" />
            <el-option label="标注中" value="annotating" />
            <el-option label="已完成" value="done" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="assetFilters.keyword" placeholder="文件名" clearable style="width: 180px" @keyup.enter="onAssetSearch" @clear="onAssetSearch" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="onAssetSearch">查询</el-button>
          <el-button :icon="RefreshLeft" @click="onResetAssetFilters">重置</el-button>
        </el-form-item>
      </el-form>
      <el-table
        ref="assetTableRef"
        :data="assetList"
        border
        stripe
        v-loading="assetTableLoading"
        :row-key="(row) => row.id"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="data_type" label="数据类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="dataTypeTagType(row.data_type)">{{ typeText(row.data_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="所属受试者" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="subjectMap[row.subject_id]">
              {{ subjectMap[row.subject_id].pseudo_id }}
              <span v-if="subjectMap[row.subject_id].name" style="color: #909399; margin-left: 4px">
                ({{ subjectMap[row.subject_id].name }})
              </span>
            </span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="layer" label="分层" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="layerTagType(row.layer)">{{ layerText(row.layer) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTagType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="质量" width="120">
          <template #default="{ row }">
            <span v-if="row.quality_score != null">{{ row.quality_score.toFixed(1) }}</span>
            <el-tag v-if="row.is_qualified === false" size="small" type="danger" style="margin-left: 4px">不合格</el-tag>
            <span v-else-if="row.quality_score == null">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="file_format" label="格式" width="80">
          <template #default="{ row }">{{ row.file_format || '—' }}</template>
        </el-table-column>
        <el-table-column label="文件大小" width="110" align="right">
          <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="160">
          <template #default="{ row }">{{ row.created_at || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="success" @click="goVisualization({ id: row.subject_id })">可视化</el-button>
            <el-button v-if="canEdit" size="small" link type="warning" @click="openAssetEditDialog(row)">编辑</el-button>
            <el-button size="small" link type="info" @click="openHistory(row, 'data_asset', `数据资产 ${row.file_name}`)">历史</el-button>
            <el-button v-if="canDelete" size="small" link type="danger" @click="removeAsset(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 12px; justify-content: flex-end"
        v-model:current-page="assetPagination.page"
        v-model:page-size="assetPagination.page_size"
        :total="assetPagination.total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="loadAssetsByType"
        @size-change="loadAssetsByType"
      />
    </el-card>

    <!-- 新增/编辑受试者弹窗 -->
    <el-dialog v-model="subjectDialog" :title="editingId ? '编辑受试者' : '新增受试者'" width="640px">
      <el-form ref="subjectFormRef" :model="subjectForm" label-width="110px">
        <el-row :gutter="12">
          <el-col v-for="f in subjectTplFields" :key="f.field_key" :span="f.span || 24">
            <el-form-item :label="f.field_label" :required="f.required">
              <el-input v-if="f.field_type === 'input'" v-model="subjectForm[f.field_key]" :placeholder="f.placeholder" />
              <el-input-number v-else-if="f.field_type === 'number'" v-model="subjectForm[f.field_key]" :min="0" :max="f.max ?? 120" style="width: 100%" :placeholder="f.placeholder" />
              <el-select v-else-if="f.field_type === 'select'" v-model="subjectForm[f.field_key]" style="width: 100%" clearable :placeholder="f.placeholder">
                <el-option v-for="o in (f.options || [])" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
              <!-- 批次/场景：单行文本输入框 + 下拉建议已有记录（最新优先），支持自由输入新值 -->
              <el-autocomplete
                v-else-if="f.field_type === 'autocomplete'"
                v-model="subjectForm[f.field_key]"
                :fetch-suggestions="((query, cb) => _fetchFieldSuggestions(f.field_key, query, cb))"
                :placeholder="f.placeholder"
                clearable
                style="width: 100%"
                @select="() => {}"
              />
              <el-input v-else-if="f.field_type === 'textarea'" v-model="subjectForm[f.field_key]" type="textarea" :rows="2" :placeholder="f.placeholder" />
              <el-date-picker v-else-if="f.field_type === 'date'" v-model="subjectForm[f.field_key]" type="date" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 数据文件上传（仅新增时显示） -->
        <el-form-item label="数据文件" v-if="!editingId">
          <el-radio-group v-model="subjectImportMode" @change="onImportModeChange">
            <el-radio-button value="none">不上传</el-radio-button>
            <el-radio-button value="files">添加文件</el-radio-button>
            <el-radio-button value="folder">添加文件夹</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- 添加文件模式 -->
        <template v-if="!editingId && subjectImportMode === 'files'">
          <el-form-item label="选择文件">
            <el-upload
              :auto-upload="false"
              multiple
              :on-change="handleSubjectFileChange"
              :file-list="subjectFileList"
            >
              <el-button type="primary" plain :icon="Upload">选择文件</el-button>
              <template #tip>
                <div class="el-upload__tip">支持多文件，系统按文件名和后缀自动识别模态类型</div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item label="文件清单" v-if="subjectFileList.length">
            <el-table :data="subjectFileList" border size="small" max-height="200">
              <el-table-column prop="file_name" label="文件名" min-width="220" />
              <el-table-column prop="data_type" label="识别类型" width="100">
                <template #default="{ row }">
                  <el-tag size="small">{{ typeText(row.data_type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="file_size" label="大小(KB)" width="100" />
            </el-table>
          </el-form-item>
        </template>

        <!-- 添加文件夹模式 -->
        <template v-if="!editingId && subjectImportMode === 'folder'">
          <el-alert
            v-if="!supportsFolderSelect"
            type="warning"
            :closable="false"
            title="当前浏览器不支持文件夹选择"
            description="请使用 Chrome、Edge 等基于 Chromium 的浏览器，或切换为「添加文件」模式。"
            show-icon
            style="margin-bottom: 12px"
          />
          <el-form-item label="选择文件夹">
            <input
              ref="subjectFolderInputRef"
              type="file"
              webkitdirectory
              directory
              multiple
              style="display: none"
              @change="handleSubjectFolderChange"
            />
            <el-button type="primary" plain :icon="FolderOpened" @click="subjectFolderInputRef?.click()">
              选择文件夹
            </el-button>
            <span v-if="subjectFolderFiles.length" style="margin-left: 12px; color: #67c23a">
              已选 {{ subjectFolderFiles.length }} 个文件
            </span>
          </el-form-item>
          <el-form-item label="文件清单" v-if="subjectFolderFiles.length">
            <el-table :data="subjectFolderFiles" border size="small" max-height="200">
              <el-table-column prop="file_name" label="文件名" min-width="220" />
              <el-table-column prop="data_type" label="识别类型" width="100">
                <template #default="{ row }">
                  <el-tag size="small">{{ typeText(row.data_type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="file_size" label="大小(KB)" width="100" />
            </el-table>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="subjectDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveSubject">保存</el-button>
      </template>
    </el-dialog>

    <!-- 上传进度弹窗 -->
    <el-dialog v-model="batchUploadProgress.visible" title="正在上传数据文件" width="540px" :close-on-click-modal="false" :show-close="batchUploadProgress.current >= batchUploadProgress.total">
      <el-progress
        :percentage="batchUploadProgress.total ? Math.round(batchUploadProgress.current / batchUploadProgress.total * 100) : 0"
        :status="batchUploadProgress.current >= batchUploadProgress.total ? (batchUploadProgress.failCount ? 'warning' : 'success') : ''"
        style="margin-bottom: 12px"
      />
      <div style="margin-bottom: 8px; color: #606266">
        进度：{{ batchUploadProgress.current }} / {{ batchUploadProgress.total }}
        <el-tag type="success" size="small" style="margin-left: 8px">成功 {{ batchUploadProgress.successCount }}</el-tag>
        <el-tag type="danger" size="small" style="margin-left: 4px" v-if="batchUploadProgress.failCount">失败 {{ batchUploadProgress.failCount }}</el-tag>
      </div>
      <div v-if="batchUploadProgress.currentFile" style="margin-bottom: 8px; color: #909399; font-size: 12px">
        正在上传：{{ batchUploadProgress.currentFile }}
      </div>
      <el-table v-if="batchUploadProgress.failures.length" :data="batchUploadProgress.failures" border size="small" max-height="160" style="margin-top: 8px">
        <el-table-column prop="name" label="失败文件" min-width="200" show-overflow-tooltip />
        <el-table-column prop="reason" label="失败原因" min-width="180" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="batchUploadProgress.visible = false" :disabled="batchUploadProgress.current < batchUploadProgress.total">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 数据接入弹窗 -->
    <el-dialog v-model="accessDialog" title="数据接入" width="680px">
      <el-form :model="assetForm" label-width="100px">
        <el-form-item label="关联受试者" required>
          <el-select v-model="assetForm.subject_id" filterable placeholder="选择受试者" style="width: 100%">
            <el-option
              v-for="s in subjectList"
              :key="s.id"
              :label="s.pseudo_id"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="导入方式">
          <el-radio-group v-model="importMode">
            <el-radio-button value="single">单文件</el-radio-button>
            <el-radio-button value="folder">整个文件夹</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- 单文件模式 -->
        <template v-if="importMode === 'single'">
          <el-form-item label="数据类型" required>
            <el-select v-model="assetForm.data_type" style="width: 100%">
              <el-option label="视频" value="video" />
              <el-option label="音频" value="audio" />
              <el-option label="脑电 EEG" value="eeg" />
              <el-option label="心电 ECG" value="ecg" />
              <el-option label="眼动" value="eye" />
              <el-option label="步态" value="gait" />
              <el-option label="量表" value="scale" />
              <el-option label="认知任务" value="task" />
            </el-select>
          </el-form-item>
          <el-form-item label="文件">
            <el-upload
              ref="singleUploadRef"
              drag
              :auto-upload="false"
              :limit="1"
              :on-change="handleFileChange"
              :on-exceed="handleExceed"
            >
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">拖拽文件到此处，或<em>点击上传</em></div>
              <template #tip>
                <div class="el-upload__tip">
                  命名规范：模态类型_受试者伪ID_采集时间戳_场景代码_批次号.后缀
                </div>
              </template>
            </el-upload>
            <el-progress
              v-if="uploadProgress > 0 && uploadProgress < 100"
              :percentage="uploadProgress"
              :stroke-width="6"
              style="margin-top: 8px"
            />
          </el-form-item>
          <el-form-item label="文件名">
            <el-input v-model="assetForm.file_name" placeholder="自动生成或手动填写" />
          </el-form-item>
          <el-form-item label="采样率">
            <el-input-number v-model="assetForm.sample_rate" :min="1" />
            <span style="margin-left: 8px; color: #909399">Hz（视频/音频可留空）</span>
          </el-form-item>
        </template>

        <!-- 文件夹批量模式 -->
        <template v-else>
          <el-form-item label="选择文件夹">
            <input
              ref="folderInputRef"
              type="file"
              webkitdirectory
              directory
              multiple
              style="display: none"
              @change="handleFolderChange"
            />
            <el-button type="primary" plain :icon="FolderOpened" @click="folderInputRef?.click()">
              选择文件夹
            </el-button>
            <span v-if="folderFiles.length" style="margin-left: 12px; color: #67c23a">
              已选 {{ folderFiles.length }} 个文件
            </span>
          </el-form-item>
          <el-form-item label="类型识别">
            <el-alert
              type="info"
              :closable="false"
              title="系统按文件后缀自动识别模态类型：mp4/avi→视频，wav/mp3→音频，edf/bdf→脑电，csv→量表/任务"
            />
          </el-form-item>
          <el-form-item label="文件清单">
            <el-table :data="folderFiles" border size="small" max-height="240">
              <el-table-column prop="file_name" label="文件名" min-width="220" />
              <el-table-column prop="data_type" label="识别类型" width="100">
                <template #default="{ row }">
                  <el-tag size="small">{{ typeText(row.data_type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="file_size" label="大小(KB)" width="100" />
            </el-table>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="accessDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleCreateAsset">
          {{ importMode === 'single' ? '接入' : '批量导入' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 数据资产查看弹窗 -->
    <el-dialog v-model="assetListDialog" :title="`${currentSubject?.pseudo_id || ''} 的数据资产`" width="900px">
      <el-table :data="currentAssets" border stripe size="small">
        <el-table-column prop="file_name" label="文件名" min-width="180" />
        <el-table-column prop="data_type" label="类型" width="80">
          <template #default="{ row }">{{ typeText(row.data_type) }}</template>
        </el-table-column>
        <el-table-column prop="layer" label="分层" width="80" />
        <el-table-column prop="file_format" label="格式" width="80">
          <template #default="{ row }">{{ row.file_format || '—' }}</template>
        </el-table-column>
        <el-table-column label="文件大小" width="100" align="right">
          <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canEdit" size="small" link type="warning" @click="openAssetEditDialog(row)">编辑</el-button>
            <el-button v-if="canDelete" size="small" link type="danger" @click="removeAsset(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="assetListDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 数据资产编辑弹窗 -->
    <el-dialog v-model="assetEditDialog" title="编辑数据资产" width="520px">
      <el-form :model="assetEditForm" label-width="100px">
        <el-form-item label="文件名">
          <el-input v-model="assetEditForm.file_name" placeholder="文件显示名称" />
        </el-form-item>
        <el-form-item label="数据类型">
          <el-select v-model="assetEditForm.data_type" style="width: 100%">
            <el-option label="视频" value="video" />
            <el-option label="音频" value="audio" />
            <el-option label="脑电 EEG" value="eeg" />
            <el-option label="心电 ECG" value="ecg" />
            <el-option label="眼动" value="eye" />
            <el-option label="步态" value="gait" />
            <el-option label="量表" value="scale" />
            <el-option label="认知任务" value="task" />
            <el-option label="辅助数据" value="json" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据分层">
          <el-select v-model="assetEditForm.layer" style="width: 100%">
            <el-option label="原始 raw" value="raw" />
            <el-option label="清洗 cleaned" value="cleaned" />
            <el-option label="特征 feature" value="feature" />
            <el-option label="标注 annotation" value="annotation" />
          </el-select>
        </el-form-item>
        <el-form-item label="文件格式">
          <el-input v-model="assetEditForm.file_format" placeholder="如 mp4 / edf / csv" />
        </el-form-item>
        <el-form-item label="采样率">
          <el-input-number v-model="assetEditForm.sample_rate" :min="1" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assetEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSaveAsset">保存</el-button>
      </template>
    </el-dialog>

    <!-- 操作日志弹窗 -->
    <el-dialog v-model="logDialog" title="操作日志" width="900px">
      <el-form :inline="true" style="margin-bottom: 12px">
        <el-form-item label="用户">
          <el-input v-model="logFilters.username" placeholder="用户名" clearable style="width: 140px" @keyup.enter="loadLogs" @clear="loadLogs" />
        </el-form-item>
        <el-form-item label="操作">
          <el-select v-model="logFilters.action" placeholder="全部" clearable style="width: 130px" @change="loadLogs">
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
          </el-select>
        </el-form-item>
        <el-form-item label="对象">
          <el-select v-model="logFilters.target_type" placeholder="全部" clearable style="width: 140px" @change="loadLogs">
            <el-option label="受试者" value="subject" />
            <el-option label="数据资产" value="asset" />
            <el-option label="标注任务" value="annotation_task" />
            <el-option label="标签" value="label" />
            <el-option label="用户" value="user" />
            <el-option label="角色菜单" value="role_menu" />
            <el-option label="脱敏配置" value="desensitize_config" />
            <el-option label="数据快照" value="snapshot" />
            <el-option label="规范" value="standard" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="loadLogs">查询</el-button>
        </el-form-item>
      </el-form>
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
        <el-table-column prop="target_type" label="对象" width="100">
          <template #default="{ row }">{{ targetTypeText(row.target_type) }}</template>
        </el-table-column>
        <el-table-column prop="target_id" label="ID" width="70" />
        <el-table-column prop="detail" label="详情" min-width="220" show-overflow-tooltip />
        <el-table-column prop="ip" label="IP" width="120">
          <template #default="{ row }">{{ row.ip || '—' }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 12px; justify-content: flex-end"
        v-model:current-page="logPagination.page"
        v-model:page-size="logPagination.page_size"
        :total="logPagination.total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @current-change="loadLogs"
        @size-change="loadLogs"
      />
    </el-dialog>

    <!-- 历史版本弹窗 -->
    <VersionHistoryDialog
      v-model:visible="historyVisible"
      :model-type="historyModel.modelType"
      :model-id="historyModel.modelId"
      :model-label="historyModel.modelLabel"
      :model-key="historyModel.modelKey"
      @rollback-success="onHistoryRollback"
    />

    <!-- 全局版本管理弹窗 -->
    <GlobalVersionDialog
      v-model:visible="globalVersionVisible"
      @rollback-success="loadSubjects"
    />

    <!-- 自动扫描配置弹窗 -->
    <el-dialog v-model="scanConfigDialog" title="受试者文件夹自动扫描" width="1000px" top="5vh">
      <div style="max-height: 75vh; overflow-y: auto; padding-right: 8px">
      <el-tabs v-model="scanActiveTab" style="margin-bottom: 4px">
        <el-tab-pane label="浏览器目录监控" name="browser">
          <el-alert
            v-if="!browserScan.supported"
            type="warning"
            :closable="false"
            :title="unsupportedReasonText"
            show-icon
            style="margin-bottom: 16px"
          />
          <el-alert
            v-else
            type="info"
            :closable="false"
            title="选择本地受试者根目录后，页面保持打开期间会按设定间隔自动扫描新增子文件夹与文件并上传到后端（前后端可跨设备部署）。子文件夹名作为伪ID，自动解析 userInfo.json。首次发现的文件只登记观察、不上传；下一轮扫描读到相同的大小与修改时间（即写入已结束）才上传，因此新文件最迟在一次扫描间隔后入库。"
            show-icon
            style="margin-bottom: 16px"
          />
          <el-descriptions :column="3" border size="small" style="margin-bottom: 16px">
            <el-descriptions-item label="监控目录">
              {{ browserScan.dirName || '未选择' }}
            </el-descriptions-item>
            <el-descriptions-item label="监控状态">
              <el-tag :type="browserScan.running ? 'success' : 'info'" size="small">
                {{ browserScan.running ? '监控中' : '已停止' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="上次扫描">
              {{ browserScan.lastScanAt || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="累计新增受试者">
              {{ browserScan.totalNewSubjects }}
            </el-descriptions-item>
            <el-descriptions-item label="累计上传文件">
              {{ browserScan.totalUploaded }}
            </el-descriptions-item>
            <el-descriptions-item label="待观察文件">
              <el-tooltip content="首次发现的文件只登记不导入；下一轮扫描读到相同的大小与修改时间才上传，避免录制中/拷贝中的半成品入库" placement="top">
                <span>{{ browserScan.observingCount || 0 }}</span>
              </el-tooltip>
            </el-descriptions-item>
            <el-descriptions-item label="扫描间隔">
              <el-input-number v-model="browserScan.intervalSec" :min="1" :max="3600" :step="1" size="small" style="width: 120px" :disabled="browserScan.running" />
              <span style="margin-left: 6px; color: #909399">秒</span>
            </el-descriptions-item>
            <el-descriptions-item label="采集批次">
              <el-autocomplete
                v-model="browserScan.collectionBatch"
                :fetch-suggestions="((q, cb) => _fetchFieldSuggestions('collection_batch', q, cb))"
                placeholder="如 BATCH_001（可选）"
                size="small"
                style="width: 160px"
                :disabled="browserScan.running"
                clearable
              />
            </el-descriptions-item>
            <el-descriptions-item label="采集场景">
              <el-autocomplete
                v-model="browserScan.collectionScene"
                :fetch-suggestions="((q, cb) => _fetchFieldSuggestions('collection_scene', q, cb))"
                placeholder="如 SCENE_A（可选）"
                size="small"
                style="width: 160px"
                :disabled="browserScan.running"
                clearable
              />
            </el-descriptions-item>
          </el-descriptions>
          <div style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <el-button type="primary" :icon="FolderOpened" :disabled="!browserScan.supported" @click="pickBrowserDir">
              {{ browserScan.dirName ? '更换目录' : '选择监控目录' }}
            </el-button>
            <el-button
              v-if="!browserScan.running"
              type="success"
              :icon="VideoPlay"
              :disabled="!browserScan.handle"
              @click="startBrowserWatch"
            >开始监控</el-button>
            <el-button
              v-else
              type="danger"
              :icon="VideoPause"
              @click="stopBrowserWatch"
            >停止监控</el-button>
            <el-button
              v-if="browserScan.pendingRestore"
              type="warning"
              :icon="RefreshRight"
              @click="restoreBrowserWatch"
            >恢复监控</el-button>
            <el-button :icon="Refresh" :disabled="!browserScan.handle || browserScan.scanning" :loading="browserScan.scanning" @click="scanBrowserOnce">
              立即扫描一次
            </el-button>
            <el-button v-if="browserScan.handle" link type="info" @click="clearBrowserDir">移除目录</el-button>
          </div>
          <el-card v-if="browserScan.scanning || browserScan.progress.currentFile" shadow="never" style="margin-bottom: 12px">
            <div style="display: flex; align-items: center; gap: 12px">
              <el-progress
                :percentage="browserScan.progress.total ? Math.round(browserScan.progress.current / browserScan.progress.total * 100) : (browserScan.progress.phase === 'scan' ? 0 : 100)"
                :status="browserScan.progress.phase === 'done' ? 'success' : ''"
                style="flex: 1"
              />
              <span style="color: #606266; font-size: 13px; white-space: nowrap; max-width: 360px; overflow: hidden; text-overflow: ellipsis">
                {{ browserScan.progress.phase === 'scan' ? '扫描目录…' : (browserScan.progress.currentFile || '处理中') }}
              </span>
            </div>
          </el-card>
          <el-table v-if="browserScan.failures.length" :data="browserScan.failures" border size="small" max-height="200" style="margin-bottom: 12px">
            <el-table-column prop="name" label="失败文件" min-width="220" show-overflow-tooltip />
            <el-table-column prop="reason" label="失败原因" min-width="200" show-overflow-tooltip />
          </el-table>
          <template v-if="!browserScan.supported">
            <el-divider content-position="left">手动一次性扫描</el-divider>
            <div style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
              <el-button type="primary" :icon="FolderOpened" @click="manualFolderInputRef?.click()">选择文件夹扫描</el-button>
              <span v-if="manualScanFiles.length" style="color: #67c23a">已识别 {{ manualScanFiles.length }} 个文件</span>
              <input ref="manualFolderInputRef" type="file" webkitdirectory directory multiple style="display:none" @change="onManualFolderChange" />
            </div>
            <el-button v-if="manualScanFiles.length" type="success" :loading="manualScanRunning" @click="runManualScanOnce">开始导入</el-button>
          </template>
        </el-tab-pane>
        <el-tab-pane label="服务端目录扫描" name="server">
      <el-alert
        type="info"
        :closable="false"
        title="配置监控文件夹后，系统会定期扫描子文件夹，每个子文件夹自动创建为新受试者（子文件夹名作为伪ID）。已存在的伪ID会自动跳过。"
        show-icon
        style="margin-bottom: 16px"
      />
      <div style="margin-bottom: 12px">
        <el-button type="primary" :icon="Plus" @click="openScanCreate">新增扫描配置</el-button>
        <el-button :icon="Refresh" @click="loadScanConfigs">刷新</el-button>
      </div>
      <el-table :data="scanConfigList" border stripe v-loading="scanConfigLoading">
        <el-table-column prop="name" label="配置名称" min-width="120" show-overflow-tooltip />
        <el-table-column prop="watch_dir" label="监控路径" min-width="180" show-overflow-tooltip />
        <el-table-column label="间隔" width="100">
          <template #default="{ row }">
            {{ row.interval_minutes }} 秒
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active"
              :loading="row._toggling"
              @change="(val) => toggleScanActive(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="last_scan_at" label="上次扫描" width="160" />
        <el-table-column prop="last_scan_count" label="新增数" width="70" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="handleRunScan(row)">立即扫描</el-button>
            <el-button size="small" link type="warning" @click="openScanEdit(row)">编辑</el-button>
            <el-button size="small" link type="danger" @click="handleDeleteScanConfig(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
        </el-tab-pane>
      </el-tabs>

      <!-- 扫描配置新增/编辑子弹窗 -->
      <el-dialog v-model="scanEditDialog" :title="editingScanId ? '编辑扫描配置' : '新增扫描配置'" width="560px" append-to-body>
        <el-form :model="scanForm" label-width="110px">
          <el-form-item label="配置名称" required>
            <el-input v-model="scanForm.name" placeholder="如 EEG采集批次A" />
          </el-form-item>
          <el-form-item label="监控文件夹" required>
            <el-input
              v-model="scanForm.watch_dir"
              placeholder="首次可粘贴完整路径（如 D:/data/受试者A），系统会自动记忆前缀；之后点击右侧按钮选文件夹即可自动拼接"
              @blur="onWatchDirBlur"
            >
              <template #append>
                <el-button :icon="FolderOpened" @click="scanFolderInputRef?.click()">选择</el-button>
              </template>
            </el-input>
            <input
              ref="scanFolderInputRef"
              type="file"
              webkitdirectory
              directory
              multiple
              style="display: none"
              @change="handleScanFolderChange"
            />
          </el-form-item>
          <el-form-item label="已记忆前缀">
            <el-input v-model="scanForm.path_prefix" placeholder="未记忆（粘贴完整路径后会自动提取并保存）" readonly>
              <template #append>
                <el-tooltip content="前缀从你输入的完整路径自动提取，保存在本地，下次选文件夹时自动复用" placement="top">
                  <el-icon><InfoFilled /></el-icon>
                </el-tooltip>
              </template>
            </el-input>
          </el-form-item>
          <el-form-item label="扫描间隔">
            <el-input-number v-model="scanForm.interval_minutes" :min="10" :max="86400" :step="10" style="width: 200px" />
            <span style="margin-left: 8px; color: #909399">秒（最小 10 秒，最大 86400 秒=1天）</span>
          </el-form-item>
          <el-form-item label="默认采集批次">
            <el-autocomplete
              v-model="scanForm.collection_batch"
              :fetch-suggestions="((q, cb) => _fetchFieldSuggestions('collection_batch', q, cb))"
              placeholder="扫描创建受试者时填入此批次（可选/可输入新值）"
              clearable
              style="width: 100%"
              @select="() => {}"
            />
          </el-form-item>
          <el-form-item label="默认采集场景">
            <el-autocomplete
              v-model="scanForm.collection_scene"
              :fetch-suggestions="((q, cb) => _fetchFieldSuggestions('collection_scene', q, cb))"
              placeholder="扫描创建受试者时填入此场景（可选/可输入新值）"
              clearable
              style="width: 100%"
              @select="() => {}"
            />
          </el-form-item>
          <el-form-item label="自动上传文件">
            <el-radio-group v-model="scanForm.auto_upload_files">
              <el-radio-button :value="true">开启</el-radio-button>
              <el-radio-button :value="false">关闭</el-radio-button>
            </el-radio-group>
            <span style="margin-left: 8px; color: #909399">开启后，子文件夹内的文件自动导入为数据资产</span>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="scanEditDialog = false">取消</el-button>
          <el-button type="primary" :loading="scanSaving" @click="handleSaveScanConfig">保存</el-button>
        </template>
      </el-dialog>
      </div>
    </el-dialog>

    <!-- 视频采集 -->
    <VideoCaptureDialog
      v-model="videoCaptureDialog"
      :subject="videoCaptureSubject"
      @success="onVideoCaptureSuccess"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import VideoCaptureDialog from '@/components/VideoCaptureDialog.vue'
import * as echarts from 'echarts'
import { Plus, Upload, Search, Refresh, UploadFilled, FolderOpened, RefreshLeft, RefreshRight, Document, Delete, InfoFilled, Check, CircleClose, VideoPlay, VideoPause } from '@element-plus/icons-vue'
import { getSubjectsApi, getSubjectBatchesApi, getSubjectBatchesAndScenesApi, createSubjectApi, updateSubjectApi, deleteSubjectApi, getAssetsApi, createAssetApi, updateAssetApi, deleteAssetApi, batchCreateAssetsApi, uploadAssetApi, getOperationLogsApi, getDataStatsApi, parseUserInfoApi } from '@/api/data'
import { supportsFsAccess, getUnsupportedReason } from '@/utils/dirWatcher'
import { runScanFromFiles, loadSubjectCache, loadUploadedMap, saveUploadedMap } from '@/utils/browserScan'
import { getSubjectTemplateApi, getScanConfigsApi, createScanConfigApi, updateScanConfigApi, deleteScanConfigApi, runScanNowApi } from '@/api/system'
import { useUserStore } from '@/stores/user'
import { useBrowserScanStore } from '@/stores/browserScan'
import VersionHistoryDialog from '@/components/VersionHistoryDialog.vue'
import GlobalVersionDialog from '@/components/GlobalVersionDialog.vue'
import { useRouter } from 'vue-router'

const userStore = useUserStore()
const canEdit = computed(() => ['admin', 'nurse', 'engineer'].includes(userStore.role))
const canDelete = computed(() => userStore.role === 'admin')

const router = useRouter()
const globalVersionVisible = ref(false)

// 历史版本
const historyVisible = ref(false)
const historyModel = reactive({ modelType: '', modelId: null, modelLabel: '', modelKey: '' })
const openHistory = (row, modelType, label) => {
  historyModel.modelType = modelType
  historyModel.modelId = row.id
  historyModel.modelLabel = label
  // 业务唯一标识：subject 用 pseudo_id，data_asset 用 file_name（避免 id 复用导致脏历史）
  historyModel.modelKey = modelType === 'subject' ? (row.pseudo_id || '')
    : modelType === 'data_asset' ? (row.file_name || '')
    : ''
  historyVisible.value = true
}
const onHistoryRollback = () => {
  if (historyModel.modelType === 'subject') {
    loadSubjects()
  } else if (historyModel.modelType === 'data_asset') {
    loadAssetsByType()
  }
}

const subjectList = ref([])
const layerStat = reactive({ raw: 0, cleaned: 0, feature: 0, annotation: 0 })
const saving = ref(false)
const tableLoading = ref(false)

// ===== 受试者批量选择（跨页选中） =====
const subjectTableRef = ref()
const selectedSubjects = ref([])
const selectAllSubjectsLoading = ref(false)
// 防止 _syncSubjectTableSelection 触发的 selection-change 反向覆盖
let _suppressSubjectSelectionChange = false

const onSubjectSelectionChange = (rows) => {
  if (_suppressSubjectSelectionChange) return
  const currentPageIds = new Set(subjectList.value.map((r) => r.id))
  const selectedInPage = new Set(rows.map((r) => r.id))
  const kept = selectedSubjects.value.filter((r) => !currentPageIds.has(r.id))
  const pageRows = rows.map((r) => ({ ...r, _placeholder: false }))
  const seen = new Set(kept.map((r) => r.id))
  const merged = [...kept]
  for (const r of pageRows) {
    if (!seen.has(r.id)) {
      seen.add(r.id)
      merged.push(r)
    }
  }
  selectedSubjects.value = merged
}

const _syncSubjectTableSelection = () => {
  if (!subjectTableRef.value) return
  _suppressSubjectSelectionChange = true
  try {
    const selectedIds = new Set(selectedSubjects.value.map((r) => r.id))
    subjectList.value.forEach((row) => {
      subjectTableRef.value.toggleRowSelection(row, selectedIds.has(row.id))
    })
  } finally {
    _suppressSubjectSelectionChange = false
  }
}

const selectAllSubjectsAcrossPages = async () => {
  selectAllSubjectsLoading.value = true
  try {
    const res = await getSubjectsApi({
      page: 1,
      page_size: 1,
      keyword: filters.keyword,
      gender: filters.gender,
      cognitive_risk_level: filters.riskLevel,
      collection_batch: filters.batch,
      has_video: filters.hasVideo,
      ids_only: true,
    })
    const allIds = (res.data?.items || []).map((x) => x.id)
    if (!allIds.length) {
      ElMessage.warning('当前筛选条件下无可选数据')
      return
    }
    const allIdSet = new Set(allIds)
    const existingNotInFilter = selectedSubjects.value.filter((r) => !allIdSet.has(r.id))
    const newSelected = allIds.map((id) => {
      const existing = selectedSubjects.value.find((r) => r.id === id)
      return existing || { id, _placeholder: true }
    })
    selectedSubjects.value = [...existingNotInFilter, ...newSelected]
    ElMessage.success(`已跨页全选 ${allIds.length} 个受试者`)
    nextTick(() => _syncSubjectTableSelection())
  } catch (e) {
    /* 接口失败时静默 */
  } finally {
    selectAllSubjectsLoading.value = false
  }
}

const clearAllSubjectSelection = () => {
  selectedSubjects.value = []
  subjectTableRef.value?.clearSelection()
}

const batchRemoveSubjects = () => {
  if (!selectedSubjects.value.length) return
  ElMessageBox.confirm(
    `确认删除选中的 ${selectedSubjects.value.length} 个受试者？该操作将级联删除其所有数据资产与磁盘文件，不可恢复。`,
    '批量删除',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      const results = await Promise.allSettled(selectedSubjects.value.map((s) => deleteSubjectApi(s.id)))
      const successCount = results.filter(r => r.status === 'fulfilled').length
      const failCount = results.length - successCount
      if (failCount === 0) {
        ElMessage.success(`已删除 ${successCount} 个受试者`)
      } else {
        ElMessage.warning(`成功删除 ${successCount} 个，失败 ${failCount} 个（失败可能因权限或文件占用）`)
      }
      if (successCount > 0) {
        subjectTableRef.value?.clearSelection()
        selectedSubjects.value = []
        loadSubjects()
        loadLayerStat()
      }
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}

// ===== 数据动态可视化 =====
const dataStatsLoading = ref(false)
const dataStatsDays = ref(30)
const dataStats = ref({
  subject_total: 0,
  asset_total: 0,
  today_subjects: 0,
  today_assets: 0,
  last_n_days: [],
  type_distribution: [],
  layer_distribution: [],
  risk_distribution: [],
  gender_distribution: [],
  days: 30,
})

const recentDaysSubjects = computed(() =>
  (dataStats.value.last_n_days || []).reduce((s, d) => s + (d.subject_count || 0), 0)
)
const recentDaysAssets = computed(() =>
  (dataStats.value.last_n_days || []).reduce((s, d) => s + (d.asset_count || 0), 0)
)
const dataStatCards = computed(() => [
  { title: '受试者总数', value: dataStats.value.subject_total, icon: 'User', color: '#409eff' },
  { title: '数据资产总数', value: dataStats.value.asset_total, icon: 'Files', color: '#67c23a' },
  {
    title: '今日新增（受试者/资产）',
    value: `${dataStats.value.today_subjects} / ${dataStats.value.today_assets}`,
    icon: 'Calendar',
    color: '#e6a23c',
  },
  {
    title: `${dataStats.value.days} 天新增（受试者/资产）`,
    value: `${recentDaysSubjects.value} / ${recentDaysAssets.value}`,
    icon: 'TrendCharts',
    color: '#9254de',
  },
])

// 模态/风险/性别 文本映射
const dataTypeTextMap = {
  video: '视频', audio: '音频', eeg: '脑电', ecg: '心电',
  eye: '眼动', gait: '步态', scale: '量表', task: '任务',
  json: '辅助数据', unknown: '未知',
}
const riskTextMap = { normal: '正常', mci: '轻度认知障碍', dementia: '痴呆', none: '未评估', unknown: '未知', '无': '无', '轻度': '轻度', '中度': '中度', '重度': '重度' }
const genderTextMap = { '男': '男', '女': '女', unknown: '未知' }

// 视图切换：subject 按受试者 / type 按数据类型
const viewMode = ref('subject')

// 筛选与分页
const filters = reactive({ keyword: '', gender: '', riskLevel: '', batch: '', hasVideo: '' })
const pagination = reactive({ page: 1, page_size: 20, total: 0 })
const batchOptions = ref([])

const loadBatchOptions = async () => {
  try {
    const res = await getSubjectBatchesApi()
    batchOptions.value = res.data || []
  } catch {
    batchOptions.value = []
  }
  // 批次+场景列表由 _loadBatchAndSceneOptions 统一加载到 _batchAndSceneOptions
  // （浏览器扫描和服务端扫描共用同一数据源）
}

const onSearch = () => {
  pagination.page = 1
  loadSubjects()
}
const onResetFilters = () => {
  filters.keyword = ''
  filters.gender = ''
  filters.riskLevel = ''
  filters.batch = ''
  filters.hasVideo = ''
  pagination.page = 1
  loadSubjects()
}

// 风险分级展示
const riskText = (v) => ({ normal: '正常', mci: '轻度认知障碍', dementia: '痴呆', '无': '无', '轻度': '轻度', '中度': '中度', '重度': '重度' }[v] || '未评估')
const riskTagType = (v) => ({ normal: 'success', mci: 'warning', dementia: 'danger', '无': 'info', '轻度': 'warning', '中度': 'danger', '重度': 'danger' }[v] || 'info')

// 功能卡片：脱敏配置仅 admin 可见，其他功能卡片对所有有权限用户可见
const allFeatures = [
  { key: 'access', title: '数据接入', desc: '单文件/分片上传/批量文件夹/断点续传/实时流', icon: 'Upload', color: '#409eff' },
  { key: 'version', title: '版本管理', desc: '全数据全版本、对比、回滚、导出', icon: 'CopyDocument', color: '#67c23a' },
  { key: 'desensitize', title: '脱敏处理', desc: '规则配置、动态脱敏、效果预览', icon: 'Lock', color: '#e6a23c' },
  { key: 'label', title: '标签管理', desc: '标签体系、生命周期、检索统计', icon: 'PriceTag', color: '#f56c6c' },
]
const features = computed(() => {
  if (userStore.role === 'admin') return allFeatures
  // 非 admin 隐藏脱敏配置（仅查看权限无意义，配置入口在系统管理页）
  return allFeatures.filter((f) => f.key !== 'desensitize')
})
const handleFeature = (key) => {
  if (key === 'access') {
    openAccessDialog()
  } else if (key === 'desensitize') {
    router.push({ path: '/system', query: { tab: 'desensitize' } })
  } else if (key === 'version') {
    globalVersionVisible.value = true
  } else if (key === 'label') {
    router.push('/label')
  }
}

// 跳转到受试者的可视化页面
const goVisualization = (row) => {
  router.push({ path: '/visualization', query: { subject: row.id } })
}

// 视频采集
const videoCaptureDialog = ref(false)
const videoCaptureSubject = ref(null)
const openVideoCapture = (row) => {
  // 新规则：受试者需要 face/body/gait 三类视频，可分别采集或重采
  // 重采确认由 VideoCaptureDialog 内部按单类型提示，这里无需全量确认
  videoCaptureSubject.value = row
  videoCaptureDialog.value = true
}
const onVideoCaptureSuccess = () => {
  // 上传成功后刷新受试者列表（更新"未采集"标签）与资产统计
  loadSubjects()
  loadLayerStat()
}

// 受试者弹窗（新增/编辑共用）
const subjectDialog = ref(false)
const subjectFormRef = ref()
const editingId = ref(null)
const subjectForm = reactive({
  pseudo_id: '', real_name: '', age: 60, gender: '男', education_level: '',
  phone: '', id_card: '',
  cognitive_risk_level: '', collection_batch: '', collection_scene: '', remark: '',
})

// 受试者模板字段（来自系统设置的受试者模板配置，决定表单渲染哪些字段及布局）
const subjectTplFields = ref([])
const loadSubjectTemplate = async () => {
  try {
    const res = await getSubjectTemplateApi()
    const data = res.data?.data || res.data || {}
    const fields = (data.fields || []).filter(f => f.enabled !== false && f.field_key)
    // 默认宽度映射（与历史硬编码表单一致），未存 span 时按此兜底
    const defaultSpan = {
      pseudo_id: 24, real_name: 12, age: 12, gender: 12, education_level: 24,
      phone: 12, id_card: 12, cognitive_risk_level: 24,
      emotion_status: 24, moca_score: 12, mmse_score: 12, ad8_score: 12,
      collection_batch: 12, collection_scene: 12, remark: 24,
    }
    subjectTplFields.value = fields.map(f => {
      // 兼容旧数据库模板：批次/场景字段强制使用 autocomplete（可输入+下拉选择）
      // 即使后端模板记录仍是 input，前端也按 autocomplete 渲染
      let field_type = f.field_type
      if (f.field_key === 'collection_batch' || f.field_key === 'collection_scene') {
        field_type = 'autocomplete'
      }
      return {
        ...f,
        field_type,
        span: f.span || defaultSpan[f.field_key] || 24,
        options: f.options || [],
      }
    })
    // 保险：如果模板里没有批次/场景字段（被管理员删除），强制注入默认字段
    const existingKeys = subjectTplFields.value.map(f => f.field_key)
    // 姓名（真实姓名）字段：若模板缺失则强制注入到伪ID之后；非 admin 由后端脱敏显示
    if (!existingKeys.includes('real_name')) {
      subjectTplFields.value.splice(1, 0, {
        field_key: 'real_name', field_label: '姓名',
        field_type: 'input', required: false, span: 12,
        placeholder: '受试者真实姓名（非管理员脱敏显示）', options: [],
      })
      existingKeys.push('real_name')
    }
    if (!existingKeys.includes('collection_batch')) {
      subjectTplFields.value.push({
        field_key: 'collection_batch', field_label: '采集批次',
        field_type: 'autocomplete', required: false, span: 12,
        placeholder: '如 BATCH_001（可选/可输入新值）', options: [],
      })
    }
    if (!existingKeys.includes('collection_scene')) {
      subjectTplFields.value.push({
        field_key: 'collection_scene', field_label: '场景代码',
        field_type: 'autocomplete', required: false, span: 12,
        placeholder: '如 SCENE_A（可选/可输入新值）', options: [],
      })
    }
    // 加载已有批次/场景列表，填充到 autocomplete 字段的 options（最新优先）
    _loadBatchAndSceneOptions(subjectTplFields.value)
  } catch (e) {
    // 接口未就绪时使用默认字段
    subjectTplFields.value = [
      { field_key: 'pseudo_id', field_label: '受试者伪ID', field_type: 'input', required: true, span: 24, placeholder: '如 SUBJ_001', options: [] },
      { field_key: 'real_name', field_label: '姓名', field_type: 'input', required: false, span: 12, placeholder: '受试者真实姓名（非管理员脱敏显示）', options: [] },
      { field_key: 'age', field_label: '年龄', field_type: 'number', required: true, span: 12, placeholder: '0-120', options: [] },
      { field_key: 'gender', field_label: '性别', field_type: 'select', required: true, span: 12, placeholder: '', options: [{ label: '男', value: '男' }, { label: '女', value: '女' }] },
      { field_key: 'education_level', field_label: '教育程度', field_type: 'input', required: false, span: 24, placeholder: '如 高中/本科', options: [] },
      { field_key: 'phone', field_label: '联系电话', field_type: 'input', required: false, span: 12, placeholder: '如 13800138000', options: [] },
      { field_key: 'id_card', field_label: '身份证号', field_type: 'input', required: false, span: 12, placeholder: '如 110101199001011234', options: [] },
      { field_key: 'cognitive_risk_level', field_label: '认知风险分级', field_type: 'select', required: false, span: 24, placeholder: '', options: [{ label: '无', value: '无' }, { label: '轻度', value: '轻度' }, { label: '中度', value: '中度' }, { label: '重度', value: '重度' }, { label: '正常', value: 'normal' }, { label: '轻度认知障碍', value: 'mci' }, { label: '痴呆', value: 'dementia' }] },
      { field_key: 'emotion_status', field_label: '情绪状态', field_type: 'input', required: false, span: 24, placeholder: '如 焦虑/抑郁', options: [] },
      { field_key: 'moca_score', field_label: 'MoCA 得分', field_type: 'number', required: false, span: 12, placeholder: '0-30', options: [], max: 30 },
      { field_key: 'mmse_score', field_label: 'MMSE 得分', field_type: 'number', required: false, span: 12, placeholder: '0-30', options: [], max: 30 },
      { field_key: 'ad8_score', field_label: 'AD8 得分', field_type: 'number', required: false, span: 12, placeholder: '0-8', options: [], max: 8 },
      { field_key: 'collection_batch', field_label: '采集批次', field_type: 'autocomplete', required: false, span: 12, placeholder: '如 BATCH_001（可选/可输入新值）', options: [] },
      { field_key: 'collection_scene', field_label: '场景代码', field_type: 'autocomplete', required: false, span: 12, placeholder: '如 SCENE_A（可选/可输入新值）', options: [] },
      { field_key: 'remark', field_label: '备注', field_type: 'textarea', required: false, span: 24, placeholder: '其他说明', options: [] },
    ]
    // 加载已有批次/场景列表，填充到 autocomplete 字段的 options（最新优先）
    _loadBatchAndSceneOptions(subjectTplFields.value)
  }
}

// 加载已有批次/场景列表（最新优先），填充到 subjectTplFields 与 scanForm 中的下拉选项
const _batchAndSceneOptions = reactive({ batches: [], scenes: [] })
const _loadBatchAndSceneOptions = async (targetFields = null) => {
  try {
    const res = await getSubjectBatchesAndScenesApi()
    const data = res.data?.data || res.data || {}
    _batchAndSceneOptions.batches = data.batches || []
    _batchAndSceneOptions.scenes = data.scenes || []
    // 填充到传入的字段列表（受试者表单）
    if (targetFields && Array.isArray(targetFields)) {
      targetFields.forEach(f => {
        if (f.field_key === 'collection_batch') f.options = _batchAndSceneOptions.batches
        if (f.field_key === 'collection_scene') f.options = _batchAndSceneOptions.scenes
      })
    }
  } catch (e) {
    // 接口未就绪时静默忽略，不影响表单使用（仍可手动输入）
  }
}

// el-autocomplete 的 fetch-suggestions 回调：根据 field_key 返回对应建议列表
// - 不输入时显示全部已有记录（最新优先）
// - 输入时按包含关系过滤
const _fetchFieldSuggestions = (fieldKey, query, cb) => {
  let source = []
  if (fieldKey === 'collection_batch') source = _batchAndSceneOptions.batches
  else if (fieldKey === 'collection_scene') source = _batchAndSceneOptions.scenes
  const q = (query || '').trim().toLowerCase()
  const results = q
    ? source.filter(v => String(v).toLowerCase().includes(q))
    : source
  // el-autocomplete 需要 { value } 格式
  cb(results.map(v => ({ value: v })))
}

// 数据接入弹窗
const accessDialog = ref(false)
const importMode = ref('single')
const assetForm = reactive({
  subject_id: null, data_type: 'video', file_name: '', sample_rate: null,
})
const uploadedFile = ref(null)
const folderInputRef = ref(null)
const singleUploadRef = ref(null)
const folderFiles = ref([])

// 新增受试者时的文件上传
const subjectImportMode = ref('none')
const subjectFileList = ref([])
const subjectFolderFiles = ref([])
// 上传进度
const batchUploadProgress = reactive({
  visible: false,
  current: 0,
  total: 0,
  successCount: 0,
  failCount: 0,
  failures: [], // [{name, reason}]
  currentFile: '',
})
const subjectFolderInputRef = ref(null)
// 检测浏览器是否支持 webkitdirectory
const supportsFolderSelect = ref(typeof document !== 'undefined' && 'webkitdirectory' in document.createElement('input'))
// 单文件上传大小上限（与后端 MAX_CONTENT_LENGTH 2GB 一致）
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

// 按后缀识别模态类型
const detectDataType = (ext, fileName = '') => {
  let realExt = (ext || '').toLowerCase().trim()
  let name = (fileName || '').toLowerCase()
  // 剥离外部加密后缀 .enc（如 xxx.wav.enc -> xxx.wav），与后端 scanner 逻辑一致
  if (realExt === 'enc' && name.endsWith('.enc')) {
    const stripped = name.slice(0, -4)
    const idx = stripped.lastIndexOf('.')
    realExt = idx > 0 ? stripped.slice(idx + 1) : ''
  }
  // userInfo 元数据文件 → json 类型资产（与后端 scanner 一致）
  if (name === 'userinfo.json' || name === 'userinfo.json.enc') return 'json'
  // 眼动评估数据 sync_data.json → eye（与后端 is_sync_data_file 一致，优先级最高）
  if (/_sync_data\.json$/.test(name)) return 'eye'
  // 量表数据（MoCA_用户ID_日期.json 等）→ scale
  if (/^(MoCA|MMSE|AD8)_.+_\d{8}\.json$/i.test(name)) return 'scale'
  // 音频格式（优先识别，确保 wav 等被正确捕获）
  if (['wav', 'mp3', 'm4a', 'flac', 'aac', 'ogg', 'wma', 'aiff', 'au', 'snd'].includes(realExt)) return 'audio'
  // 视频格式
  if (['mp4', 'avi', 'mov', 'mkv'].includes(realExt)) return 'video'
  if (['edf', 'bdf'].includes(realExt)) return 'eeg'
  if (['ecg', 'dat'].includes(realExt)) return 'ecg'
  if (['json'].includes(realExt)) return 'task'
  // CSV 按文件名关键词识别具体类型
  if (['csv'].includes(realExt)) {
    if (name.includes('eeg') || name.includes('brain') || name.includes('exg')) return 'eeg'
    if (name.includes('ecg') || name.includes('heart')) return 'ecg'
    if (name.includes('eye') || name.includes('gaze')) return 'eye'
    if (name.includes('gait') || name.includes('walk') || name.includes('pressure')) return 'gait'
    return 'scale'
  }
  // 按文件名关键词补充识别（无已知扩展名时）
  if (name.includes('audio') || name.includes('sound') || name.includes('voice') || name.includes('录音')) return 'audio'
  if (name.includes('eeg') || name.includes('brain')) return 'eeg'
  if (name.includes('ecg') || name.includes('heart')) return 'ecg'
  if (name.includes('eye') || name.includes('gaze')) return 'eye'
  if (name.includes('gait') || name.includes('walk')) return 'gait'
  return null
}
const typeText = (t) => ({ video: '视频', audio: '音频', eeg: '脑电', ecg: '心电', eye: '眼动', gait: '步态', scale: '量表', task: '任务', json: '辅助数据' }[t] || '未知')
const targetTypeText = (t) => ({
  subject: '受试者', asset: '数据资产', annotation_task: '标注任务',
  label: '标签', user: '用户', role_menu: '角色菜单',
  desensitize_config: '脱敏配置', snapshot: '数据快照', standard: '规范',
}[t] || t || '—')

// 资产查看弹窗
const assetListDialog = ref(false)
const currentSubject = ref(null)
const currentAssets = ref([])

const loadSubjects = async () => {
  tableLoading.value = true
  try {
    const res = await getSubjectsApi({
      page: pagination.page,
      page_size: pagination.page_size,
      keyword: filters.keyword,
      gender: filters.gender,
      cognitive_risk_level: filters.riskLevel,
      collection_batch: filters.batch,
      has_video: filters.hasVideo,
    })
    subjectList.value = res.data.items || []
    pagination.total = res.data.total || 0
    // 翻页后同步当前页的勾选状态到表格 DOM
    nextTick(() => _syncSubjectTableSelection())
  } catch (e) {
    subjectList.value = []
  } finally {
    tableLoading.value = false
  }
}

// ===== 图表实例 =====
const layerChartRef = ref()
const trendChartRef = ref()
const typeChartRef = ref()
const riskChartRef = ref()
const genderChartRef = ref()
let layerChart = null
let trendChart = null
let typeChart = null
let riskChart = null
let genderChart = null

const initCharts = () => {
  if (layerChartRef.value && !layerChart) layerChart = echarts.init(layerChartRef.value)
  if (trendChartRef.value && !trendChart) trendChart = echarts.init(trendChartRef.value)
  if (typeChartRef.value && !typeChart) typeChart = echarts.init(typeChartRef.value)
  if (riskChartRef.value && !riskChart) riskChart = echarts.init(riskChartRef.value)
  if (genderChartRef.value && !genderChart) genderChart = echarts.init(genderChartRef.value)
}

// 数据湖分层 - 横向条形图（数据源：dataStats.layer_distribution）
const updateLayerChart = () => {
  const layerMap = { raw: '原始数据', cleaned: '清洗数据', feature: '特征数据', annotation: '标注数据', unknown: '未知' }
  const colors = { raw: '#409eff', cleaned: '#67c23a', feature: '#e6a23c', annotation: '#f56c6c', unknown: '#909399' }
  const rows = (dataStats.value.layer_distribution || []).slice()
  // 确保四层都有展示
  const order = ['raw', 'cleaned', 'feature', 'annotation']
  order.forEach((k) => {
    if (!rows.find((r) => r.layer === k)) rows.push({ layer: k, count: 0 })
  })
  const names = rows.map((r) => layerMap[r.layer] || r.layer)
  const values = rows.map((r) => ({ value: r.count, key: r.layer }))
  layerChart?.setOption({
    tooltip: { trigger: 'axis', formatter: (p) => `${p[0].name}: ${p[0].value} 条` },
    grid: { left: 80, right: 60, top: 20, bottom: 30 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: names },
    series: [{
      type: 'bar',
      data: values.map((v) => ({
        value: v.value,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
            { offset: 0, color: colors[v.key] || '#909399' },
            { offset: 1, color: (colors[v.key] || '#909399') + '55' },
          ]),
          borderRadius: [0, 4, 4, 0],
        },
      })),
      barWidth: '50%',
      label: { show: true, position: 'right', formatter: '{c} 条' },
    }],
  })
}

// 接入趋势 - 双折线图（受试者 + 数据资产）
const updateTrendChart = () => {
  const trendData = dataStats.value.last_n_days || []
  trendChart?.setOption({
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    grid: { left: 50, right: 30, top: 20, bottom: 50 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: trendData.map((d) => d.date.slice(5)),
      axisLabel: { rotate: trendData.length > 12 ? 35 : 0, fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '受试者',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        data: trendData.map((d) => d.subject_count),
        lineStyle: { width: 2, color: '#409eff' },
        itemStyle: { color: '#409eff' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(64,158,255,0.3)' },
            { offset: 1, color: 'rgba(64,158,255,0.02)' },
          ]),
        },
      },
      {
        name: '数据资产',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        data: trendData.map((d) => d.asset_count),
        lineStyle: { width: 2, color: '#67c23a' },
        itemStyle: { color: '#67c23a' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(103,194,58,0.3)' },
            { offset: 1, color: 'rgba(103,194,58,0.02)' },
          ]),
        },
      },
    ],
  })
}

// 模态分布 - 玫瑰图
const updateTypeChart = () => {
  const typeColorMap = {
    video: '#409eff', audio: '#67c23a', eeg: '#e6a23c', ecg: '#f56c6c',
    eye: '#9254de', gait: '#13c2c2', scale: '#fa8c16', task: '#722ed1',
    json: '#909399', unknown: '#c0c4cc',
  }
  const data = (dataStats.value.type_distribution || []).map((d) => ({
    name: dataTypeTextMap[d.data_type] || d.data_type,
    value: d.count,
    itemStyle: { color: typeColorMap[d.data_type] || '#c0c4cc' },
  }))
  typeChart?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['25%', '70%'],
      roseType: 'radius',
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#ccc' } }],
    }],
  })
}

// 风险分级 - 柱状图
const updateRiskChart = () => {
  const riskColorMap = { normal: '#67c23a', mci: '#e6a23c', dementia: '#f56c6c', none: '#909399', unknown: '#c0c4cc', '无': '#909399', '轻度': '#e6a23c', '中度': '#f56c6c', '重度': '#f56c6c' }
  // 固定顺序展示
  const order = ['无', '轻度', '中度', '重度', 'normal', 'mci', 'dementia', 'none']
  const rows = (dataStats.value.risk_distribution || []).slice()
  order.forEach((k) => {
    if (!rows.find((r) => r.risk_level === k)) rows.push({ risk_level: k, count: 0 })
  })
  riskChart?.setOption({
    tooltip: { trigger: 'axis', formatter: (p) => `${p[0].name}: ${p[0].value} 人` },
    grid: { left: 40, right: 20, top: 20, bottom: 60 },
    xAxis: {
      type: 'category',
      data: rows.map((r) => riskTextMap[r.risk_level] || r.risk_level),
      axisLabel: { interval: 0, rotate: 20, fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: rows.map((r) => ({
        value: r.count,
        itemStyle: { color: riskColorMap[r.risk_level] || '#c0c4cc', borderRadius: [4, 4, 0, 0] },
      })),
      barWidth: '45%',
      label: { show: true, position: 'top', fontSize: 11 },
    }],
  })
}

// 性别分布 - 环形图
const updateGenderChart = () => {
  const genderColorMap = { '男': '#409eff', '女': '#f56c6c', unknown: '#c0c4cc' }
  const data = (dataStats.value.gender_distribution || []).map((d) => ({
    name: genderTextMap[d.gender] || d.gender || '未知',
    value: d.count,
    itemStyle: { color: genderColorMap[d.gender] || '#c0c4cc' },
  }))
  genderChart?.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} 人 ({d}%)' },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['45%', '70%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#ccc' } }],
    }],
  })
}

const updateAllCharts = () => {
  updateLayerChart()
  updateTrendChart()
  updateTypeChart()
  updateRiskChart()
  updateGenderChart()
}

// 加载数据动态统计（包含数据湖分层，替代原 loadLayerStat 的拉取逻辑）
// 静默重试最多2次（应对后端 DB 死锁等暂时性错误），仍失败才提示
const loadDataStats = async () => {
  dataStatsLoading.value = true
  const doFetch = () => getDataStatsApi({ days: dataStatsDays.value, _t: Date.now() })
  try {
    let res
    let lastErr
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        res = await doFetch()
        break
      } catch (e) {
        lastErr = e
        const status = e.response?.status
        // 仅对 500/网络错误重试；4xx 直接抛出
        if (status && status < 500) throw e
        if (attempt < 2) await new Promise((r) => setTimeout(r, 600 * (attempt + 1)))
      }
    }
    if (!res) throw lastErr
    dataStats.value = res.data
    // 同步更新 layerStat（保留兼容性）
    const ld = res.data.layer_distribution || []
    layerStat.raw = ld.find((x) => x.layer === 'raw')?.count || 0
    layerStat.cleaned = ld.find((x) => x.layer === 'cleaned')?.count || 0
    layerStat.feature = ld.find((x) => x.layer === 'feature')?.count || 0
    layerStat.annotation = ld.find((x) => x.layer === 'annotation')?.count || 0
  } catch (e) {
    ElMessage.warning('加载数据动态失败，请稍后刷新重试')
  } finally {
    dataStatsLoading.value = false
  }
  await nextTick()
  if (!layerChart) initCharts()
  updateAllCharts()
}

// loadLayerStat 作为 loadDataStats 的别名，保持现有调用点兼容
const loadLayerStat = loadDataStats

const handleLayerResize = () => {
  layerChart?.resize()
  trendChart?.resize()
  typeChart?.resize()
  riskChart?.resize()
  genderChart?.resize()
}

const resetForm = () => {
  // 按当前模板字段重置，避免新增字段在表单中无初始值
  subjectTplFields.value.forEach(f => {
    const k = f.field_key
    if (f.field_type === 'number') subjectForm[k] = null
    else subjectForm[k] = ''
  })
  // 年龄默认值兜底
  if (subjectTplFields.value.some(f => f.field_key === 'age')) subjectForm.age = 60
  // 性别默认值兜底
  if (subjectTplFields.value.some(f => f.field_key === 'gender') && !subjectForm.gender) subjectForm.gender = '男'
}

const openSubjectDialog = () => {
  editingId.value = null
  resetForm()
  subjectImportMode.value = 'none'
  subjectFileList.value = []
  subjectFolderFiles.value = []
  subjectDialog.value = true
}

// 切换导入模式时清理另一模式的残留文件
const onImportModeChange = (mode) => {
  if (mode !== 'files') subjectFileList.value = []
  if (mode !== 'folder') subjectFolderFiles.value = []
}

const openEditDialog = (row) => {
  editingId.value = row.id
  // 先重置所有模板字段为空
  subjectTplFields.value.forEach(f => { subjectForm[f.field_key] = row[f.field_key] ?? (f.field_type === 'number' ? null : '') })
  subjectDialog.value = true
}

// 新增受试者：文件选择处理
const handleSubjectFileChange = (file) => {
  // 提取扩展名：剥离外部加密后缀 .enc，取真实扩展名（如 xxx.wav.enc -> wav）
  let nameForExt = file.name
  if (nameForExt.toLowerCase().endsWith('.enc')) {
    nameForExt = nameForExt.slice(0, -4)
  }
  const lastDotIdx = nameForExt.lastIndexOf('.')
  const ext = lastDotIdx > 0 ? nameForExt.slice(lastDotIdx + 1) : ''
  const dataType = detectDataType(ext, file.name)
  if (!dataType) {
    ElMessage.warning(`文件 ${file.name} 无法识别类型，已跳过`)
    return
  }
  if (file.raw?.size > MAX_UPLOAD_BYTES) {
    ElMessage.warning(`文件 ${file.name} 超过 2GB 上限，已跳过`)
    return
  }
  subjectFileList.value.push({
    file: file.raw,
    file_name: file.name,
    file_format: ext,
    file_size: Math.round((file.size || 0) / 1024),
    data_type: dataType,
  })
}

const handleSubjectFolderChange = async (e) => {
  const files = Array.from(e.target.files || [])
  const unrecognized = []
  const recognized = []
  let userInfoFile = null
  let skippedMetaCount = 0
  let oversizedCount = 0
  // 从 webkitRelativePath 提取文件夹名作为伪ID（优先级最高）
  let folderName = ''
  if (files.length && files[0].webkitRelativePath) {
    folderName = files[0].webkitRelativePath.split('/')[0] || ''
  }
  if (folderName) {
    subjectForm.pseudo_id = folderName
  }

  files.forEach((f) => {
    const nameLower = f.name.toLowerCase()
    // 跳过密钥文件（不应入库）
    if (nameLower === '密钥.txt' || nameLower === 'key.txt') {
      skippedMetaCount++
      return
    }
    // 跳过眼动评估字段说明文件（仅供查阅，不导入）
    if (nameLower.endsWith('_sync_fields_zh.json')) {
      skippedMetaCount++
      return
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      oversizedCount++
      return
    }
    // 检测 userInfo 文件（明文或外部加密）：单独提取用于解析元数据，同时作为 json 资产入库
    if (nameLower === 'userinfo.json' || nameLower === 'userinfo.json.enc') {
      userInfoFile = f
      // 不 return：userInfo 也要作为 json 数据资产上传入库（继续走下方识别逻辑）
    }
    // 提取扩展名：剥离外部加密后缀 .enc，取真实扩展名（如 xxx.wav.enc -> wav）
    let nameForExt = f.name
    if (nameForExt.toLowerCase().endsWith('.enc')) {
      nameForExt = nameForExt.slice(0, -4)
    }
    const lastDotIdx = nameForExt.lastIndexOf('.')
    const ext = lastDotIdx > 0 ? nameForExt.slice(lastDotIdx + 1) : ''
    const dataType = detectDataType(ext, f.name)
    if (dataType) {
      recognized.push({
        file: f,
        file_name: f.name,
        file_path: f.webkitRelativePath || f.name,
        file_format: ext,
        file_size: Math.round((f.size || 0) / 1024),
        data_type: dataType,
      })
    } else if (ext) {
      // 有扩展名但无法识别，收集用于提示
      unrecognized.push({ name: f.name, ext })
    }
  })
  subjectFolderFiles.value = recognized

  // 如果检测到 userInfo 文件，调用后端 API 解析并自动填充表单
  if (userInfoFile) {
    try {
      const formData = new FormData()
      formData.append('file', userInfoFile)
      const res = await parseUserInfoApi(formData)
      const fields = res.data?.fields
      if (fields && Object.keys(fields).length) {
        Object.keys(fields).forEach((key) => {
          // pseudo_id 以文件夹名为准，不使用 userInfo 中的 id 覆盖
          if (key === 'pseudo_id') return
          if (fields[key] !== null && fields[key] !== undefined && fields[key] !== '') {
            subjectForm[key] = fields[key]
          }
        })
        ElMessage.success(`已从 ${userInfoFile.name} 自动读取受试者信息（${Object.keys(fields).length} 个字段）`)
      } else {
        ElMessage.info(`已读取 ${userInfoFile.name}，但未识别到可填充的字段`)
      }
    } catch (err) {
      const msg = err.response?.data?.message || err.message || '未知错误'
      ElMessage.warning(`userInfo.json 解析失败：${msg}`)
    }
  }

  const oversizeNote = oversizedCount ? `，跳过 ${oversizedCount} 个超过 2GB 的文件` : ''
  if (unrecognized.length) {
    const sample = unrecognized.slice(0, 3).map((u) => `${u.name}（.${u.ext}）`).join('、')
    const metaNote = skippedMetaCount ? `，跳过 ${skippedMetaCount} 个元数据/密钥文件` : ''
    const userInfoNote = userInfoFile ? '，已读取 userInfo' : ''
    ElMessage.warning(`已选 ${files.length} 个文件，其中 ${recognized.length} 个可识别将被导入${userInfoNote}${metaNote}${oversizeNote}；${unrecognized.length} 个无法识别类型：${sample}${unrecognized.length > 3 ? ' 等' : ''}`)
  } else if (files.length > recognized.length + skippedMetaCount + (userInfoFile ? 1 : 0)) {
    ElMessage.info(`已选 ${files.length} 个文件，其中 ${recognized.length} 个可识别类型将被导入${oversizeNote}`)
  } else if (userInfoFile) {
    ElMessage.success(`已选 ${files.length} 个文件，${recognized.length} 个数据文件将被导入，已自动读取 userInfo 填充表单${oversizeNote}`)
  }
}

const handleSaveSubject = async () => {
  // 校验必填字段（动态遍历模板字段）
  const missing = []
  subjectTplFields.value.forEach(f => {
    if (f.required) {
      const val = subjectForm[f.field_key]
      if (val === null || val === undefined || val === '') {
        missing.push(f.field_label)
      }
    }
  })
  if (missing.length) {
    ElMessage.warning(`请填写必填字段：${missing.join('、')}`)
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateSubjectApi(editingId.value, { ...subjectForm })
      ElMessage.success('受试者信息已更新')
    } else {
      let res
      try {
        res = await createSubjectApi({ ...subjectForm })
      } catch (err) {
        const msg = err.response?.data?.message || err.message || '创建失败'
        ElMessage.error(msg)
        return
      }
      const subjectId = res.data?.id
      if (!subjectId) {
        ElMessage.error('受试者创建失败：未返回受试者ID')
        return
      }
      // 如果选择了文件，上传到该受试者
      if (subjectImportMode.value === 'files' && subjectFileList.value.length) {
        await uploadSubjectFiles(subjectId, subjectFileList.value)
      } else if (subjectImportMode.value === 'folder' && subjectFolderFiles.value.length) {
        await uploadSubjectFiles(subjectId, subjectFolderFiles.value)
      }
    }
    subjectDialog.value = false
    loadSubjects()
    // 刷新数据动态 + 数据资产列表（按类型查看时），确保接入结果立即反映在界面上
    loadLayerStat()
    if (viewMode.value === 'type') loadAssetsByType()
  } finally {
    saving.value = false
  }
}

// 批量上传文件到指定受试者（带进度与失败详情）
const uploadSubjectFiles = async (subjectId, files) => {
  batchUploadProgress.visible = true
  batchUploadProgress.current = 0
  batchUploadProgress.total = files.length
  batchUploadProgress.successCount = 0
  batchUploadProgress.failCount = 0
  batchUploadProgress.failures = []
  batchUploadProgress.currentFile = ''
  for (const f of files) {
    batchUploadProgress.currentFile = f.file_name
    try {
      const fd = new FormData()
      fd.append('file', f.file)
      fd.append('subject_id', subjectId)
      fd.append('data_type', f.data_type)
      fd.append('layer', 'raw')
      await uploadAssetApi(fd)
      batchUploadProgress.successCount++
    } catch (e) {
      batchUploadProgress.failCount++
      const reason = e.response?.data?.message || e.message || '上传失败'
      batchUploadProgress.failures.push({ name: f.file_name, reason })
    }
    batchUploadProgress.current++
  }
  batchUploadProgress.currentFile = ''
  // 汇总提示
  if (batchUploadProgress.failCount === 0) {
    ElMessage.success(`全部 ${batchUploadProgress.successCount} 个文件已上传`)
  } else {
    ElMessage.warning(`成功 ${batchUploadProgress.successCount} 个，失败 ${batchUploadProgress.failCount} 个`)
  }
}

const removeSubject = (row) => {
  ElMessageBox.confirm(
    `确认删除受试者「${row.pseudo_id}」？该操作将级联删除其所有数据资产与磁盘文件，不可恢复。`,
    '危险操作',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      await deleteSubjectApi(row.id)
      ElMessage.success('受试者已删除')
      // 从已选列表中移除（避免残留 ID）
      selectedSubjects.value = selectedSubjects.value.filter((s) => s.id !== row.id)
      loadSubjects()
      loadLayerStat()
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}

const openAccessDialog = () => {
  if (!subjectList.value.length) {
    ElMessage.warning('请先新增受试者')
    return
  }
  assetForm.subject_id = subjectList.value[0].id
  assetForm.data_type = 'video'
  assetForm.file_name = ''
  assetForm.sample_rate = null
  uploadedFile.value = null
  importMode.value = 'single'
  folderFiles.value = []
  uploadProgress.value = 0
  // 清空上传组件的内部文件列表
  nextTick(() => {
    singleUploadRef.value?.clearFiles()
    if (folderInputRef.value) folderInputRef.value.value = ''
  })
  accessDialog.value = true
}

const handleFileChange = (file) => {
  if (file.raw?.size > MAX_UPLOAD_BYTES) {
    ElMessage.warning('文件超过 2GB 上限，请选择较小的文件')
    return
  }
  uploadedFile.value = file
  if (!assetForm.file_name) assetForm.file_name = file.name
  // 眼动评估数据 sync_data.json 自动识别为 eye 类型
  const nameLower = (file.name || '').toLowerCase()
  if (/_sync_data\.json$/.test(nameLower)) {
    assetForm.data_type = 'eye'
  }
  // 量表数据自动识别为 scale 类型
  if (/^(moca|mmse|ad8)_.+_\d{8}\.json$/.test(nameLower)) {
    assetForm.data_type = 'scale'
  }
}

const handleExceed = () => {
  ElMessage.warning('仅支持单个文件')
}

const handleFolderChange = (e) => {
  const files = Array.from(e.target.files || [])
  const unrecognized = []
  const recognized = []
  let skippedMetaCount = 0
  let oversizedCount = 0
  files.forEach((f) => {
    const nameLower = f.name.toLowerCase()
    // 跳过密钥文件与眼动字段说明文件（不入库）；userInfo 需作为 json 数据资产入库
    if (nameLower === '密钥.txt' || nameLower === 'key.txt'
        || nameLower.endsWith('_sync_fields_zh.json')) {
      skippedMetaCount++
      return
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      oversizedCount++
      return
    }
    // 提取扩展名：剥离外部加密后缀 .enc，取真实扩展名（如 xxx.wav.enc -> wav）
    let nameForExt = f.name
    if (nameForExt.toLowerCase().endsWith('.enc')) {
      nameForExt = nameForExt.slice(0, -4)
    }
    const lastDotIdx = nameForExt.lastIndexOf('.')
    const ext = lastDotIdx > 0 ? nameForExt.slice(lastDotIdx + 1) : ''
    const dataType = detectDataType(ext, f.name)
    if (dataType) {
      recognized.push({
        file: f,
        file_name: f.name,
        file_path: f.webkitRelativePath || f.name,
        file_format: ext,
        file_size: Math.round((f.size || 0) / 1024),
        data_type: dataType,
      })
    } else if (ext) {
      unrecognized.push({ name: f.name, ext })
    }
  })
  folderFiles.value = recognized // 仅导入可识别类型的文件
  const oversizeNote = oversizedCount ? `，跳过 ${oversizedCount} 个超过 2GB 的文件` : ''
  if (unrecognized.length) {
    const sample = unrecognized.slice(0, 3).map((u) => `${u.name}（.${u.ext}）`).join('、')
    const metaNote = skippedMetaCount ? `，跳过 ${skippedMetaCount} 个元数据/密钥文件` : ''
    ElMessage.warning(`已选 ${files.length} 个文件，其中 ${recognized.length} 个可识别将被导入${metaNote}${oversizeNote}；${unrecognized.length} 个无法识别类型：${sample}${unrecognized.length > 3 ? ' 等' : ''}`)
  } else if (files.length > recognized.length + skippedMetaCount) {
    ElMessage.info(`已选 ${files.length} 个文件，其中 ${recognized.length} 个可识别类型将被导入${oversizeNote}`)
  } else if (skippedMetaCount) {
    ElMessage.success(`已选 ${files.length} 个文件，${recognized.length} 个数据文件将被导入，跳过 ${skippedMetaCount} 个元数据/密钥文件${oversizeNote}`)
  }
}

const uploadProgress = ref(0)

const handleCreateAsset = async () => {
  if (!assetForm.subject_id) {
    ElMessage.warning('请选择受试者')
    return
  }
  saving.value = true
  try {
    if (importMode.value === 'single') {
      if (!assetForm.data_type) {
        ElMessage.warning('请选择数据类型')
        saving.value = false
        return
      }
      if (!uploadedFile.value?.raw) {
        ElMessage.warning('请选择要上传的文件')
        saving.value = false
        return
      }
      const fd = new FormData()
      fd.append('file', uploadedFile.value.raw)
      fd.append('subject_id', assetForm.subject_id)
      fd.append('data_type', assetForm.data_type)
      fd.append('layer', 'raw')
      if (assetForm.sample_rate) fd.append('sample_rate', assetForm.sample_rate)
      const res = await uploadAssetApi(fd, (e) => {
        if (e.total) uploadProgress.value = Math.round((e.loaded / e.total) * 100)
      })
      uploadProgress.value = 0
      ElMessage.success(res.message || '文件已上传并接入数据湖（原始层）')
    } else {
      if (!folderFiles.value.length) {
        ElMessage.warning('请选择文件夹')
        saving.value = false
        return
      }
      const assets = folderFiles.value.map((f) => ({
        data_type: f.data_type,
        layer: 'raw',
        file_name: f.file_name,
        file_path: `raw/${f.file_path}`,
        file_format: f.file_format,
        file_size: f.file_size * 1024,
      }))
      const res = await batchCreateAssetsApi({ subject_id: assetForm.subject_id, assets })
      ElMessage.success(res.message || '批量导入成功')
    }
    accessDialog.value = false
    // 刷新数据动态 + 数据资产列表（按类型查看时），确保接入结果立即反映在界面上
    loadLayerStat()
    if (viewMode.value === 'type') loadAssetsByType()
  } finally {
    saving.value = false
  }
}

const viewAssets = async (row) => {
  currentSubject.value = row
  assetListDialog.value = true
  try {
    const res = await getAssetsApi({ page: 1, page_size: 100, subject_id: row.id })
    currentAssets.value = res.data.items || []
  } catch (e) {
    currentAssets.value = []
  }
}

// 数据资产编辑
const assetEditDialog = ref(false)
const editingAssetId = ref(null)
const assetEditForm = reactive({
  file_name: '', data_type: 'video', layer: 'raw', file_format: '', sample_rate: null,
})

const openAssetEditDialog = (row) => {
  editingAssetId.value = row.id
  Object.assign(assetEditForm, {
    file_name: row.file_name || '',
    data_type: row.data_type || 'video',
    layer: row.layer || 'raw',
    file_format: row.file_format || '',
    sample_rate: row.sample_rate ?? null,
  })
  assetEditDialog.value = true
}

const handleSaveAsset = async () => {
  saving.value = true
  try {
    await updateAssetApi(editingAssetId.value, { ...assetEditForm })
    ElMessage.success('数据资产已更新')
    assetEditDialog.value = false
    // 刷新当前视图的资产列表与分层统计
    if (viewMode.value === 'type') {
      loadAssetsByType()
    } else if (currentSubject.value) {
      const res = await getAssetsApi({ page: 1, page_size: 100, subject_id: currentSubject.value.id })
      currentAssets.value = res.data.items || []
    }
    loadLayerStat()
  } finally {
    saving.value = false
  }
}

const removeAsset = (row) => {
  ElMessageBox.confirm(
    `确认删除数据资产「${row.file_name}」？该操作会同步删除磁盘文件与转码缓存，不可恢复。`,
    '危险操作',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      await deleteAssetApi(row.id)
      ElMessage.success('数据资产已删除')
      // 立即从当前列表移除，避免等待接口返回的延迟
      assetList.value = assetList.value.filter((a) => a.id !== row.id)
      currentAssets.value = currentAssets.value.filter((a) => a.id !== row.id)
      assetPagination.total = Math.max(0, assetPagination.total - 1)
      // 异步刷新完整列表与统计
      if (viewMode.value === 'type') {
        await loadAssetsByType()
      }
      loadLayerStat()
      // 视频资产删除后同步刷新受试者列表（video_types 标签即时更新，无需手动刷新页面）
      loadSubjects()
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}

// ===== 按数据类型视图：数据资产管理 =====
const allSubjects = ref([])
const subjectMap = computed(() => {
  const m = {}
  allSubjects.value.forEach((s) => { m[s.id] = s })
  return m
})
const assetFilters = reactive({ data_type: '', layer: '', status: '', keyword: '' })
const assetPagination = reactive({ page: 1, page_size: 20, total: 0 })
const assetList = ref([])
const assetTableLoading = ref(false)
const selectedAssets = ref([])
const assetTableRef = ref()

const layerText = (l) => ({ raw: '原始', cleaned: '清洗', feature: '特征', annotation: '标注' }[l] || l || '—')
const layerTagType = (l) => ({ raw: 'info', cleaned: 'success', feature: 'warning', annotation: 'danger' }[l] || 'info')
const statusText = (s) => ({
  uploaded: '已上传', cleaning: '清洗中', standardized: '已标准化',
  annotating: '标注中', done: '已完成',
}[s] || s || '—')
const statusTagType = (s) => ({
  uploaded: 'info', cleaning: 'warning', standardized: 'primary',
  annotating: 'warning', done: 'success',
}[s] || 'info')
const dataTypeTagType = (t) => ({
  video: 'danger', audio: 'primary', eeg: 'success', ecg: 'warning',
  eye: 'info', gait: 'info', scale: 'success', task: 'warning', json: 'info',
}[t] || 'info')

// 文件大小格式化：字节 → B/KB/MB/GB/TB
const formatFileSize = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`
}

const onViewModeChange = (mode) => {
  if (mode === 'type') {
    loadAllSubjects()
    loadAssetsByType()
  }
}

const loadAllSubjects = async () => {
  // 缓存：页面生命周期内已加载则跳过重复拉取（subjectMap 仅在引用变化时重建）
  if (allSubjects.value.length) return
  try {
    const res = await getSubjectsApi({ page: 1, page_size: 10000 })
    allSubjects.value = res.data.items || []
  } catch (e) {
    allSubjects.value = []
  }
}

const onAssetSearch = () => {
  assetPagination.page = 1
  loadAssetsByType()
}

const onResetAssetFilters = () => {
  assetFilters.data_type = ''
  assetFilters.layer = ''
  assetFilters.status = ''
  assetFilters.keyword = ''
  assetPagination.page = 1
  loadAssetsByType()
}

// 跨页选中：selectedAssets 作为单一数据源，ID 集合驱动
// 翻页后通过 _syncTableSelection 同步当前页的勾选状态到表格 DOM
// 用户在表格勾选/取消时通过 onSelectionChange 同步回 selectedAssets
let _suppressSelectionChange = false  // 防止 _syncTableSelection 触发的 selection-change 反向覆盖

const onSelectionChange = (rows) => {
  if (_suppressSelectionChange) return
  // 当前页 ID 集合
  const currentPageIds = new Set(assetList.value.map((r) => r.id))
  // 当前页被选中的 ID 集合
  const selectedInPage = new Set(rows.map((r) => r.id))
  // 合并：保留其他页选中 + 当前页变更
  const kept = selectedAssets.value.filter((r) => !currentPageIds.has(r.id))
  const pageRows = rows.map((r) => ({ ...r, _placeholder: false }))
  // 去重合并
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

// 翻页/筛选后，根据 selectedAssets 同步当前页的勾选状态到表格 DOM
const _syncTableSelection = () => {
  if (!assetTableRef.value) return
  _suppressSelectionChange = true
  try {
    const selectedIds = new Set(selectedAssets.value.map((r) => r.id))
    assetList.value.forEach((row) => {
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
      data_type: assetFilters.data_type,
      layer: assetFilters.layer,
      ids_only: true,
    })
    const allIds = (res.data?.items || []).map((x) => x.id)
    if (!allIds.length) {
      ElMessage.warning('当前筛选条件下无可选数据')
      return
    }
    const allIdSet = new Set(allIds)
    // 保留当前已选（其他筛选条件下的）+ 添加当前筛选条件的全部 ID
    const existingNotInFilter = selectedAssets.value.filter((r) => !allIdSet.has(r.id))
    const newSelected = allIds.map((id) => {
      const existing = selectedAssets.value.find((r) => r.id === id)
      return existing || { id, _placeholder: true }
    })
    selectedAssets.value = [...existingNotInFilter, ...newSelected]
    ElMessage.success(`已跨页全选 ${allIds.length} 个文件`)
    // 同步当前页勾选状态
    nextTick(() => _syncTableSelection())
  } catch (e) {
    // 接口失败时静默
  } finally {
    selectAllLoading.value = false
  }
}

// 清空所有选择（包含其他页）
const clearAllSelection = () => {
  selectedAssets.value = []
  assetTableRef.value?.clearSelection()
}

const loadAssetsByType = async () => {
  assetTableLoading.value = true
  try {
    const res = await getAssetsApi({
      page: assetPagination.page,
      page_size: assetPagination.page_size,
      data_type: assetFilters.data_type,
      layer: assetFilters.layer,
    })
    let items = res.data.items || []
    // 状态与关键词后端暂不支持，前端按当前页过滤
    if (assetFilters.status) {
      items = items.filter((i) => i.status === assetFilters.status)
    }
    if (assetFilters.keyword) {
      const kw = assetFilters.keyword.toLowerCase()
      items = items.filter((i) => (i.file_name || '').toLowerCase().includes(kw))
    }
    assetList.value = items
    assetPagination.total = res.data.total || 0
    // 翻页后同步当前页的勾选状态到表格 DOM
    nextTick(() => _syncTableSelection())
  } catch (e) {
    assetList.value = []
  } finally {
    assetTableLoading.value = false
  }
}

const batchRemoveAssets = () => {
  if (!selectedAssets.value.length) return
  ElMessageBox.confirm(
    `确认删除选中的 ${selectedAssets.value.length} 个数据资产？该操作会同步删除磁盘文件，不可恢复。`,
    '批量删除',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      // 使用 allSettled 确保单条失败不阻塞其他删除，并精确报告成功/失败数
      const results = await Promise.allSettled(selectedAssets.value.map((a) => deleteAssetApi(a.id)))
      const successCount = results.filter(r => r.status === 'fulfilled').length
      const failCount = results.length - successCount
      if (failCount === 0) {
        ElMessage.success(`已删除 ${successCount} 个数据资产`)
      } else {
        ElMessage.warning(`成功删除 ${successCount} 个，失败 ${failCount} 个（失败可能因权限或文件占用）`)
      }
      if (successCount > 0) {
        // reserve-selection 模式下需显式清空表格选中状态
        assetTableRef.value?.clearSelection()
        selectedAssets.value = []
        loadAssetsByType()
        loadLayerStat()
        // 删除后同步刷新受试者列表（video_types 标签即时更新）
        loadSubjects()
      }
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}

// ===== 操作日志 =====
const logDialog = ref(false)
const logLoading = ref(false)
const logList = ref([])
const logFilters = reactive({ username: '', action: '', target_type: '' })
const logPagination = reactive({ page: 1, page_size: 20, total: 0 })

const actionText = (a) => ({
  create: '创建', update: '更新', delete: '删除',
  upload: '上传', batch_import: '批量导入',
  pre_annotate: '预标注', assign: '分配', annotate: '标注', review: '复核',
  clean: '清洗', standardize: '标准化',
}[a] || a)
const actionTagType = (a) => ({
  create: 'success', update: 'warning', delete: 'danger',
  upload: 'primary', batch_import: 'info',
  pre_annotate: 'info', assign: 'primary', annotate: 'success', review: 'warning',
  clean: 'primary', standardize: 'success',
}[a] || 'info')
const roleText = (r) => ({
  admin: '管理员', doctor: '医生', annotator: '标注员',
  nurse: '护理人员', engineer: '数据工程师',
}[r] || (r || '—'))
const roleTagType = (r) => ({
  admin: 'danger', doctor: 'warning', annotator: 'primary',
  nurse: 'info', engineer: 'success',
}[r] || 'info')

const openLogDialog = () => {
  logDialog.value = true
  logPagination.page = 1
  loadLogs()
}

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
  } finally {
    logLoading.value = false
  }
}

// ==================== 浏览器目录监控（FS Access API，全局 store 单例） ====================
const scanActiveTab = ref('browser')
const bsStore = useBrowserScanStore()
const browserScan = bsStore.state
const manualFolderInputRef = ref(null)
const manualScanFiles = ref([])
const manualScanRunning = ref(false)

// 不支持时的原因提示（区分：浏览器不支持 vs 非安全上下文）
const unsupportedReasonText = computed(() => {
  const reason = getUnsupportedReason()
  if (reason) return reason + '。可使用下方「手动一次性扫描」，或在「服务端目录扫描」页配置后端挂载目录。'
  return '当前浏览器不支持目录监控（需 Chrome/Edge 86+ 且通过 HTTPS 或 localhost 访问）。可使用下方「手动一次性扫描」，或在「服务端目录扫描」页配置后端挂载目录。'
})

// 扫描完成后的延迟串行刷新：避免与后端 DB 写事务竞争导致死锁
const _refreshAfterScan = async () => {
  await new Promise((r) => setTimeout(r, 800))
  try { await loadSubjects() } catch { /* 刷新失败不阻断扫描 */ }
  try { await loadDataStats() } catch { /* 刷新失败不阻断扫描 */ }
}

const pickBrowserDir = async () => {
  try {
    const ok = await bsStore.pickDir()
    if (ok) ElMessage.success(`已选择目录：${browserScan.dirName}`)
  } catch (e) {
    ElMessage.error('选择目录失败：' + (e.message || e))
  }
}

const clearBrowserDir = async () => {
  await bsStore.clearDir()
  ElMessage.info('已移除监控目录')
}

const scanBrowserOnce = async () => {
  try {
    const prev = { subjects: browserScan.totalNewSubjects, files: browserScan.totalUploaded }
    await bsStore.scanOnce()
    const r = browserScan.lastResult
    const syncPart = r?.updatedSubjects ? `，更新 ${r.updatedSubjects} 个受试者信息` : ''
    const obsPart = r?.observingCount ? `，${r.observingCount} 个文件处于写入观察期（下轮读数未变才上传）` : ''
    if (r && (r.newSubjects || r.uploadedPaths.length || r.updatedSubjects)) {
      ElMessage.success(`本次扫描：新增 ${r.newSubjects} 个受试者，上传 ${r.uploadedPaths.length} 个文件${syncPart}${obsPart}`)
      _refreshAfterScan()
    } else if (r && r.observingCount) {
      ElMessage.info(`正在观察 ${r.observingCount} 个文件（首次发现只登记，下一轮扫描读数未变才上传）`)
    } else if (r && !r.failures.length) {
      ElMessage.info('本次扫描无新增文件')
    } else if (r && r.failures.length) {
      ElMessage.warning(`扫描完成，${r.failures.length} 个文件失败`)
    }
  } catch (e) {
    ElMessage.error('扫描失败：' + (e.message || e))
  }
}

const startBrowserWatch = async () => {
  try {
    // 用户主动开始监控：重置写入观察期基线，目录内尚未上传的文件重新观察一轮
    await bsStore.startWatch({ resetObservation: true })
    ElMessage.success('已开始监控，路由切换不会中断，刷新页面后可一键恢复')
  } catch (e) {
    ElMessage.error(e.message || '启动监控失败')
  }
}

const stopBrowserWatch = () => {
  bsStore.stopWatch()
  ElMessage.info('已停止监控')
}

const restoreBrowserWatch = async () => {
  try {
    await bsStore.restoreWatch()
    ElMessage.success('已恢复监控')
  } catch (e) {
    ElMessage.error(e.message || '恢复失败')
  }
}

// 组件挂载时注册扫描完成回调（store 在后台扫描时会调此回调刷新列表）
const _onScanComplete = (result) => {
  if (result.newSubjects || result.uploadedPaths.length || result.updatedSubjects) {
    const syncPart = result.updatedSubjects ? `，更新 ${result.updatedSubjects} 个受试者信息` : ''
    const obsPart = result.observingCount ? `，${result.observingCount} 个文件待观察` : ''
    ElMessage.success(`自动扫描：新增 ${result.newSubjects} 个受试者，上传 ${result.uploadedPaths.length} 个文件${syncPart}${obsPart}`)
    _refreshAfterScan()
  } else if (result.observingCount) {
    ElMessage.info(`自动扫描：正在观察 ${result.observingCount} 个文件（下一轮读数未变才上传）`)
  }
}

/** 组件挂载时初始化 store：恢复持久化目录句柄，注册扫描完成回调 */
const initBrowserWatch = async () => {
  try {
    await bsStore.init()
    bsStore.setOnScanComplete(_onScanComplete)
    // 刷新页面后自动恢复监控：权限仍有效则直接恢复扫描（不弹授权框、无需用户点击）；
    // 仅权限降级（需用户手势授权）时才保留"恢复监控"按钮。
    await bsStore.autoResumeIfGranted()
  } catch { /* 忽略初始化错误 */ }
}

// 降级：手动一次性扫描（不支持 FS Access API 时，用 webkitdirectory 选目录后一次性导入）
const onManualFolderChange = (e) => {
  const files = Array.from(e.target.files || [])
  const recognized = []
  for (const f of files) {
    const path = f.webkitRelativePath || f.name
    const segs = path.split('/')
    if (segs.length < 2) continue // 根目录直接放的文件忽略
    const name = f.name
    const lower = name.toLowerCase()
    // userInfo 也作为 json 资产入库，不再跳过
    if (lower.endsWith('_sync_fields_zh.json')) continue
    if (name.startsWith('~$') || name.startsWith('.')) continue
    recognized.push({ file: f, path, name, size: f.size, lastModified: f.lastModified })
  }
  manualScanFiles.value = recognized
  if (recognized.length) ElMessage.success(`已识别 ${recognized.length} 个文件`)
  e.target.value = ''
}

const runManualScanOnce = async () => {
  if (!manualScanFiles.value.length) return
  manualScanRunning.value = true
  browserScan.scanning = true
  browserScan.progress = { phase: 'scan', current: 0, total: manualScanFiles.value.length, currentFile: '处理中…' }
  try { await loadSubjectCache() } catch { /* 忽略 */ }
  try {
    // 复用持久化的已上传记录：原先传空对象，导致每次点击都全量重传
    //（后端幂等虽挡住重复入库，但网络与解密开销照付）
    const result = await runScanFromFiles(manualScanFiles.value, {
      uploadedMap: loadUploadedMap(),
      onProgress: (p) => { browserScan.progress = p },
    })
    saveUploadedMap(result.uploadedMap || {})
    if (result.newSubjects || result.uploadedPaths.length || result.updatedSubjects) {
      const syncPart = result.updatedSubjects ? `，更新 ${result.updatedSubjects} 个受试者信息` : ''
      const obsPart = result.observingCount ? `，${result.observingCount} 个文件待观察` : ''
      ElMessage.success(`导入完成：新增 ${result.newSubjects} 个受试者，上传 ${result.uploadedPaths.length} 个文件${syncPart}${obsPart}`)
      _refreshAfterScan()
    } else if (result.observingCount) {
      ElMessage.warning(`已登记 ${result.observingCount} 个文件，但读数尚未稳定（可能仍在写入）。请稍后再点击一次「开始导入」完成入库`)
    } else {
      ElMessage.info('无文件被导入')
    }
    if (result.failures.length) {
      browserScan.failures = result.failures
      ElMessage.warning(`${result.failures.length} 个文件导入失败，详见失败列表`)
    }
    manualScanFiles.value = []
  } catch (e) {
    ElMessage.error('导入失败：' + (e.message || e))
  } finally {
    manualScanRunning.value = false
    browserScan.scanning = false
  }
}

// ==================== 受试者文件夹自动扫描 ====================
const scanConfigDialog = ref(false)
const scanConfigLoading = ref(false)
const scanConfigList = ref([])
const scanEditDialog = ref(false)
const editingScanId = ref(null)
const scanSaving = ref(false)
const scanForm = reactive({
  name: '',
  watch_dir: '',
  path_prefix: '',
  interval_minutes: 60,
  is_active: true,
  auto_upload_files: true,
  collection_batch: '',
  collection_scene: '',
})
const scanFolderInputRef = ref(null)

// 路径前缀本地持久化 key（同一台机/同一浏览器共享，避免每次重复填写）
const SCAN_PATH_PREFIX_KEY = 'scan_config_path_prefix'

// 读取本地保存的前缀，初始化到表单
const loadPathPrefix = () => {
  try {
    scanForm.path_prefix = localStorage.getItem(SCAN_PATH_PREFIX_KEY) || ''
  } catch (e) {
    scanForm.path_prefix = ''
  }
}

// 从监控文件夹完整路径中自动提取前缀（最后一段视为文件夹名，前面部分视为前缀）
// 提取后保存到本地，下次选文件夹时自动复用
const onWatchDirBlur = () => {
  const dir = (scanForm.watch_dir || '').trim()
  if (!dir) return
  // 必须包含路径分隔符，否则视为纯文件夹名（无法提取前缀）
  const match = dir.match(/^(.*[\\/])([^\\/]+)[\\/]*$/)
  if (!match) return
  const prefix = match[1]
  // 与当前已记忆前缀不同则更新并保存
  if (prefix !== scanForm.path_prefix) {
    scanForm.path_prefix = prefix
    try {
      localStorage.setItem(SCAN_PATH_PREFIX_KEY, prefix)
    } catch (e) { /* localStorage 不可用时静默忽略 */ }
  }
}

// 拼接前缀和文件夹名，保证中间只有一个分隔符
const joinPath = (prefix, folderName) => {
  const p = prefix.replace(/[\\/]+$/, '')
  return `${p}/${folderName}`
}

const handleScanFolderChange = (e) => {
  const files = e.target.files || []
  if (!files.length) return
  // webkitRelativePath 格式为 "文件夹名/子文件"，取第一段作为文件夹名
  const relPath = files[0].webkitRelativePath || ''
  const folderName = relPath.split('/')[0] || ''
  if (!folderName) {
    ElMessage.warning('未能获取文件夹名，请重新选择')
    e.target.value = ''
    return
  }
  const prefix = (scanForm.path_prefix || '').trim()
  if (prefix) {
    // 有记忆前缀：自动拼接为完整路径
    scanForm.watch_dir = joinPath(prefix, folderName)
    ElMessage.success(`已用记忆前缀自动拼接：${scanForm.watch_dir}`)
  } else {
    // 无记忆前缀：填入文件夹名，提示用户首次可输入完整路径让系统记忆
    scanForm.watch_dir = folderName
    ElMessage.info(`已选择文件夹「${folderName}」。浏览器无法直接读取完整路径，请直接在输入框补全为完整路径（如 D:/data/${folderName}），失焦后系统会自动记忆前缀，之后选文件夹即可自动拼接`)
  }
  // 清空 input 的 value 以便重复选择同一文件夹
  e.target.value = ''
}


const openScanConfigDialog = () => {
  scanConfigDialog.value = true
  loadScanConfigs()
}

const loadScanConfigs = async () => {
  scanConfigLoading.value = true
  try {
    const res = await getScanConfigsApi()
    scanConfigList.value = res.data || []
  } catch (e) {
    scanConfigList.value = []
  } finally {
    scanConfigLoading.value = false
  }
}

const openScanCreate = () => {
  editingScanId.value = null
  Object.assign(scanForm, {
    name: '',
    watch_dir: '',
    path_prefix: '',
    interval_minutes: 60,
    is_active: true,
    auto_upload_files: true,
    collection_batch: '',
    collection_scene: '',
  })
  // 从本地恢复上次保存的前缀，方便复用
  loadPathPrefix()
  // 刷新批次/场景下拉选项（最新优先）
  _loadBatchAndSceneOptions()
  scanEditDialog.value = true
}

const openScanEdit = (row) => {
  editingScanId.value = row.id
  Object.assign(scanForm, {
    name: row.name,
    watch_dir: row.watch_dir,
    path_prefix: '',
    interval_minutes: row.interval_minutes,
    is_active: row.is_active,
    auto_upload_files: row.auto_upload_files,
    collection_batch: row.collection_batch || '',
    collection_scene: row.collection_scene || '',
  })
  // 编辑时也恢复本地前缀，便于下次选择文件夹时拼接
  loadPathPrefix()
  // 刷新批次/场景下拉选项（最新优先）
  _loadBatchAndSceneOptions()
  scanEditDialog.value = true
}

const handleSaveScanConfig = async () => {
  if (!scanForm.name.trim()) {
    ElMessage.warning('请填写配置名称')
    return
  }
  if (!scanForm.watch_dir.trim()) {
    ElMessage.warning('请填写监控文件夹路径')
    return
  }
  scanSaving.value = true
  try {
    // path_prefix 仅前端本地使用，不提交到后端
    const { path_prefix, ...payload } = scanForm
    if (editingScanId.value) {
      await updateScanConfigApi(editingScanId.value, payload)
    } else {
      await createScanConfigApi(payload)
    }
    ElMessage.success('保存成功')
    scanEditDialog.value = false
    loadScanConfigs()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    scanSaving.value = false
  }
}

const handleRunScan = async (row) => {
  try {
    const res = await runScanNowApi(row.id)
    const newCount = res.data?.new_count || 0
    ElMessage.success(res.message || `扫描完成，新增 ${newCount} 个受试者`)
    loadScanConfigs()
    loadSubjects()
    loadLayerStat()
    // 扫描到新受试者时自动刷新页面（重置筛选条件并重新加载）
    if (newCount > 0) {
      // 清空筛选条件，确保新受试者立即可见
      filters.keyword = ''
      filters.gender = ''
      filters.riskLevel = ''
      await nextTick()
      loadSubjects()
    }
  } catch (e) {
    /* 拦截器已提示 */
  }
}

const handleDeleteScanConfig = (row) => {
  ElMessageBox.confirm(`确认删除扫描配置「${row.name}」？`, '提示', { type: 'warning' })
    .then(async () => {
      await deleteScanConfigApi(row.id)
      ElMessage.success('已删除')
      loadScanConfigs()
    }).catch(() => {})
}

// 直接在列表切换启停（无需进入编辑）
const toggleScanActive = async (row, val) => {
  row._toggling = true
  try {
    // 仅传必要字段，避免把 _toggling/last_scan_at/last_scan_count 等前端临时字段污染到后端
    await updateScanConfigApi(row.id, {
      name: row.name,
      watch_dir: row.watch_dir,
      interval_minutes: row.interval_minutes,
      is_active: val,
      id_pattern: row.id_pattern,
      auto_upload_files: row.auto_upload_files,
      collection_batch: row.collection_batch || '',
      collection_scene: row.collection_scene || '',
    })
    row.is_active = val
    ElMessage.success(val ? '已启用扫描' : '已停用扫描')
  } catch (e) {
    // 失败时 switch 会自动回弹（model-value 仍为旧值）
    ElMessage.error(e?.response?.data?.message || '操作失败')
  } finally {
    row._toggling = false
  }
}

onMounted(() => {
  loadSubjects()
  loadLayerStat()
  loadSubjectTemplate()
  loadBatchOptions()
  initBrowserWatch()
  window.addEventListener('resize', handleLayerResize)
})

onBeforeUnmount(() => {
  // 路由切换时仅清除组件回调，不停止 store 定时器（监控持续运行）
  bsStore.setOnScanComplete(null)
  window.removeEventListener('resize', handleLayerResize)
  layerChart?.dispose()
  trendChart?.dispose()
  typeChart?.dispose()
  riskChart?.dispose()
  genderChart?.dispose()
  layerChart = null
  trendChart = null
  typeChart = null
  riskChart = null
  genderChart = null
})
</script>

<style scoped lang="scss">
.feature-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}
.feature-col {
  flex: 1 1 0;
  min-width: 220px;
}
.feature-card {
  cursor: pointer;
  text-align: center;
  height: 100%;
  transition: transform 0.2s;
  &:hover {
    transform: translateY(-2px);
  }
  .feature-title {
    font-size: 15px;
    font-weight: 600;
    margin: 8px 0 4px;
  }
  .feature-desc {
    font-size: 12px;
    color: #909399;
    line-height: 1.6;
  }
}

/* 数据动态可视化区 */
.data-stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 16px;
  background: #fafbfc;
  border-radius: 6px;
  border-left: 3px solid #ebeef5;
}
.data-stat-info {
  flex: 1;
  min-width: 0;
}
.data-stat-value {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
  word-break: break-all;
}
.data-stat-title {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.chart-block {
  background: #fafbfc;
  border-radius: 6px;
  padding: 8px 12px 4px;
  height: 100%;
}
.chart-block-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 4px;
}
</style>
