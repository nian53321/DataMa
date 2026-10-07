<template>
  <div class="page-container">
    <div class="page-header">
      <span class="title">数据对齐与可视化</span>
      <el-space wrap>
        <el-radio-group v-model="viewMode" @change="onModeChange">
          <el-radio-button value="subject">按受试者</el-radio-button>
          <el-radio-button value="file">按文件</el-radio-button>
        </el-radio-group>
        <template v-if="viewMode === 'subject'">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索伪ID/备注"
          clearable
          style="width: 180px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-select
          v-model="filters.gender"
          placeholder="性别"
          clearable
          style="width: 100px"
          @change="onSearch"
        >
          <el-option label="男" value="男" />
          <el-option label="女" value="女" />
        </el-select>
        <el-select
          v-model="filters.riskLevel"
          placeholder="风险分级"
          clearable
          style="width: 140px"
          @change="onSearch"
        >
          <el-option label="无" value="无" />
          <el-option label="轻度" value="轻度" />
          <el-option label="中度" value="中度" />
          <el-option label="重度" value="重度" />
          <el-option label="未评估" value="none" />
        </el-select>
        <el-select
          v-model="selectedSubject"
          placeholder="选择受试者"
          filterable
          style="width: 200px"
          @change="onSubjectChange"
        >
          <el-option
            v-for="s in subjectList"
            :key="s.id"
            :label="`${s.pseudo_id}${s.cognitive_risk_level ? ' · ' + riskText(s.cognitive_risk_level) : ''}`"
            :value="s.id"
          />
        </el-select>
        <el-button :icon="RefreshLeft" @click="onResetFilters">重置</el-button>
        <el-button type="primary" :icon="Aim" :disabled="!selectedSubject" :loading="aligning" @click="handleAlign">
          执行对齐
        </el-button>
        </template>
        <template v-else>
        <el-select
          v-model="assetFilter.data_type"
          placeholder="数据类型"
          clearable
          style="width: 140px"
        >
          <el-option v-for="(label, key) in dataTypeText" :key="key" :label="label" :value="key" />
        </el-select>
        <el-input
          v-model="assetFilter.keyword"
          placeholder="搜索文件名"
          clearable
          style="width: 200px"
        />
        <el-select
          v-model="selectedAssetId"
          placeholder="选择数据资产"
          filterable
          style="width: 380px"
          @change="onAssetChange"
        >
          <el-option
            v-for="a in filteredFileAssets"
            :key="a.id"
            :label="`${a.file_name} (${dataTypeText[a.data_type] || a.data_type})`"
            :value="a.id"
          />
        </el-select>
        <el-tag v-if="selectedAsset" size="small" type="success">
          {{ dataTypeText[selectedAsset.data_type] || selectedAsset.data_type }} · 共 {{ filteredFileAssets.length }} 个资产
        </el-tag>
        </template>
      </el-space>
    </div>

    <!-- 未选择受试者时的数据概览仪表盘 -->
    <div v-if="viewMode === 'subject' && !selectedSubject" v-loading="overviewLoading">
      <!-- 统计卡片 -->
      <el-row :gutter="16">
        <el-col :span="6" v-for="s in statCards" :key="s.label">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-icon" :style="{ background: s.bg }">
              <el-icon :size="24" color="#fff"><component :is="s.icon" /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ s.value }}</div>
              <div class="stat-label">{{ s.label }}</div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 分布图 -->
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="12">
          <el-card>
            <template #header><span>受试者风险分级分布</span></template>
            <div ref="riskPieRef" style="height: 260px"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card>
            <template #header><span>数据资产模态分布</span></template>
            <div ref="assetBarRef" style="height: 260px"></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 引导提示 -->
      <el-card style="margin-top: 16px">
        <el-alert
          type="info"
          :closable="false"
          title="请选择受试者查看多模态数据可视化"
          description="上方支持按伪ID/性别/风险分级筛选受试者，选中后即可查看视频、音频、脑电、心电、眼动、步态等多模态对齐可视化。"
          show-icon
        />
      </el-card>
    </div>


    <div v-else-if="viewMode === 'subject' && selectedSubject" v-loading="loading">
      <!-- 受试者信息 + 量表 -->
      <el-card style="margin-bottom: 16px">
        <template #header><span>受试者信息</span></template>
        <el-descriptions :column="{ xs: 1, sm: 2, md: 3, lg: 6 }" border size="small">
          <el-descriptions-item label="伪ID">{{ subjectInfo?.pseudo_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="年龄">{{ maskField('age', subjectInfo?.age) ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="性别">{{ maskField('gender', subjectInfo?.gender) || '—' }}</el-descriptions-item>
          <el-descriptions-item label="认知风险">{{ riskText(subjectInfo?.cognitive_risk_level) }}</el-descriptions-item>
          <el-descriptions-item label="MMSE">{{ subjectInfo?.mmse_score ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="MoCA">{{ subjectInfo?.moca_score ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="AD8">{{ subjectInfo?.ad8_score ?? '—' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 分模态可视化 -->
      <el-row :gutter="16">
        <el-col :span="24">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>视频模块</span>
                <el-tag size="small" :type="videoGroups.some(g => g.videos.length) ? 'success' : 'info'">共 {{ videoList.length }} 路 · {{ videoGroups.filter(g => g.videos.length).length }} 类</el-tag>
              </div>
            </template>
            <!-- 三个视频子模块（face/body/gait，始终渲染；缺失时显示"尚未采集"），每类独立播放器 -->
            <div class="video-subgrid" style="display: flex; flex-wrap: wrap; gap: 16px">
              <div v-for="g in videoGroups" :key="g.type" style="flex: 1 1 320px; min-width: 300px; border: 1px solid #ebeef5; border-radius: 6px; padding: 12px">
                <!-- 子模块头：类型标签 + 该类视频下拉 -->
                <div class="card-title" style="margin-bottom: 8px">
                  <span>{{ g.label }}</span>
                  <el-space v-if="g.videos.length">
                    <el-select
                      v-if="g.videos.length > 1"
                      v-model="videoGroupSel[g.type]"
                      size="small"
                      style="width: 190px"
                    >
                      <el-option
                        v-for="(v, i) in g.videos"
                        :key="v.id"
                        :label="`${i + 1}. ${v.file_name}`"
                        :value="v.id"
                      />
                    </el-select>
                    <el-tag size="small" type="success">{{ g.videos.length }} 路</el-tag>
                  </el-space>
                </div>
                <!-- 播放器 -->
                <div class="video-box">
                  <div class="video-wrap" v-if="currentVideoOf(g.type)?.file_url">
                    <video
                      v-if="!videoGroupLoad[g.type]?.error"
                      :ref="(el) => { if (el) videoGroupRefs[g.type] = el; else delete videoGroupRefs[g.type] }"
                      :key="currentVideoOf(g.type).id"
                      :src="playUrl(currentVideoOf(g.type))"
                      controls
                      controlslist="nodownload noremoteplayback"
                      playsinline
                      crossorigin="anonymous"
                      class="video-el"
                      @loadstart="videoGroupLoad[g.type] = { loading: true, error: false }"
                      @canplay="videoGroupLoad[g.type] = { loading: false, error: false }"
                      @error="onGroupVideoError(g.type, $event)"
                      @contextmenu.prevent
                    />
                    <!-- 转码/加载中遮罩 -->
                    <div v-if="videoGroupLoad[g.type]?.loading && !videoGroupLoad[g.type]?.error" class="loading-overlay">
                      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
                      <p>{{ isPlayableVideo(currentVideoOf(g.type).file_format) ? '加载中...' : '正在转码为 MP4，请稍候...' }}</p>
                    </div>
                    <!-- 出错回退下载（仅管理员可下载原文件） -->
                    <div v-if="videoGroupLoad[g.type]?.error" class="preview-box">
                      <el-icon size="32" color="#e6a23c"><VideoCamera /></el-icon>
                      <p>该格式暂无法在线播放</p>
                      <el-button v-if="isAdmin" type="primary" size="small" :icon="Download" tag="a" :href="fileUrl(currentVideoOf(g.type))" download>
                        下载文件
                      </el-button>
                      <p v-else class="sub-tip" style="color: #909399; font-size: 12px">无下载权限，请联系管理员</p>
                    </div>
                  </div>
                  <div v-else class="preview-box">
                    <el-icon size="32" color="#c0c4cc"><VideoCamera /></el-icon>
                    <p>尚未采集</p>
                  </div>
                  <p v-if="currentVideoOf(g.type)" class="sub-tip" style="margin-top: 6px">
                    {{ currentVideoOf(g.type).file_name }}
                    <el-tag size="small" :type="isPlayableVideo(currentVideoOf(g.type).file_format) ? 'success' : 'warning'" style="margin-left: 6px">
                      {{ isPlayableVideo(currentVideoOf(g.type).file_format) ? '可在线播放' : '自动转码播放' }}
                    </el-tag>
                  </p>
                </div>
                <!-- 深度视频（伪彩色，按需加载转码） -->
                <div v-if="currentVideoOf(g.type)" class="depth-video">
                  <el-divider content-position="left"><el-icon><DataAnalysis /></el-icon>&nbsp;深度视频（伪彩色）</el-divider>
                  <div v-if="depthGroupOf(g.type).status === 'idle'" class="preview-box">
                    <el-button type="primary" plain @click="loadDepthGroupVideo(g.type)">
                      <el-icon style="margin-right: 6px"><VideoCamera /></el-icon>加载深度视频
                    </el-button>
                    <p class="sub-tip" style="color: #909399; font-size: 12px; margin-top: 6px">
                      按需转码，仅当需要可视化深度数据时执行（首次约需数十秒）
                    </p>
                  </div>
                  <div v-else-if="depthGroupOf(g.type).status === 'failed'" class="preview-box">
                    <el-icon size="32" color="#c0c4cc"><VideoCamera /></el-icon>
                    <p v-if="depthGroupOf(g.type).noDepth">该视频无深度轨，无法可视化</p>
                    <template v-else>
                      <p>深度视频加载失败，请重试</p>
                      <el-button type="primary" size="small" plain @click="loadDepthGroupVideo(g.type)">重试</el-button>
                    </template>
                  </div>
                  <div v-else class="depth-video-box">
                    <video
                      v-if="depthGroupOf(g.type).url"
                      :key="depthGroupOf(g.type).url"
                      :src="depthGroupOf(g.type).url"
                      controls
                      controlslist="nodownload noremoteplayback"
                      playsinline
                      class="depth-video-el"
                      @loadeddata="onDepthGroupLoaded(g.type, $event)"
                      @error="onDepthGroupError(g.type, $event)"
                    />
                    <div v-if="depthGroupOf(g.type).status === 'loading'" class="loading-overlay">
                      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
                      <p>正在转码深度视频，请稍候（首次约需数十秒）...</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </el-card>
        </el-col>
        <!-- 音频模块（单独占一行） -->
        <el-col :span="24" style="margin-top: 16px">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>音频模块</span>
                <el-space v-if="audioList.length">
                  <el-select
                    v-model="currentAudioId"
                    size="small"
                    style="width: 220px"
                  >
                    <el-option
                      v-for="(a, i) in audioList"
                      :key="a.id"
                      :label="`${i + 1}. ${a.file_name}`"
                      :value="a.id"
                    />
                  </el-select>
                  <el-tag size="small" type="success">共 {{ audioList.length }} 路</el-tag>
                </el-space>
                <el-tag v-else size="small" type="info">无音频数据</el-tag>
              </div>
            </template>
            <div class="audio-box">
              <audio
                v-if="currentAudio?.file_url"
                :key="currentAudio.id"
                :src="playUrl(currentAudio)"
                controls
                controlslist="nodownload"
                @contextmenu.prevent
                style="width: 100%"
              />
              <div v-else class="preview-box">
                <el-icon size="40" color="#c0c4cc"><Microphone /></el-icon>
                <p>该受试者暂无音频数据</p>
              </div>
              <p v-if="currentAudio" class="sub-tip" style="margin-top: 6px">{{ currentAudio.file_name }}</p>
            </div>
          </el-card>
        </el-col>
        <!-- 脑电信号 EEG -->
        <el-col v-if="tracks.some(t => t.data_type === 'eeg')" :span="24" style="margin-top: 16px">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>脑电信号 EEG</span>
                <el-space>
                  <el-button-group size="small">
                    <el-button size="small" @click="toggleAllEegChannels(true)">全部显示</el-button>
                    <el-button size="small" @click="toggleAllEegChannels(false)">全部取消</el-button>
                  </el-button-group>
                  <el-radio-group v-model="eegLayout" size="small">
                    <el-radio-button value="stack">堆叠</el-radio-button>
                    <el-radio-button value="overlay">叠加</el-radio-button>
                  </el-radio-group>
                  <el-tag v-if="eegMeta" size="small" type="success">{{ eegMeta.device }}</el-tag>
                  <el-tag v-if="eegMeta" size="small">{{ eegMeta.channels }}通道 · {{ eegMeta.sampleRate }}Hz · {{ eegMeta.duration }}s</el-tag>
                </el-space>
              </div>
            </template>
            <div class="chart-label">
              多通道波形（{{ eegMeta?.channels || 0 }} 通道 ·
              {{ eegLayout === 'stack' ? '纵向堆叠，各通道独立 Y 轴' : '叠加显示，共用 Y 轴' }} ·
              原始值，未做基线/单位换算）
            </div>
            <div ref="eegChannelRef" :style="{ height: eegLayout === 'stack' ? `${Math.max(600, (eegMeta?.channels || 1) * 90)}px` : '600px' }"></div>
          </el-card>
        </el-col>
        <!-- 心电 ECG -->
        <el-col v-if="tracks.some(t => t.data_type === 'ecg')" :span="12" style="margin-top: 16px">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>心电信号 ECG</span>
                <el-space>
                  <el-tag v-if="ecgMeta" size="small" type="success">{{ ecgMeta.device }}</el-tag>
                  <el-tag v-if="ecgMeta" size="small">{{ ecgMeta.sampleRate }}Hz · {{ ecgMeta.duration }}s · {{ ecgMeta.points }}点</el-tag>
                  <el-tag v-else size="small" type="info">无数据</el-tag>
                </el-space>
              </div>
            </template>
            <div ref="ecgRef" style="height: 360px"></div>
          </el-card>
        </el-col>

        <!-- 眼动 Eye -->
        <el-col v-if="tracks.some(t => t.data_type === 'eye')" :span="12" style="margin-top: 16px">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>眼动指标 Eye Tracking</span>
                <el-space>
                  <el-tag v-if="eyeMeta?.risk_value" size="small" type="warning">风险指数 {{ eyeMeta.risk_value }}</el-tag>
                  <el-tag v-if="eyeMeta?.risk_proportion" size="small">高于同龄人 {{ (eyeMeta.risk_proportion * 100).toFixed(1) }}%</el-tag>
                  <el-tag v-if="eyeParseFailed" size="small" type="danger">解析失败</el-tag>
                  <el-tag v-else-if="!eyeMeta" size="small" type="info">无数据</el-tag>
                </el-space>
              </div>
            </template>
            <div v-if="!eyeParseFailed" ref="eyeRef" style="height: 360px"></div>
            <el-empty v-else description="眼动数据解析失败" :image-size="80" />
          </el-card>
        </el-col>

        <!-- 步态 Gait -->
        <el-col v-if="tracks.some(t => t.data_type === 'gait')" :span="12" style="margin-top: 16px">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>步态分析 Gait</span>
                <el-tag size="small" type="warning">模拟足底压力</el-tag>
              </div>
            </template>
            <div ref="gaitRef" style="height: 200px"></div>
          </el-card>
        </el-col>
        <el-col :span="24" style="margin-top: 16px">
          <el-card>
            <template #header>
              <div class="card-title">
                <span>量表与认知任务模块</span>
                <el-tag v-if="scaleCards.length" size="small" type="info">{{ scaleCards.length }} 张量表</el-tag>
              </div>
            </template>
            <!-- 全部量表资产：每张表一张卡片（有几张展示几张） -->
            <div
              v-if="scaleCards.length"
              style="display: flex; flex-wrap: wrap; gap: 16px"
            >
              <div
                v-for="card in scaleCards"
                :key="card.assetId"
                style="flex: 1 1 320px; min-width: 300px"
              >
                <div
                  class="scale-card-header"
                  style="margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; gap: 8px"
                >
                  <span class="scale-card-name" style="font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                    {{ card.summary?.assess_name || card.file_name || '量表' }}
                  </span>
                  <el-tag v-if="card.summary" size="small" type="success" style="flex: none">
                    {{ card.summary.total_score }}/{{ card.summary.max_score }}
                    <template v-if="card.summary.education_adjusted">（教育校正）</template>
                  </el-tag>
                </div>
                <div
                  v-if="card.summary && card.summary.sections && card.summary.sections.length"
                  :ref="(el) => { if (el) scaleRadarEls[card.assetId] = el; else delete scaleRadarEls[card.assetId] }"
                  style="height: 260px; border: 1px solid #ebeef5; border-radius: 6px"
                ></div>
                <el-empty
                  v-else-if="card.summary"
                  :description="`总分 ${card.summary.total_score}/${card.summary.max_score}（暂无分项明细）`"
                  :image-size="70"
                />
                <el-empty v-else description="量表数据解析失败" :image-size="70" />
                <div class="scale-card-file" style="margin-top: 4px; font-size: 12px; color: #909399; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                  {{ card.file_name }}
                </div>
              </div>
            </div>
            <!-- 无量表资产但受试者字段有量表得分：回退展示受试者字段雷达图 -->
            <div v-else-if="hasSubjectScaleFields" ref="radarRef" style="height: 280px"></div>
            <el-empty v-else description="暂无量表数据" :image-size="80" />
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 按文件模式 -->
    <div v-else-if="viewMode === 'file'" class="file-mode" v-loading="fileLoading">
      <!-- 未选择资产提示 -->
      <el-card v-if="!selectedAsset">
        <el-alert
          type="info"
          :closable="false"
          title="请选择一个数据资产进行可视化"
          description="顶部支持按数据类型筛选与关键词搜索，选中后即可查看对应的视频/音频/脑电/心电/眼动/步态等单文件可视化。"
          show-icon
        />
      </el-card>

      <!-- 选中资产后：文件信息 + 多模态网格布局（与按受试者模式一致） -->
      <template v-else>
        <!-- 文件信息 -->
        <el-card style="margin-bottom: 16px">
          <template #header>
            <div class="card-title">
              <span>文件信息</span>
              <el-tag size="small" type="primary">{{ dataTypeText[selectedAsset.data_type] || selectedAsset.data_type }}</el-tag>
            </div>
          </template>
          <el-descriptions :column="{ xs: 1, sm: 2, md: 3, lg: 6 }" border size="small">
            <el-descriptions-item label="文件名">{{ selectedAsset.file_name }}</el-descriptions-item>
            <el-descriptions-item label="类型">{{ dataTypeText[selectedAsset.data_type] || selectedAsset.data_type }}</el-descriptions-item>
            <el-descriptions-item label="格式">{{ selectedAsset.file_format || '—' }}</el-descriptions-item>
            <el-descriptions-item label="分层">{{ selectedAsset.layer || '—' }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ selectedAsset.status || '—' }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ selectedAsset.created_at || '—' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- 多模态网格布局：按文件模式只显示对应类型模块 -->
        <el-row :gutter="16">
          <!-- 视频模块 -->
          <el-col v-if="selectedAsset.data_type === 'video'" :span="24">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>视频模块</span>
                  <el-tag size="small" type="success">当前文件</el-tag>
                </div>
              </template>
              <div class="video-box">
                <div class="video-wrap" v-if="selectedAsset.file_url">
                  <video
                    v-if="!videoError"
                    ref="videoRef"
                    :key="selectedAsset.id"
                    :src="playUrl(selectedAsset)"
                    controls
                    controlslist="nodownload noremoteplayback"
                    playsinline
                    crossorigin="anonymous"
                    class="video-el"
                    @loadstart="videoLoading = true"
                    @canplay="videoLoading = false"
                    @error="onVideoError"
                    @contextmenu.prevent
                  />
                  <div v-if="videoLoading && !videoError" class="loading-overlay">
                    <el-icon class="is-loading" :size="32"><Loading /></el-icon>
                    <p>{{ isPlayableVideo(selectedAsset.file_format) ? '加载中...' : '正在转码为 MP4，请稍候...' }}</p>
                  </div>
                  <div v-if="videoError" class="preview-box">
                    <el-icon size="40" color="#e6a23c"><VideoCamera /></el-icon>
                    <p>该格式暂无法在线播放</p>
                    <el-button v-if="isAdmin" type="primary" size="small" :icon="Download" tag="a" :href="fileUrl(selectedAsset)" download>
                      下载文件
                    </el-button>
                    <p v-else class="sub-tip" style="color: #909399; font-size: 12px">无下载权限，请联系管理员</p>
                  </div>
                </div>
                <div v-else class="preview-box">
                  <el-icon size="40" color="#c0c4cc"><VideoCamera /></el-icon>
                  <p>该资产暂无视频文件</p>
                </div>
                <p class="sub-tip" style="margin-top: 6px">
                  {{ selectedAsset.file_name }}
                  <el-tag size="small" :type="isPlayableVideo(selectedAsset.file_format) ? 'success' : 'warning'" style="margin-left: 6px">
                    {{ isPlayableVideo(selectedAsset.file_format) ? '可在线播放' : '自动转码播放' }}
                  </el-tag>
                </p>
              </div>
              <!-- 深度视频（伪彩色，像彩色视频一样逐帧播放；手动按需加载转码） -->
              <div v-if="selectedAsset" class="depth-video">
                <el-divider content-position="left"><el-icon><DataAnalysis /></el-icon>&nbsp;深度视频（伪彩色）</el-divider>
                <div v-if="depthVideoState.status === 'idle'" class="preview-box">
                  <el-button type="primary" plain @click="loadDepthVideo(selectedAsset.id)">
                    <el-icon style="margin-right: 6px"><VideoCamera /></el-icon>加载深度视频
                  </el-button>
                  <p class="sub-tip" style="color: #909399; font-size: 12px; margin-top: 6px">
                    按需转码，仅当需要可视化深度数据时执行（首次约需数十秒）
                  </p>
                </div>
                <div v-else-if="depthVideoState.status === 'failed'" class="preview-box">
                  <el-icon size="40" color="#c0c4cc"><VideoCamera /></el-icon>
                  <p v-if="depthVideoState.noDepth">该视频无深度轨，无法可视化</p>
                  <template v-else>
                    <p>深度视频加载失败，请重试</p>
                    <el-button type="primary" size="small" plain @click="loadDepthVideo(selectedAsset.id)">重试</el-button>
                  </template>
                </div>
                <div v-else class="depth-video-box">
                  <video
                    :key="depthVideoState.url"
                    :src="depthVideoState.url"
                    controls
                    controlslist="nodownload noremoteplayback"
                    playsinline
                    class="depth-video-el"
                    @loadeddata="onDepthVideoLoaded"
                    @error="onDepthVideoError"
                  />
                  <div v-if="depthVideoState.status === 'loading'" class="loading-overlay">
                    <el-icon class="is-loading" :size="32"><Loading /></el-icon>
                    <p>正在转码深度视频，请稍候（首次约需数十秒）...</p>
                  </div>
                </div>
              </div>
            </el-card>
          </el-col>

          <!-- 音频模块 -->
          <el-col v-if="selectedAsset.data_type === 'audio'" :span="24">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>音频模块</span>
                  <el-tag size="small" type="success">当前文件</el-tag>
                </div>
              </template>
              <div class="audio-box">
                <audio
                  v-if="selectedAsset.file_url"
                  :key="selectedAsset.id"
                  :src="playUrl(selectedAsset)"
                  controls
                  controlslist="nodownload"
                  @contextmenu.prevent
                  style="width: 100%"
                />
                <div v-else class="preview-box">
                  <el-icon size="40" color="#c0c4cc"><Microphone /></el-icon>
                  <p>该资产暂无音频文件</p>
                </div>
                <p class="sub-tip" style="margin-top: 6px">{{ selectedAsset.file_name }}</p>
              </div>
            </el-card>
          </el-col>

          <!-- 脑电信号 EEG -->
          <el-col v-if="selectedAsset.data_type === 'eeg'" :span="24" style="margin-top: 16px">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>脑电信号 EEG</span>
                  <el-space>
                    <el-button-group size="small">
                      <el-button size="small" @click="toggleAllEegChannels(true)">全部显示</el-button>
                      <el-button size="small" @click="toggleAllEegChannels(false)">全部取消</el-button>
                    </el-button-group>
                    <el-radio-group v-model="eegLayout" size="small">
                      <el-radio-button value="stack">堆叠</el-radio-button>
                      <el-radio-button value="overlay">叠加</el-radio-button>
                    </el-radio-group>
                    <el-tag size="small" type="success">当前文件</el-tag>
                    <el-tag v-if="eegMeta" size="small" type="success">{{ eegMeta.device }}</el-tag>
                    <el-tag v-if="eegMeta" size="small">{{ eegMeta.channels }}通道 · {{ eegMeta.sampleRate }}Hz · {{ eegMeta.duration }}s</el-tag>
                  </el-space>
                </div>
              </template>
              <div class="chart-label">
                多通道波形（{{ eegMeta?.channels || 0 }} 通道 ·
                {{ eegLayout === 'stack' ? '纵向堆叠，各通道独立 Y 轴' : '叠加显示，共用 Y 轴' }} ·
                原始值，未做基线/单位换算）
              </div>
              <div ref="eegChannelRef" :style="{ height: eegLayout === 'stack' ? `${Math.max(600, (eegMeta?.channels || 1) * 90)}px` : '600px' }"></div>
            </el-card>
          </el-col>

          <!-- 心电 ECG -->
          <el-col v-if="selectedAsset.data_type === 'ecg'" :span="24" style="margin-top: 16px">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>心电信号 ECG</span>
                  <el-space>
                    <el-tag size="small" type="success">当前文件</el-tag>
                    <el-tag v-if="ecgMeta" size="small" type="success">{{ ecgMeta.device }}</el-tag>
                    <el-tag v-if="ecgMeta" size="small">{{ ecgMeta.sampleRate }}Hz · {{ ecgMeta.duration }}s · {{ ecgMeta.points }}点</el-tag>
                  </el-space>
                </div>
              </template>
              <div ref="ecgRef" style="height: 300px"></div>
            </el-card>
          </el-col>

          <!-- 眼动 Eye -->
          <el-col v-if="selectedAsset.data_type === 'eye'" :span="24" style="margin-top: 16px">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>眼动指标 Eye Tracking</span>
                  <el-space>
                    <el-tag v-if="eyeMeta?.risk_value" size="small" type="warning">风险指数 {{ eyeMeta.risk_value }}</el-tag>
                    <el-tag v-if="eyeMeta?.risk_proportion" size="small">高于同龄人 {{ (eyeMeta.risk_proportion * 100).toFixed(1) }}%</el-tag>
                    <el-tag v-if="eyeParseFailed" size="small" type="danger">解析失败</el-tag>
                    <el-tag v-else size="small" type="success">当前文件</el-tag>
                  </el-space>
                </div>
              </template>
              <div v-if="!eyeParseFailed" ref="eyeRef" style="height: 300px"></div>
              <el-empty v-else description="眼动数据解析失败" :image-size="80" />
            </el-card>
          </el-col>

          <!-- 步态 Gait -->
          <el-col v-if="selectedAsset.data_type === 'gait'" :span="24" style="margin-top: 16px">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>步态分析 Gait</span>
                  <el-tag size="small" type="success">当前文件</el-tag>
                </div>
              </template>
              <div ref="gaitRef" style="height: 300px"></div>
            </el-card>
          </el-col>

          <!-- 量表与认知任务 -->
          <el-col v-if="['scale', 'task'].includes(selectedAsset.data_type)" :span="24" style="margin-top: 16px">
            <el-card>
              <template #header>
                <div class="card-title">
                  <span>量表与认知任务模块</span>
                  <el-space>
                    <el-tag v-if="scaleMeta?.summary" size="small" type="success">
                      {{ scaleMeta.summary.assess_name || '量表' }} {{ scaleMeta.summary.total_score }}/{{ scaleMeta.summary.max_score }}
                    </el-tag>
                    <el-tag v-if="scaleParseFailed" size="small" type="danger">解析失败</el-tag>
                    <el-tag v-else size="small" type="success">当前文件</el-tag>
                  </el-space>
                </div>
              </template>
              <div v-if="!scaleParseFailed" ref="radarRef" style="height: 280px"></div>
              <el-empty v-else description="量表数据解析失败" :image-size="80" />
            </el-card>
          </el-col>
        </el-row>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { Aim, RefreshLeft, Download, Loading, User, DataLine, VideoCamera, Headset, Microphone, DataAnalysis } from '@element-plus/icons-vue'
import { getSubjectOverviewApi, getTimelineApi, alignModalitiesApi, getEegAssetApi, getEcgAssetApi, getEyeAssetApi, getScaleAssetApi } from '@/api/visualization'
import { getSubjectsApi as getSubjects, getAssetsApi } from '@/api/data'
import { fetchAllPages } from '@/utils/fetchAll'
import { fetchSignedUrlApi } from '@/api/media'
import { useUserStore } from '@/stores/user'
import { usePrivacyStore } from '@/stores/privacy'
import { ElMessage } from 'element-plus'

const userStore = useUserStore()
const privacy = usePrivacyStore()
const route = useRoute()
// 仅管理员可下载原始文件（/file 接口后端已限制 @role_required(ADMIN)）
const isAdmin = computed(() => userStore.role === 'admin')
// 受试者敏感字段展示脱敏：非 admin 后端已脱敏故原样返回；
// admin 受「隐私信息」开关控制（关闭时按 /system/desensitize/config 规则打码）
const maskField = (key, value) => privacy.mask(key, value)

const selectedSubject = ref(null)
const subjectList = ref([])
const subjectInfo = ref(null)
const tracks = ref([])
const loading = ref(false)
const aligning = ref(false)
let subjectSeq = 0  // 受试者切换请求序号：快速切换时丢弃过期响应，避免数据错配

// 顶部筛选
const filters = reactive({ keyword: '', gender: '', riskLevel: '' })
const overviewLoading = ref(false)
const allSubjects = ref([]) // 全量受试者（用于统计）
const allAssets = ref([])   // 全量数据资产（用于统计）

const radarRef = ref()
const videoRef = ref()
const eegChannelRef = ref()
const ecgRef = ref()
const eyeRef = ref()
const gaitRef = ref()
const riskPieRef = ref()
const assetBarRef = ref()
const eegMeta = ref(null)
const ecgMeta = ref(null)
// 脑电布局：overlay=全部通道叠加在同一坐标系（默认，共用 Y 轴便于横向对比）
// stack=纵向堆叠瀑布图（每通道独立 Y 轴，通道多时需要长页面滚动）
const eegLayout = ref('overlay')
const eyeMeta = ref(null)       // 眼动指标（sync_data 解析结果）
const eyeParseFailed = ref(false)  // 眼动解析失败标记：失败时不回退模拟数据，直接展示失败状态
const scaleMeta = ref(null)     // 量表得分（按文件模式单选，MoCA 等）
const scaleParseFailed = ref(false)  // 量表解析失败标记（按文件模式）：失败时不回退受试者字段
const scaleCards = ref([])      // 受试者模式全部量表资产 [{ assetId, file_name, summary }]
let scaleRadarEls = {}          // 量表雷达图 DOM 引用映射（受试者模式，assetId -> el）
let scaleRadarCharts = {}       // 量表雷达图 echarts 实例映射（assetId -> chart）
let radarChart = null
let eegChannelChart = null
// 脑电图容器尺寸观察器：堆叠模式容器高度随通道数变化，需自动 resize
let eegResizeObserver = null
// EEG 渲染序号：updateEegCharts 含 await，快速切换受试者/资产时丢弃过期渲染
let eegRenderSeq = 0
let eegChannelsData = []
// legend 选中状态（通道名 -> 是否显示），控制堆叠子图里哪些通道参与排布
let eegLegendSel = {}
let ecgChart = null
let eyeChart = null
let gaitChart = null
let riskPieChart = null
let assetBarChart = null

// 深度视频播放器（Orbbec 深度轨转码为伪彩色 MP4，像彩色视频一样逐帧播放）
// 状态机：idle（未请求）→ 点击"加载深度视频" → loading（转码中）→ ready（可播放）
// 或 failed（noDepth=true 为真无深度轨；否则转码/网络失败，可重试）
// 手动触发：切换视频仅重置为 idle，不自动请求转码接口，避免无谓的服务器转码开销
const depthVideoState = reactive({ url: '', status: 'idle', noDepth: false, retried: false, resumeAt: 0 })
const resetDepthVideo = () => {
  depthVideoState.url = ''
  depthVideoState.status = 'idle'
  depthVideoState.noDepth = false
  depthVideoState.retried = false
  depthVideoState.resumeAt = 0
}
const loadDepthVideo = (assetId) => {
  if (!assetId || depthVideoState.status === 'loading' || depthVideoState.status === 'ready') return
  depthVideoState.url = ''
  depthVideoState.noDepth = false
  depthVideoState.retried = false
  depthVideoState.status = 'loading'
  // 用 5 分钟过期的资源绑定签名 URL 替代长期 JWT 进 URL（避免 token 泄漏）
  fetchSignedUrlApi({ kind: 'depth_video', asset_id: assetId })
    .then((res) => {
      const url = res?.data?.url
      if (url && depthVideoState.status === 'loading') depthVideoState.url = url
    })
    .catch(() => {
      if (depthVideoState.status === 'loading') {
        depthVideoState.url = ''
        depthVideoState.status = 'failed'
      }
    })
}
const onDepthVideoLoaded = (e) => {
  // 自动重试换 URL 后恢复到出错时的播放位置
  if (depthVideoState.resumeAt > 0) {
    try { e.target.currentTime = depthVideoState.resumeAt } catch (err) { /* ignore */ }
    depthVideoState.resumeAt = 0
  }
  depthVideoState.status = 'ready'
}
const markDepthVideoFailed = (noDepth) => {
  depthVideoState.url = ''
  depthVideoState.noDepth = noDepth
  depthVideoState.status = 'failed'
}
const onDepthVideoError = (e) => {
  const code = e?.target?.error?.code
  if (code === 1) return // aborted，正常切换
  const resumeAt = e?.target?.currentTime || 0
  const assetId = selectedAsset.value?.id
  if (!assetId) return markDepthVideoFailed(false)
  const stale = () => selectedAsset.value?.id !== assetId // 探测期间已切换资产则丢弃结果
  // 探测真实状态码后再处理（video error 不携带 HTTP 状态）：
  // 200=资源正常（多为签名 URL 过期后播放中补发请求被 401）→ 换新 URL 自动重试并恢复进度；
  // 404=无深度轨；其余=转码/网络失败。自动重试每轮仅一次，避免循环
  fetchSignedUrlApi({ kind: 'depth_video', asset_id: assetId })
    .then((res) => {
      if (stale()) return
      const url = res?.data?.url
      if (!url) return markDepthVideoFailed(false)
      fetch(url, { method: 'HEAD' })
        .then((resp) => {
          if (stale()) return
          if (resp.status === 200 && !depthVideoState.retried) {
            depthVideoState.retried = true
            depthVideoState.resumeAt = resumeAt
            depthVideoState.url = url
            return
          }
          markDepthVideoFailed(resp.status === 404)
        })
        .catch(() => { if (!stale()) markDepthVideoFailed(false) })
    })
    .catch(() => { if (!stale()) markDepthVideoFailed(false) })
}

// 按文件模式
const viewMode = ref('subject')
const dataTypeText = {
  video: '视频', audio: '音频', eeg: '脑电', ecg: '心电',
  eye: '眼动', gait: '步态', scale: '量表', task: '任务',
}
const assetFilter = reactive({ data_type: '', keyword: '' })
const fileAssetList = ref([])
const fileLoading = ref(false)
const selectedAssetId = ref(null)
const selectedAsset = computed(() => fileAssetList.value.find((a) => a.id === selectedAssetId.value) || null)
const filteredFileAssets = computed(() => fileAssetList.value.filter((a) => {
  if (assetFilter.data_type && a.data_type !== assetFilter.data_type) return false
  if (assetFilter.keyword && !(a.file_name || '').toLowerCase().includes(assetFilter.keyword.toLowerCase())) return false
  return true
}))

const riskText = (r) => ({
  '无': '无', '轻度': '轻度', '中度': '中度', '重度': '重度',
}[r] || (r || '未评估'))

// 统计卡片
const statCards = computed(() => [
  { label: '受试者总数', value: allSubjects.value.length, icon: 'User', bg: '#409eff' },
  { label: '数据资产总数', value: allAssets.value.length, icon: 'DataLine', bg: '#67c23a' },
  { label: '视频文件', value: allAssets.value.filter((a) => a.data_type === 'video').length, icon: 'VideoCamera', bg: '#e6a23c' },
  { label: '生理信号', value: allAssets.value.filter((a) => ['eeg', 'ecg', 'eye', 'gait'].includes(a.data_type)).length, icon: 'Headset', bg: '#f56c6c' },
])

// 筛选查询
const onSearch = async () => {
  overviewLoading.value = true
  try {
    const params = { page: 1, page_size: 200 }
    if (filters.keyword) params.keyword = filters.keyword
    if (filters.gender) params.gender = filters.gender
    if (filters.riskLevel) params.cognitive_risk_level = filters.riskLevel
    const res = await getSubjects(params)
    subjectList.value = res.data.items || []
  } catch (e) {
    subjectList.value = []
  } finally {
    overviewLoading.value = false
  }
}

const onResetFilters = () => {
  filters.keyword = ''
  filters.gender = ''
  filters.riskLevel = ''
  onSearch()
}

// 视频轨/音频轨（支持多路，可切换）
// 视频播放列表：排除原始深度序列资产（.zst，非可播放视频，经 depth-video 端点单独转码）
const videoList = computed(() => tracks.value.filter((t) => {
  if (t.data_type !== 'video') return false
  const m = t.metadata || {}
  if ((t.file_name || '').toLowerCase().endsWith('.zst') || m.depth_raw) return false
  return true
}))
const audioList = computed(() => tracks.value.filter((t) => t.data_type === 'audio'))
const currentVideoId = ref(null)
const currentAudioId = ref(null)
const videoError = ref(false)
const videoLoading = ref(false)
const currentVideo = computed(() => videoList.value.find((v) => v.id === currentVideoId.value) || videoList.value[0])
const currentAudio = computed(() => audioList.value.find((a) => a.id === currentAudioId.value) || audioList.value[0])

// ===== 受试者模式视频子模块（face/body/gait + 通用，每类独立播放器） =====
const videoGroupDefs = [
  { type: 'face', label: '面部视频' },
  { type: 'body', label: '身体视频' },
  { type: 'gait', label: '步态视频' },
  { type: '', label: '通用视频' },
]
// 视频按类型分组（face/body/gait 三种模态始终渲染；无视频的显示"尚未采集"占位，
// 避免只采一个视频时单独占满整行）。通用视频（无类型）仅在确有视频时渲染，
// 以免多出一格造成误导。video_type 存于 metadata（face/body/gait）；无类型视为通用视频。
const videoGroups = computed(() =>
  videoGroupDefs
    .map((g) => ({ ...g, videos: videoList.value.filter((v) => (v.metadata?.video_type || '') === g.type) }))
    .filter((g) => g.type !== '' || g.videos.length)
)
const videoGroupSel = reactive({})    // type -> 当前选中视频 id
const videoGroupLoad = reactive({})   // type -> { loading, error }
const videoGroupRefs = {}             // type -> <video> DOM
const videoGroupDepth = reactive({})  // type -> 深度视频 { url, status, noDepth, retried, resumeAt }
const depthGroupOf = (type) => (videoGroupDepth[type] || (videoGroupDepth[type] = { url: '', status: 'idle', noDepth: false, retried: false, resumeAt: 0 }))
const currentVideoOf = (type) => {
  const g = videoGroups.value.find((x) => x.type === type)
  if (!g || !g.videos.length) return null
  return g.videos.find((v) => v.id === videoGroupSel[type]) || g.videos[0]
}
const onGroupVideoError = (type, e) => {
  const code = e?.target?.error?.code
  if (code === 1) return // aborted，正常切换
  videoGroupLoad[type] = { loading: false, error: true }
}
// 签名 URL 异步就绪后触发各子模块 video.load()（与单视频逻辑一致）
watch(
  () => videoGroups.value.map((g) => `${g.type}:${playUrl(currentVideoOf(g.type))}`).join('|'),
  () => {
    if (!videoGroups.value.length) return
    videoGroups.value.forEach((g) => {
      const src = playUrl(currentVideoOf(g.type))
      if (!src) return
      videoGroupLoad[g.type] = { loading: true, error: false }
      const el = videoGroupRefs[g.type]
      nextTick(() => { try { el?.load() } catch (e) { /* ignore */ } })
    })
  },
)
// 子模块切换视频时重置该组深度视频状态（不自动请求转码）
watch(
  () => videoGroups.value.map((g) => `${g.type}:${currentVideoOf(g.type)?.id || ''}`).join('|'),
  () => {
    videoGroupDefs.forEach((d) => {
      if (videoGroupDepth[d.type]) videoGroupDepth[d.type] = { url: '', status: 'idle' }
    })
  },
)
const loadDepthGroupVideo = (type) => {
  const cv = currentVideoOf(type)
  const st = depthGroupOf(type)
  if (!cv || st.status === 'loading' || st.status === 'ready') return
  st.url = ''
  st.noDepth = false
  st.retried = false
  st.status = 'loading'
  fetchSignedUrlApi({ kind: 'depth_video', asset_id: cv.id })
    .then((res) => {
      const url = res?.data?.url
      if (url && st.status === 'loading') st.url = url
    })
    .catch(() => {
      if (st.status === 'loading') { st.url = ''; st.status = 'failed' }
    })
}
const onDepthGroupLoaded = (type, e) => {
  const st = depthGroupOf(type)
  // 自动重试换 URL 后恢复到出错时的播放位置
  if (st.resumeAt > 0) {
    try { e.target.currentTime = st.resumeAt } catch (err) { /* ignore */ }
    st.resumeAt = 0
  }
  st.status = 'ready'
}
const markDepthGroupFailed = (type, noDepth) => {
  const st = depthGroupOf(type)
  st.url = ''
  st.noDepth = noDepth
  st.status = 'failed'
}
const onDepthGroupError = (type, e) => {
  const code = e?.target?.error?.code
  if (code === 1) return // aborted，正常切换
  const cv = currentVideoOf(type)
  if (!cv) return markDepthGroupFailed(type, false)
  const resumeAt = e?.target?.currentTime || 0
  // 探测期间切换了视频则丢弃结果（切换时状态对象会被整体替换）
  const stale = () => currentVideoOf(type)?.id !== cv.id
  // 探测真实状态码后再处理（video error 不携带 HTTP 状态）：
  // 200=资源正常（多为签名 URL 过期后播放中补发请求被 401）→ 换新 URL 自动重试并恢复进度；
  // 404=无深度轨；其余=转码/网络失败。自动重试每轮仅一次，避免循环
  fetchSignedUrlApi({ kind: 'depth_video', asset_id: cv.id })
    .then((res) => {
      if (stale()) return
      const url = res?.data?.url
      if (!url) return markDepthGroupFailed(type, false)
      fetch(url, { method: 'HEAD' })
        .then((resp) => {
          if (stale()) return
          const st = depthGroupOf(type)
          if (resp.status === 200 && !st.retried) {
            st.retried = true
            st.resumeAt = resumeAt
            st.url = url
            return
          }
          markDepthGroupFailed(type, resp.status === 404)
        })
        .catch(() => { if (!stale()) markDepthGroupFailed(type, false) })
    })
    .catch(() => { if (!stale()) markDepthGroupFailed(type, false) })
}

// 浏览器原生可播放的视频格式（mkv/avi/flv 等由后端转码为 mp4）
const PLAYABLE_VIDEO = ['mp4', 'webm', 'ogg', 'ogv', 'mov', 'm4v']
const isPlayableVideo = (fmt) => PLAYABLE_VIDEO.includes((fmt || '').toLowerCase())

// 视频/音频/下载地址：统一走后端短期签名 URL（资源绑定、5 分钟过期）
// 浏览器 <video>/<audio>/<a download> 标签无法发送 Authorization 头，
// 故由 /api/media/signed-url 签发 ?media_token= 短期签名，避免长期 JWT 进 URL。
// 缓存 4 分钟后后台刷新，保证 5 分钟窗口内不断流。
const mediaUrlCache = reactive({})
const mediaUrlFetching = new Set()
const MEDIA_CACHE_TTL = 4 * 60 * 1000
const refreshMediaSignedUrl = (kind, id) => {
  const key = `${kind}:${id}`
  if (mediaUrlFetching.has(key)) return
  mediaUrlFetching.add(key)
  fetchSignedUrlApi({ kind, asset_id: id })
    .then((res) => {
      const url = res?.data?.url
      if (url) mediaUrlCache[key] = { url, ts: Date.now() }
    })
    .catch(() => {})
    .finally(() => mediaUrlFetching.delete(key))
}
const getMediaSignedUrl = (kind, id) => {
  if (!id) return ''
  const key = `${kind}:${id}`
  const cached = mediaUrlCache[key]
  if (cached) {
    if (Date.now() - cached.ts < MEDIA_CACHE_TTL) return cached.url
    refreshMediaSignedUrl(kind, id) // 接近过期：后台刷新，暂用旧 URL
    return cached.url
  }
  refreshMediaSignedUrl(kind, id)
  return ''
}
const playUrl = (v) => getMediaSignedUrl('asset_play', v?.id)
const fileUrl = (v) => getMediaSignedUrl('asset_file', v?.id)

// 视频加载出错（如转码失败/文件损坏）
// 注：MEDIA_ERR_ABORTED(1) 是切换视频时浏览器正常中止旧请求，忽略
const onVideoError = (e) => {
  const code = e?.target?.error?.code
  if (code === 1) return // aborted，正常切换行为
  videoLoading.value = false
  videoError.value = true
}

// 切换视频时重置状态。不在 src 为空时调 load()（空 src load 会触发 error 误判播放失败），
// 由下方 watch(currentVideoPlaySrc) 在签名 URL 就绪后统一触发 load()。
const onVideoSwitch = () => {
  videoError.value = false
  videoLoading.value = true
}

// 签名 URL 异步填充 mediaUrlCache 后，<video> 的 :src 虽响应式更新，
// 但浏览器对已挂载元素改 src 不会自动重新加载——显式 load() 触发真正播放。
// 用 computed 精确绑定"当前视频的签名 URL"，仅当它从空变有值/变化时才 load，
// 避免 audio 等其他 kind 的 cache 更新误触 video.load()（空 src load 会触发 error）。
const currentVideoPlaySrc = computed(() =>
  viewMode.value === 'subject' ? playUrl(currentVideo.value) : playUrl(selectedAsset.value)
)
watch(currentVideoPlaySrc, (src) => {
  if (!src) return
  videoError.value = false
  videoLoading.value = true
  nextTick(() => {
    if (videoRef.value) {
      try { videoRef.value.load() } catch (e) { /* ignore */ }
    }
  })
})
// 切换视频/文件时重置深度视频为 idle（不自动请求转码，用户按需点击"加载深度视频"）
watch(() => (viewMode.value === 'subject' ? currentVideo.value?.id : null), () => resetDepthVideo())
watch(() => (viewMode.value === 'file' ? selectedAssetId.value : null), () => resetDepthVideo())

const hasModality = (t) => tracks.value.some((tr) => tr.data_type === t)
const trackOffset = (t) => {
  const tr = tracks.value.find((tr) => tr.data_type === t)
  return tr ? tr.offset_ms : 0
}

const initCharts = () => {
  if (radarRef.value && !radarChart) radarChart = echarts.init(radarRef.value)
  // EEG 卡片受 v-if 控制（该受试者/资产无 EEG 时整块卸载），DOM 换了但旧实例
  // 仍持有已脱离文档的节点。initCharts 跳过重建会导致图画在看不见的地方，
  // 故这里比对实例绑定的 DOM，不是当前 ref 就先销毁再重建。
  if (eegChannelRef.value) {
    const bound = eegChannelChart && !eegChannelChart.isDisposed()
      ? eegChannelChart.getDom() : null
    if (bound && bound !== eegChannelRef.value) {
      try { eegChannelChart.dispose() } catch (e) { /* 实例可能已失效 */ }
      eegChannelChart = null
      eegResizeObserver?.disconnect()
      eegResizeObserver = null
    }
    if (!eegChannelChart) {
      eegChannelChart = echarts.init(eegChannelRef.value)
      // 堆叠模式下容器高度随通道数变化（模板 :style 绑定），ECharts 不会自动
      // 跟随容器尺寸；window resize 事件覆盖不到这种「同页内高度变化」，
      // 故用 ResizeObserver 兜底，任何容器尺寸变化都重新 resize。
      if (typeof ResizeObserver !== 'undefined') {
        eegResizeObserver = new ResizeObserver(() => {
          if (eegChannelChart && !eegChannelChart.isDisposed()) eegChannelChart.resize()
        })
        eegResizeObserver.observe(eegChannelRef.value)
      }
    }
  }
  if (ecgRef.value && !ecgChart) ecgChart = echarts.init(ecgRef.value)
  if (eyeRef.value && !eyeChart) eyeChart = echarts.init(eyeRef.value)
  if (gaitRef.value && !gaitChart) gaitChart = echarts.init(gaitRef.value)
  // 适配当前 DOM 尺寸（防止初始化时宽高为 0）
  ;[radarChart, eegChannelChart, ecgChart, eyeChart, gaitChart].forEach((c) => c?.resize())
}

// 解析失败时销毁眼动图表实例：模板切换为失败提示后 DOM 已卸载，
// 保留实例会持有游离 DOM；下次成功解析时 initCharts 会重建
const disposeEyeChart = () => {
  if (eyeChart) {
    try { eyeChart.dispose() } catch (e) { /* 实例可能已失效 */ }
    eyeChart = null
  }
}

// 同上：解析失败时销毁量表雷达图实例
const disposeRadarChart = () => {
  if (radarChart) {
    try { radarChart.dispose() } catch (e) { /* 实例可能已失效 */ }
    radarChart = null
  }
}

// 步态模拟（左右足底压力交替）
const genGait = (points) => {
  const left = [], right = []
  for (let i = 0; i < points; i++) {
    const phase = (i / points) * 8
    left.push(Number((Math.max(0, Math.sin(phase * Math.PI)) * (0.7 + Math.random() * 0.3)).toFixed(3)))
    right.push(Number((Math.max(0, Math.sin(phase * Math.PI + Math.PI)) * (0.7 + Math.random() * 0.3)).toFixed(3)))
  }
  return { left, right }
}

// 通道数据极值：不用 Math.min(...arr) 展开语法 —— 通道点数可达 3000+，
// 大数组展开有爆栈风险（参数个数上限），EEG 每帧要算 16 次，必须走循环。
const chExtent = (arr) => {
  let mn = Infinity, mx = -Infinity
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    if (v < mn) mn = v
    if (v > mx) mx = v
  }
  return [mn, mx]
}

// 通道峰峰值
const updateEegCharts = async (eeg) => {
  if (!eeg) return
  const seq = ++eegRenderSeq
  const prevChannels = eegMeta.value?.channels || 0
  eegMeta.value = eeg.meta
  if (!eegChannelChart) return
  const channels = eeg.channels || []
  if (!channels.length) return
  // eegMeta 变化会改写堆叠模式的容器高度（channels * 90），ECharts 感知不到
  // 容器尺寸变化。必须等 DOM 更新后 resize canvas，否则 grid 按新高度布局、
  // canvas 仍是初始化时的旧高度 → 只显示上半部分（需切一次叠加再切回来才正常）。
  if (eegMeta.value?.channels !== prevChannels) {
    await nextTick()
    // await 期间可能已切换受试者/资产，或图表实例已被销毁重建
    if (seq !== eegRenderSeq || !eegChannelChart) return
    eegChannelChart.resize()
  }
  if (seq !== eegRenderSeq) return
  const colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272',
    '#fc8452', '#9a60fd', '#ea7ccc', '#5d7092', '#c9db70', '#00c2ff',
    '#ff7d00', '#8d98b3', '#e0c3fc', '#fbbf00']
  const numPoints = channels[0].data.length
  const xData = Array.from({ length: numPoints }, (_, i) => i)

  // 原始通道数据，**不做任何数值变换**（无基线去除、无缩放、无平移）
  eegChannelsData = channels.map((ch, idx) => ({
    name: ch.name.replace('EXG Channel ', 'CH'),
    data: ch.data,
    color: colors[idx % colors.length],
  }))

  const stackMode = eegLayout.value === 'stack'
  // 后端 unit='counts' 表示原始 ADC 计数，无量纲后缀
  const unit = eegMeta.value && eegMeta.value.unit === 'counts' ? '' : ' μV'

  // 堆叠模式：每个通道一个独立 grid + 独立 Y 轴，刻度即该通道的原始幅值。
  // 各通道直流偏置差异极大（实测 45310~106260，是单通道波幅的 7.6 倍），
  // 共用一个 Y 轴会把每条波形压成一条窄带（观感上「很平、没有波动」）。
  // 独立 Y 轴既保留原始数值，又让每条波形占满自己的绘图区。
  // 叠加模式：全部通道共用一个 Y 轴，范围取全局原始值。
  const visibleCh = () => eegChannelsData.filter(ch => eegLegendSel[ch.name] !== false)

  // Y 轴刻度缩写：原始计数常在 1e4~1e5，6 位数字会挤爆左边距
  const axFmt = (v) => (Math.abs(v) >= 10000
    ? `${(v / 1000).toFixed(1)}k`
    : String(Math.round(v)))

  const chRange = (ch) => {
    const [mn, mx] = chExtent(ch.data)
    const pad = (mx - mn) * 0.12 || 1
    return [Number((mn - pad).toFixed(2)), Number((mx + pad).toFixed(2))]
  }

  const buildSeries = (list) => list.map((ch, i) => ({
    name: ch.name,
    type: 'line',
    showSymbol: false,
    data: ch.data,
    xAxisIndex: stackMode ? i : 0,
    yAxisIndex: stackMode ? i : 0,
    lineStyle: { width: 1, color: ch.color },
    itemStyle: { color: ch.color },
    emphasis: { focus: 'series' },
  }))

  // 每个 grid 必须有自己的 xAxis —— ECharts 的 grid.xAxisIndex 是「引用」，
  // 若 N 个 grid 只给 1 个 xAxis，其余 grid 的坐标系在 axisProxy 阶段拿不到
  // axis 对象，渲染时读 axis.type 直接抛
  // "Cannot read properties of undefined (reading 'type')"。
  // 视觉上只保留最后一个子图的刻度与轴名，中间子图 axisLabel/axisLine 全关。
  const buildXAxes = (n) => {
    const out = []
    for (let i = 0; i < n; i++) {
      const isLast = i === n - 1
      out.push({
        type: 'category',
        gridIndex: i,
        data: xData,
        axisLabel: isLast
          ? { fontSize: 9, interval: Math.floor(numPoints / 8) || 1 }
          : { show: false },
        axisTick: { show: false },
        axisLine: isLast
          ? { show: true, lineStyle: { color: '#dcdfe6' } }
          : { show: false },
        splitLine: { show: false },
        axisPointer: { label: isLast ? { show: true } : { show: false } },
      })
    }
    return out
  }

  // 叠加模式：单个 x 轴，带轴名
  const buildOverlayXAxis = () => ([{
    type: 'category',
    gridIndex: 0,
    data: xData,
    name: '采样点',
    nameLocation: 'middle',
    nameGap: 24,
    axisLabel: { fontSize: 9, interval: Math.floor(numPoints / 8) || 1 },
    axisTick: { show: false },
    axisLine: { show: true, lineStyle: { color: '#dcdfe6' } },
    splitLine: { show: false },
  }])

  const buildStackAxes = (list) => {
    const n = list.length
    const top = 46
    const bottom = 40
    const gap = 8
    // 以容器真实高度为准，而不是按可见通道数重算高度：
    // 容器高度由「总通道数」决定（模板 :style），若这里按可见通道数 n 算
    // plotH，取消部分通道后子图只占容器上半部分，下半部分空着。
    const boxH = eegChannelRef.value?.clientHeight || Math.max(600, (eegMeta.value?.channels || n) * 90)
    const plotH = Math.max(120, boxH - top - bottom)
    const cellH = (plotH - gap * (n - 1)) / n
    const grid = []
    const yAxis = []
    list.forEach((ch, i) => {
      const [lo, hi] = chRange(ch)
      grid.push({
        left: 104, right: 24,
        top: top + i * (cellH + gap),
        height: cellH,
        xAxisIndex: i,
        yAxisIndex: i,
      })
      yAxis.push({
        type: 'value',
        gridIndex: i,
        min: lo,
        max: hi,
        // 通道名贴在各自子图左上角
        name: ch.name,
        nameLocation: 'end',
        nameGap: 6,
        nameTextStyle: { fontSize: 9, color: ch.color, align: 'right' },
        axisLabel: { fontSize: 9, formatter: axFmt },
        axisLine: { show: true, lineStyle: { color: '#dcdfe6' } },
        splitLine: { show: true, lineStyle: { color: '#f2f3f5' } },
        splitNumber: 3,
      })
    })
    return { grid, yAxis, xAxis: buildXAxes(n) }
  }

  const buildOverlayAxes = (list) => {
    let gMin = Infinity
    let gMax = -Infinity
    list.forEach(ch => {
      const [mn, mx] = chExtent(ch.data)
      if (mn < gMin) gMin = mn
      if (mx > gMax) gMax = mx
    })
    const pad = (gMax - gMin) * 0.05 || 1
    return {
      grid: { left: 104, right: 24, top: 46, bottom: 40 },
      xAxis: buildOverlayXAxis(),
      yAxis: [{
        type: 'value',
        min: Number((gMin - pad).toFixed(2)),
        max: Number((gMax + pad).toFixed(2)),
        name: `原始幅值${unit}`,
        nameTextStyle: { fontSize: 10 },
        axisLabel: { fontSize: 9, formatter: axFmt },
        splitLine: { lineStyle: { color: '#f2f3f5' } },
      }],
    }
  }

  const renderAxes = (list) => (stackMode ? buildStackAxes(list) : buildOverlayAxes(list))

  const vis = visibleCh()
  if (!vis.length) return
  const axes = renderAxes(vis)
  // grid 可能是对象（叠加）或数组（堆叠），统一成 x 轴索引数组
  const zoomIdx = Array.isArray(axes.grid)
    ? axes.grid.map((_, i) => i)
    : [0]

  eegChannelChart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      // 堆叠模式下鼠标只落在单个子图上，这里汇总所有可见通道在同一样ing点的原始值
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params]
        if (!arr.length) return ''
        const point = arr[0].axisValue
        const idx = Number(point)
        const lines = [`采样点 ${point}`]
        visibleCh().forEach(ch => {
          const v = ch.data[idx]
          if (v == null) return
          lines.push(
            `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;`
            + `background:${ch.color};margin-right:5px"></span>${ch.name}: `
            + `${Number(v).toFixed(1)}${unit}`)
        })
        return lines.join('<br/>')
      },
    },
    legend: {
      top: 4,
      textStyle: { fontSize: 10 },
      data: eegChannelsData.map(ch => ch.name),
      selected: eegLegendSel,
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: axes.grid,
    xAxis: axes.xAxis,
    yAxis: axes.yAxis,
    dataZoom: [
      { type: 'inside', xAxisIndex: zoomIdx },
      { type: 'slider', xAxisIndex: zoomIdx, height: 16, bottom: 6 },
    ],
    series: buildSeries(vis),
  }, true)

  // legend 切换：重排子图，隐藏的通道不留空槽
  eegChannelChart.off('legendselectchanged')
  eegChannelChart.on('legendselectchanged', (params) => {
    eegLegendSel = { ...(params.selected || {}) }
    const vis2 = visibleCh()
    if (!vis2.length) return
    const ax2 = renderAxes(vis2)
    const zi = Array.isArray(ax2.grid) ? ax2.grid.map((_, i) => i) : [0]
    eegChannelChart.setOption({
      grid: ax2.grid,
      xAxis: ax2.xAxis,
      yAxis: ax2.yAxis,
      dataZoom: [
        { type: 'inside', xAxisIndex: zi },
        { type: 'slider', xAxisIndex: zi, height: 16, bottom: 6 },
      ],
      series: buildSeries(vis2),
    }, true)
    eegChannelChart.resize()
  })
}

// 批量显示/隐藏所有脑电通道（Y 轴由 legendselectchanged 统一重排）
const toggleAllEegChannels = (show) => {
  if (!eegChannelChart || !eegChannelsData.length) return
  const selected = {}
  eegChannelsData.forEach(ch => { selected[ch.name] = show })
  eegLegendSel = selected
  eegChannelChart.setOption({ legend: { selected } })
}

// 切换堆叠/叠加布局：容器高度与 Y 轴口径都变，需按新布局完整重绘
watch(eegLayout, async () => {
  if (!eegChannelsData.length) return
  await nextTick()
  eegChannelChart?.resize()
  const payload = {
    meta: eegMeta.value,
    channels: eegChannelsData.map(ch => ({ name: ch.name.replace(/^CH/, 'EXG Channel '), data: ch.data })),
  }
  updateEegCharts(payload)
})

// 渲染真实 ECG 数据（单通道波形）
// 后端返回结构：{ meta: { channels, sampleRate, duration, device, points, baseline }, data: [[时间秒, 幅值], ...] }
const updateEcgChart = (ecg) => {
  if (!ecg) return
  ecgMeta.value = ecg.meta || null
  if (!ecgChart) return
  // 后端返回 [[时间秒, 幅值], ...]：时间轴已按采样率重建（降采样不压缩时间轴），
  // 幅值已扣除 ADC 基线，波形围绕 0 展示
  const data = ecg.data || []
  if (!data.length) {
    ecgChart.setOption({ series: [] }, true)
    return
  }
  // 计算 Y 轴范围，给上下留 5% 余量
  let yMin = Infinity, yMax = -Infinity
  for (const [, v] of data) {
    if (v < yMin) yMin = v
    if (v > yMax) yMax = v
  }
  if (!isFinite(yMin) || !isFinite(yMax)) { yMin = -1; yMax = 1 }
  const pad = (yMax - yMin) * 0.05 || 0.1
  ecgChart.setOption({
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (Array.isArray(v) ? v[1]?.toFixed(3) : (v == null ? '' : Number(v).toFixed(3))),
    },
    grid: { left: 56, right: 16, top: 30, bottom: 40 },
    xAxis: {
      type: 'value',
      min: 0,
      max: ecg.meta?.duration || undefined,
      name: '时间(s)',
      nameLocation: 'middle',
      nameGap: 22,
      axisLabel: { formatter: (v) => Number(v).toFixed(1) },
    },
    yAxis: {
      type: 'value',
      name: '幅值',
      min: Number((yMin - pad).toFixed(3)),
      max: Number((yMax + pad).toFixed(3)),
      nameLocation: 'end',
      nameGap: 10,
      nameTextStyle: { fontSize: 11 },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'slider', xAxisIndex: 0, height: 16, bottom: 6 },
    ],
    series: [{
      type: 'line',
      showSymbol: false,
      data,
      lineStyle: { color: '#ee6666', width: 1 },
      itemStyle: { color: '#ee6666' },
      areaStyle: { color: 'rgba(238,102,102,0.08)' },
    }],
  }, true)
}

// 清空 ECG 图表
const clearEcgChart = () => {
  ecgMeta.value = null
  if (ecgChart) ecgChart.setOption({ series: [] }, true)
}

// 眼动指标柱状图：能力值 + 眼跳速度
const updateEyeChart = (meta) => {
  if (!eyeChart) return
  const cv = meta.capacity_values || {}
  const categories = Object.keys(cv).filter(k => cv[k] != null)
  const values = categories.map(k => cv[k])
  // 眼跳各片段平均速度
  const speedList = meta.eye_jump?.speedList || []
  const speedLabels = speedList.map((_, i) => `P${i + 1}`)
  eyeChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['能力值', '眼跳平均速度'], top: 0 },
    grid: { left: 48, right: 48, top: 52, bottom: 30 },
    xAxis: [
      { type: 'category', data: categories, axisPointer: { type: 'shadow' } },
    ],
    yAxis: [
      { type: 'value', name: '能力值', min: 0, max: 1, position: 'left' },
      { type: 'value', name: '速度(°/s)', position: 'right' },
    ],
    series: [
      {
        name: '能力值', type: 'bar', data: values, barWidth: '40%',
        itemStyle: { color: '#5470c6', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', formatter: '{c}' },
      },
      {
        name: '眼跳平均速度', type: 'line', yAxisIndex: 1, data: speedList,
        showSymbol: true, symbolSize: 6, lineStyle: { color: '#91cc75', width: 2 },
        itemStyle: { color: '#91cc75' },
      },
    ],
  }, true)
}

const updateSimCharts = (modality = null) => {
  // modality 指定时仅渲染对应模态；不指定（受试者模式）渲染全部
  // 注：ECG 已由 updateEcgChart 接管真实数据
  // 眼动：有真实数据时显示能力值柱状图；无数据/解析失败时清空，不回退模拟数据
  if (eyeChart && (!modality || modality === 'eye')) {
    if (eyeMeta.value?.capacity_values) {
      updateEyeChart(eyeMeta.value)
    } else {
      eyeChart.setOption({ series: [] }, true)
    }
  }
  if (gaitChart && (!modality || modality === 'gait')) {
    const g = genGait(400)
    gaitChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['左脚', '右脚'], top: 0 },
      grid: { left: 40, right: 16, top: 36, bottom: 28 },
      xAxis: { type: 'category', data: g.left.map((_, i) => i * 10), name: 'ms' },
      yAxis: { type: 'value', name: '压力(kg)' },
      series: [
        { name: '左脚', type: 'line', showSymbol: false, smooth: true, data: g.left, areaStyle: { color: 'rgba(84,112,198,0.15)' } },
        { name: '右脚', type: 'line', showSymbol: false, smooth: true, data: g.right, areaStyle: { color: 'rgba(145,204,117,0.15)' } },
      ],
    }, true)
  }
}

// 受试者是否有量表字段（用于无量表资产时的回退雷达图）
const hasSubjectScaleFields = computed(() => {
  const s = subjectInfo.value || {}
  return [s.mmse_score, s.moca_score, s.ad8_score, s.cognitive_risk_level]
    .some((v) => v !== undefined && v !== null && v !== '')
})

// 清空受试者模式多量表雷达图
const clearScaleRadars = () => {
  Object.values(scaleRadarCharts).forEach((c) => {
    try { c.dispose() } catch (e) { /* 实例可能已失效 */ }
  })
  scaleRadarCharts = {}
  scaleRadarEls = {}
  if (radarChart) radarChart.setOption({ series: [] }, true)
}

// 渲染受试者模式下全部量表雷达图：
// - 有量表资产：每张量表一个雷达图（有几张展示几张）
// - 无量表资产但受试者字段有得分：回退展示受试者字段雷达图
// - 均无：清空
const renderScaleRadars = async () => {
  clearScaleRadars()
  if (scaleCards.value.length) {
    await nextTick()
    scaleCards.value.forEach((card) => {
      const el = scaleRadarEls[card.assetId]
      const summary = card.summary
      if (!el || !summary || !Array.isArray(summary.sections)) return
      const sections = summary.sections.filter((sec) => sec.max_score > 0)
      if (!sections.length) return
      // 快速切换受试者时同一 DOM 可能被多次渲染，复用已存在的实例避免重复 init 报错
      let chart = echarts.getInstanceByDom(el)
      if (!chart) chart = echarts.init(el)
      scaleRadarCharts[card.assetId] = chart
      chart.setOption({
        tooltip: { trigger: 'item' },
        radar: {
          indicator: sections.map((sec) => ({ name: sec.name, max: sec.max_score })),
          shape: 'polygon',
        },
        series: [{
          type: 'radar',
          data: [{
            value: sections.map((sec) => sec.score),
            name: `${summary.assess_name || '量表'} ${summary.total_score}/${summary.max_score}`,
          }],
          areaStyle: { color: 'rgba(84,112,198,0.15)' },
          lineStyle: { color: '#5470c6' },
          itemStyle: { color: '#5470c6' },
        }],
      })
    })
  } else if (hasSubjectScaleFields.value) {
    await nextTick()
    if (radarRef.value) {
      if (!radarChart) radarChart = echarts.init(radarRef.value)
      // scaleMeta 为 null → updateRadarChart 走受试者字段回退分支
      updateRadarChart()
    }
  } else {
    if (radarChart) radarChart.setOption({ series: [] }, true)
  }
}

const updateRadarChart = () => {
  if (!radarChart) return
  const s = subjectInfo.value || {}
  const summary = scaleMeta.value?.summary
  // 有 MoCA 量表摘要时：展示 MoCA 各分项得分雷达图
  if (summary && summary.sections && summary.sections.length > 0) {
    const sections = summary.sections.filter(sec => sec.max_score > 0)
    radarChart.setOption({
      tooltip: { trigger: 'item' },
      radar: {
        indicator: sections.map(sec => ({ name: sec.name, max: sec.max_score })),
        shape: 'polygon',
      },
      series: [{
        type: 'radar',
        data: [{
          value: sections.map(sec => sec.score),
          name: `${summary.assess_name || '量表'} ${summary.total_score}/${summary.max_score}`,
        }],
        areaStyle: { color: 'rgba(84,112,198,0.15)' },
        lineStyle: { color: '#5470c6' },
        itemStyle: { color: '#5470c6' },
      }],
    }, true)
    return
  }
  // 无量表数据时回退到 Subject 量表字段
  radarChart.setOption({
    tooltip: {},
    radar: {
      indicator: [
        { name: 'MMSE', max: 30 },
        { name: 'MoCA', max: 30 },
        { name: 'AD8', max: 8 },
        { name: '认知风险', max: 3 },
      ],
      // 各轴 max 差异大（30/30/8/3），禁用刻度对齐避免小范围轴刻度不可读
      alignTicks: false,
    },
    series: [{
      type: 'radar',
      data: [{
        value: [
          s.mmse_score ?? 0,
          s.moca_score ?? 0,
          s.ad8_score ?? 0,
          { '无': 0, '轻度': 1, '中度': 2, '重度': 3 }[s.cognitive_risk_level] ?? 0,
        ],
        name: '量表得分',
      }],
    }],
  }, true)
}

// 选择受试者后加载数据
const onSubjectChange = async () => {
  if (!selectedSubject.value) return
  const seq = ++subjectSeq
  loading.value = true
  try {
    const [overview, timeline] = await Promise.all([
      getSubjectOverviewApi(selectedSubject.value),
      getTimelineApi(selectedSubject.value),
    ])
    if (seq !== subjectSeq) return  // 已切换受试者，丢弃过期结果
    subjectInfo.value = overview.data.subject || overview.data
    tracks.value = timeline.data.tracks || []
    // 默认选中第一路视频/音频
    currentVideoId.value = videoList.value[0]?.id || null
    currentAudioId.value = audioList.value[0]?.id || null
    videoError.value = false
    videoLoading.value = !!currentVideoId.value
    // 重置解析失败标记（上次受试者的失败状态不带入本次）
    eyeParseFailed.value = false
    await nextTick()
    if (seq !== subjectSeq) return
    initCharts()
    // 检查各模态是否有真实数据
    const hasModality = (type) => tracks.value.some((t) => t.data_type === type)
    // 脑电：有真实 EEG 资产时加载解析，否则不展示
    if (hasModality('eeg')) {
      const eegAsset = tracks.value.find((t) => t.data_type === 'eeg')
      try {
        const eegRes = await getEegAssetApi(eegAsset.id)
        if (seq !== subjectSeq) return  // 过期响应丢弃
        if (eegRes.code === 200 && eegRes.data) {
          updateEegCharts(eegRes.data)
        } else {
          ElMessage.warning(eegRes.message || '脑电文件解析失败')
          eegMeta.value = null
          clearEegCharts()
        }
      } catch (e) {
        if (seq !== subjectSeq) return  // 过期响应丢弃
        const msg = e?.response?.data?.message || e?.message || '脑电文件解析失败'
        ElMessage.warning(msg)
        eegMeta.value = null
        clearEegCharts()
      }
    } else {
      eegMeta.value = null
      clearEegCharts()
    }
    // ECG：有真实资产时加载解析
    if (hasModality('ecg')) {
      const ecgAsset = tracks.value.find((t) => t.data_type === 'ecg')
      try {
        const ecgRes = await getEcgAssetApi(ecgAsset.id)
        if (seq !== subjectSeq) return  // 过期响应丢弃
        if (ecgRes.code === 200 && ecgRes.data) {
          updateEcgChart(ecgRes.data)
        } else {
          ElMessage.warning(ecgRes.message || '心电文件解析失败')
          clearEcgChart()
        }
      } catch (e) {
        if (seq !== subjectSeq) return  // 过期响应丢弃
        const msg = e?.response?.data?.message || e?.message || '心电文件解析失败'
        ElMessage.warning(msg)
        clearEcgChart()
      }
    } else {
      clearEcgChart()
    }
    // 眼动：有真实眼动资产时加载 sync_data 指标；解析失败直接展示失败状态，不回退模拟数据
    eyeMeta.value = null
    if (hasModality('eye')) {
      const eyeAsset = tracks.value.find((t) => t.data_type === 'eye')
      try {
        const eyeRes = await getEyeAssetApi(eyeAsset.id)
        if (seq !== subjectSeq) return  // 过期响应丢弃
        if (eyeRes.code === 200 && eyeRes.data) {
          eyeMeta.value = eyeRes.data
        } else {
          eyeParseFailed.value = true
          disposeEyeChart()
        }
      } catch (e) {
        if (seq !== subjectSeq) return  // 过期响应丢弃
        eyeParseFailed.value = true
        disposeEyeChart()
      }
      updateSimCharts('eye')
    } else {
      if (eyeChart) eyeChart.setOption({ series: [] }, true)
    }
    // 步态：模拟数据
    if (hasModality('gait')) {
      updateSimCharts('gait')
    } else {
      if (gaitChart) gaitChart.setOption({ series: [] }, true)
    }
    // 量表：加载该受试者全部量表资产（有几张展示几张），逐张解析摘要
    scaleCards.value = []
    clearScaleRadars()
    if (hasModality('scale')) {
      const scaleAssets = tracks.value.filter((t) => t.data_type === 'scale')
      const cards = []
      for (const a of scaleAssets) {
        let summary = null
        // 优先用 timeline 附带 metadata 中已解析的 summary（扫描/上传时已写入）
        if (a.metadata && a.metadata.summary) {
          summary = a.metadata.summary
        } else {
          // 解析失败时 summary 保持 null，卡片模板内联展示"量表数据解析失败"
          try {
            const scaleRes = await getScaleAssetApi(a.id)
            if (seq !== subjectSeq) return  // 过期响应丢弃
            if (scaleRes.code === 200 && scaleRes.data) {
              summary = scaleRes.data.summary || null
            }
          } catch (e) { /* 解析失败：summary 为 null，模板展示失败状态 */ }
        }
        cards.push({ assetId: a.id, file_name: a.file_name, summary })
      }
      if (seq === subjectSeq) scaleCards.value = cards
    }
    // 量表雷达图：多量表全部展示 / 无量表资产时回退受试者字段
    if (seq === subjectSeq) renderScaleRadars()
  } finally {
    if (seq === subjectSeq) loading.value = false
  }
}

const handleAlign = async () => {
  aligning.value = true
  try {
    await alignModalitiesApi({ subject_id: selectedSubject.value, anchor: 'trigger' })
    ElMessage.success('对齐任务已完成')
    await onSubjectChange()
  } finally {
    aligning.value = false
  }
}

// 销毁所有图表实例（切换模式/资产时 DOM 重建，旧实例需释放）
const disposeAllCharts = () => {
  // dispose 后清空 DOM 内容，移除 echarts 残留的 _echarts_instance_ 属性和 canvas
  const safeDispose = (chart, refObj) => {
    if (chart) {
      try { chart.dispose() } catch (e) { /* 实例可能已失效 */ }
    }
    if (refObj && refObj.value) {
      refObj.value.innerHTML = ''
      delete refObj.value._echarts_instance_
    }
  }
  safeDispose(radarChart, radarRef); radarChart = null
  safeDispose(eegChannelChart, eegChannelRef); eegChannelChart = null
  eegResizeObserver?.disconnect(); eegResizeObserver = null
  safeDispose(ecgChart, ecgRef); ecgChart = null
  safeDispose(eyeChart, eyeRef); eyeChart = null
  safeDispose(gaitChart, gaitRef); gaitChart = null
  // 受试者模式多量表雷达图（每个量表一个实例）
  Object.values(scaleRadarCharts).forEach((c) => {
    try { c.dispose() } catch (e) { /* 实例可能已失效 */ }
  })
  scaleRadarCharts = {}
  scaleRadarEls = {}
  riskPieChart?.dispose(); riskPieChart = null
  assetBarChart?.dispose(); assetBarChart = null
}

// 加载文件模式资产列表
const loadFileAssets = async () => {
  fileLoading.value = true
  try {
    fileAssetList.value = await fetchAllPages(getAssetsApi)
  } catch (e) {
    fileAssetList.value = []
  } finally {
    fileLoading.value = false
  }
}

// 模式切换：清空状态 + 释放图表 + 按需加载数据
const onModeChange = (mode) => {
  selectedSubject.value = null
  selectedAssetId.value = null
  tracks.value = []
  subjectInfo.value = null
  videoError.value = false
  videoLoading.value = false
  disposeAllCharts()
  if (mode === 'file') {
    loadFileAssets()
  } else {
    nextTick(() => {
      initOverviewCharts()
      updateOverviewCharts()
    })
  }
}

// 按文件模式：选中资产后渲染对应可视化
const onAssetChange = async () => {
  videoError.value = false
  videoLoading.value = false
  // 清空选择：销毁图表，重置状态
  if (!selectedAsset.value) {
    disposeAllCharts()
    subjectInfo.value = null
    return
  }
  const t = selectedAsset.value.data_type
  if (t === 'video' && selectedAsset.value.file_url) {
    videoLoading.value = true
  }
  // 设置关联受试者信息（供量表雷达图使用）
  subjectInfo.value = subjectList.value.find(
    (s) => s.id === selectedAsset.value.subject_id
  ) || null
  // 重置解析失败标记（上一个文件的失败状态不带入本次）
  eyeParseFailed.value = false
  scaleParseFailed.value = false
  // 等待 DOM 渲染（首次选文件时 <template v-else> 从不渲染变为渲染）
  await nextTick()
  // 等浏览器布局完成，确保 echarts 拿到正确的 DOM 尺寸
  await new Promise((resolve) => requestAnimationFrame(resolve))
  // 初始化图表（initCharts 跳过已存在的实例，切换文件时复用）
  initCharts()

  // 按文件模式：仅渲染与所选资产类型匹配的模态数据
  // 脑电：选中 eeg 时解析真实文件
  if (t === 'eeg') {
    try {
      const eegRes = await getEegAssetApi(selectedAsset.value.id)
      if (eegRes.code === 200 && eegRes.data) {
        updateEegCharts(eegRes.data)
      } else {
        ElMessage.warning(eegRes.message || '脑电文件解析失败')
        eegMeta.value = null
        clearEegCharts()
      }
    } catch (e) {
      const msg = e?.response?.data?.message || e?.message || '脑电文件解析失败，请检查文件格式'
      ElMessage.warning(msg)
      eegMeta.value = null
      clearEegCharts()
    }
  } else {
    eegMeta.value = null
    clearEegCharts()
  }

  // ECG：选中 ecg 时解析真实文件
  if (t === 'ecg') {
    try {
      const ecgRes = await getEcgAssetApi(selectedAsset.value.id)
      if (ecgRes.code === 200 && ecgRes.data) {
        updateEcgChart(ecgRes.data)
      } else {
        ElMessage.warning(ecgRes.message || '心电文件解析失败')
        clearEcgChart()
      }
    } catch (e) {
      const msg = e?.response?.data?.message || e?.message || '心电文件解析失败，请检查文件格式'
      ElMessage.warning(msg)
      clearEcgChart()
    }
  } else {
    clearEcgChart()
  }

  // 眼动：选中 eye 时加载真实 sync_data 指标；解析失败直接展示失败状态，不回退模拟数据
  eyeMeta.value = null
  if (t === 'eye') {
    try {
      const eyeRes = await getEyeAssetApi(selectedAsset.value.id)
      if (eyeRes.code === 200 && eyeRes.data) {
        eyeMeta.value = eyeRes.data
      } else {
        eyeParseFailed.value = true
        disposeEyeChart()
      }
    } catch (e) {
      eyeParseFailed.value = true
      disposeEyeChart()
    }
    updateSimCharts('eye')
  } else if (t === 'gait') {
    updateSimCharts('gait')
  } else {
    if (eyeChart) eyeChart.setOption({ series: [] }, true)
    if (gaitChart) gaitChart.setOption({ series: [] }, true)
  }

  // 量表：选中 scale 时加载真实 MoCA 数据；解析失败直接展示失败状态，不回退受试者字段
  scaleMeta.value = null
  if (t === 'scale') {
    try {
      const scaleRes = await getScaleAssetApi(selectedAsset.value.id)
      if (scaleRes.code === 200 && scaleRes.data?.summary) {
        scaleMeta.value = scaleRes.data
      } else {
        scaleParseFailed.value = true
        disposeRadarChart()
      }
    } catch (e) {
      scaleParseFailed.value = true
      disposeRadarChart()
    }
  }

  // 量表雷达图：量表解析失败时模板已切换为失败提示；scale/task 类型或有关联受试者时渲染
  if (scaleParseFailed.value) {
    // 已销毁实例，无需渲染
  } else if (['scale', 'task'].includes(t) || subjectInfo.value) {
    updateRadarChart()
  } else {
    clearRadarChart()
  }
}

// 清空 EEG 图表
const clearEegCharts = () => {
  if (eegChannelChart) eegChannelChart.setOption({ series: [] }, true)
}

// 清空眼动/步态图表（ECG 已由 clearEcgChart 单独管理）
const clearSimCharts = () => {
  if (eyeChart) eyeChart.setOption({ series: [] }, true)
  if (gaitChart) gaitChart.setOption({ series: [] }, true)
}

// 清空量表雷达图
const clearRadarChart = () => {
  if (radarChart) radarChart.setOption({ series: [] }, true)
}

const loadSubjects = async () => {
  try {
    const [subjects, assets] = await Promise.all([
      fetchAllPages(getSubjects),
      fetchAllPages(getAssetsApi),
    ])
    subjectList.value = subjects
    allSubjects.value = subjects
    allAssets.value = assets
  } catch (e) {
    /* 接口未就绪 */
  }
}

// 概览图表：风险分级饼图 + 模态分布柱状图
const updateOverviewCharts = () => {
  if (riskPieChart) {
    // 认知风险分级仅 无/轻度/中度/重度 四级（2026-10-06 统一）
    const counts = { unknown: 0, '无': 0, '轻度': 0, '中度': 0, '重度': 0 }
    allSubjects.value.forEach((s) => {
      const r = s.cognitive_risk_level
      if (r in counts) counts[r]++
      else counts.unknown++
    })
    const data = [
      { name: '无', value: counts['无'], itemStyle: { color: '#909399' } },
      { name: '轻度', value: counts['轻度'], itemStyle: { color: '#e6a23c' } },
      { name: '中度', value: counts['中度'], itemStyle: { color: '#f56c6c' } },
      { name: '重度', value: counts['重度'], itemStyle: { color: '#f56c6c' } },
    ]
    if (counts.unknown) data.push({ name: '未评估', value: counts.unknown, itemStyle: { color: '#909399' } })
    riskPieChart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0 },
      series: [{
        type: 'pie', radius: ['40%', '70%'], center: ['50%', '45%'],
        label: { formatter: '{b}\n{c}' },
        data,
      }],
    }, true)
  }
  if (assetBarChart) {
    // 模态分布不含步态（2026-10-06 需求）：types 是硬编码类别列表，
    // 数据源里没有 gait 资产时仍会画出 0 值空柱，故从列表中移除
    const typeText = { video: '视频', audio: '音频', eeg: '脑电', ecg: '心电', eye: '眼动', scale: '量表', task: '任务' }
    const types = ['video', 'audio', 'eeg', 'ecg', 'eye', 'scale', 'task']
    const typeColor = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#00bcd4', '#ff9800']
    const counts = types.map((t) => allAssets.value.filter((a) => a.data_type === t).length)
    assetBarChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 16, top: 24, bottom: 32 },
      xAxis: { type: 'category', data: types.map((t) => typeText[t]), axisLabel: { interval: 0 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{
        type: 'bar', barWidth: '50%',
        data: counts.map((c, i) => ({ value: c, itemStyle: { color: typeColor[i] } })),
        label: { show: true, position: 'top' },
      }],
    }, true)
  }
}

const initOverviewCharts = () => {
  if (riskPieRef.value && !riskPieChart) riskPieChart = echarts.init(riskPieRef.value)
  if (assetBarRef.value && !assetBarChart) assetBarChart = echarts.init(assetBarRef.value)
}

const handleResize = () => {
  radarChart?.resize()
  Object.values(scaleRadarCharts).forEach((c) => c?.resize())
  eegChannelChart?.resize()
  ecgChart?.resize()
  eyeChart?.resize()
  gaitChart?.resize()
  riskPieChart?.resize()
  assetBarChart?.resize()
}

onMounted(async () => {
  // 年龄/性别遮罩依赖脱敏规则；管理员若尚未加载则补一次（幂等）
  privacy.loadConfig()
  await loadSubjects()
  await nextTick()
  // 初始页：概览图表（统计卡片 + 风险分布饼图 + 模态分布柱图）
  initOverviewCharts()
  updateOverviewCharts()
  // 受试者详情页：模态图表（选中受试者后才显示）
  initCharts()
  // 从数据管理页跳转时，自动选中指定受试者
  const subjectId = route.query.subject
  if (subjectId) {
    const id = Number(subjectId)
    const exists = subjectList.value.some((s) => s.id === id)
    if (exists) {
      selectedSubject.value = id
      await onSubjectChange()
    }
  }
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  disposeAllCharts()
})
</script>

<style scoped lang="scss">
.stat-card {
  :deep(.el-card__body) {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 18px 20px;
  }
  .stat-icon {
    width: 48px;
    height: 48px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .stat-info {
    flex: 1;
    min-width: 0;
  }
  .stat-value {
    font-size: 26px;
    font-weight: 600;
    color: #303133;
    line-height: 1.2;
  }
  .stat-label {
    font-size: 13px;
    color: #909399;
    margin-top: 4px;
  }
}
.chart-label {
  font-size: 12px;
  color: #606266;
  margin-bottom: 6px;
  font-weight: 500;
}
/* 深度视频（Orbbec 深度轨转码伪彩色，像彩色视频一样播放） */
.depth-video {
  margin-top: 4px;
  .depth-video-box {
    position: relative;
    background: #000;
    border-radius: 4px;
    overflow: hidden;
  }
  .depth-video-el {
    display: block;
    width: 100%;
    aspect-ratio: 16 / 9;
    max-height: 320px;
    background: #000;
  }
}
.card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  .sub-tip {
    font-size: 12px;
    color: #909399;
  }
}
.preview-box {
  height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  color: #909399;
  p {
    margin: 0;
    font-size: 13px;
  }
}
.video-el {
  width: 100%;
  aspect-ratio: 16 / 9;
  max-height: 320px;
  background: #000;
  border-radius: 4px;
  object-fit: contain;
}
.video-wrap {
  position: relative;
}
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(0, 0, 0, 0.6);
  border-radius: 4px;
  color: #fff;
  pointer-events: none;
  p {
    margin: 0;
    font-size: 13px;
  }
}
/* 按文件模式：卡片间距与按受试者模式的 el-row gutter 保持一致 */
.file-mode {
  :deep(.el-card) {
    margin-bottom: 16px;
  }
  :deep(.el-card):last-child {
    margin-bottom: 0;
  }
}
</style>
