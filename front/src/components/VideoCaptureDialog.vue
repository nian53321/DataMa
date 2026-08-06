<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="视频采集"
    width="1280px"
    top="4vh"
    :close-on-click-modal="false"
    @open="onOpen"
    @closed="onClosed"
  >
    <!-- 受试者信息 + 三类视频采集状态 -->
    <div class="subject-bar">
      <el-tag type="info">受试者</el-tag>
      <span style="margin-left: 8px; font-weight: 600">{{ subject?.pseudo_id || '—' }}</span>
      <span style="margin-left: 12px; color: #909399">{{ subject?.name || '匿名' }}</span>
      <el-divider direction="vertical" />
      <span class="video-status-label">视频采集进度：</span>
      <el-tag
        v-for="t in VIDEO_TYPE_OPTIONS"
        :key="t.value"
        :type="hasVideoType(t.value) ? 'success' : 'info'"
        size="small"
        effect="plain"
        style="margin-right: 6px"
      >
        {{ t.label }}：{{ hasVideoType(t.value) ? '已采' : '未采' }}
      </el-tag>
    </div>

    <!-- 视频类型选择 -->
    <div class="type-bar">
      <span class="type-label">采集类型：</span>
      <el-radio-group v-model="currentVideoType" size="small">
        <el-radio-button
          v-for="t in VIDEO_TYPE_OPTIONS"
          :key="t.value"
          :label="t.value"
        >
          {{ t.label }}
        </el-radio-button>
      </el-radio-group>
      <span v-if="hasVideoType(currentVideoType)" style="margin-left: 12px; color: #e6a23c; font-size: 12px">
        该类型已有视频，本次采集将替换旧视频
      </span>
    </div>

    <!-- 设备源选择：普通摄像头 / Orbbec 深度相机 -->
    <div class="source-bar">
      <span class="type-label">设备源：</span>
      <el-radio-group v-model="deviceSource" size="small" @change="onDeviceSourceChange">
        <el-radio-button label="webcam">普通摄像头</el-radio-button>
        <el-radio-button label="orbbec" :disabled="!orbbecAvailable">
          Orbbec 深度相机
        </el-radio-button>
        <el-radio-button label="realsense" :disabled="!realSenseAvailable">
          RealSense 深度相机
        </el-radio-button>
      </el-radio-group>
      <el-tag v-if="deviceSource === 'orbbec'" size="small" type="success" style="margin-left: 8px">
        序列号：{{ orbbecSerial || '—' }}
      </el-tag>
      <el-tag v-if="deviceSource === 'realsense'" size="small" type="success" style="margin-left: 8px">
        序列号：{{ realSenseSerial || '—' }}
      </el-tag>
      <el-tag v-if="orbbecChecked && !orbbecAvailable" size="small" type="info" style="margin-left: 8px">
        Orbbec 深度相机未检测到
      </el-tag>
      <el-tag v-if="realSenseChecked && !realSenseAvailable" size="small" type="info" style="margin-left: 8px">
        RealSense 深度相机未检测到
      </el-tag>
      <!-- 普通摄像头：设备选择（深度相机 RGB 亦作为普通摄像头列出） -->
      <template v-if="deviceSource === 'webcam'">
        <el-divider direction="vertical" />
        <span class="type-label">摄像头：</span>
        <el-select
          v-model="selectedDeviceId"
          size="small"
          style="width: 280px"
          placeholder="选择摄像头"
          :disabled="phase === 'recording' || phase === 'paused'"
          @change="onDeviceChange"
        >
          <el-option
            v-for="d in cameraOptions"
            :key="d.deviceId"
            :label="cameraLabel(d)"
            :value="d.deviceId"
          />
        </el-select>
      </template>
      <!-- Orbbec 模式：彩色+深度同时显示（上下拼接，不再单选切换） -->
      <template v-if="deviceSource === 'orbbec'">
        <el-divider direction="vertical" />
        <span class="type-label">画面：</span>
        <span style="font-size: 13px; color: #606266">彩色 + 深度（同时显示）</span>
      </template>
    </div>

    <!-- 视频区 -->
    <div
      class="video-stage"
      :class="{ 'is-orbbec': deviceSource === 'orbbec', 'is-realsense': deviceSource === 'realsense' }"
    >
      <!-- 普通摄像头：video 元素 -->
      <video
        v-if="deviceSource === 'webcam'"
        ref="videoRef"
        class="video-el"
        :class="{ recording: phase === 'recording', paused: phase === 'paused' }"
        :controls="canSeek"
        :autoplay="!canSeek"
        muted
        playsinline
      />

      <!-- Orbbec 深度相机：采集前与录制中显示实时画面(MJPEG 流)，完成后播放预览视频 -->
      <template v-else-if="deviceSource === 'orbbec'">
        <!-- 采集前/录制中：实时预览画面（录制中与录制共用相机会话，画面不断流） -->
        <img
          v-if="(phase === 'idle' || phase === 'recording') && orbbecLiveUrl"
          :src="orbbecLiveUrl"
          class="video-el"
          alt="实时预览"
        />
        <!-- 录制完成/裁剪中：播放预览视频（后端从 mkv 提取的 H.264 彩色轨；editing 态必须保留元素供裁剪源绘制） -->
        <video
          v-else-if="(phase === 'done' || phase === 'editing') && orbbecPreviewUrl"
          ref="orbbecVideoRef"
          :src="orbbecPreviewUrl"
          class="video-el"
          controls
          autoplay
          muted
          loop
        />
        <div v-else class="placeholder">
          <el-icon v-if="orbbecStarting" class="is-loading" :size="40"><Loading /></el-icon>
          <el-icon v-else :size="48"><VideoCamera /></el-icon>
          <div style="margin-top: 12px; color: #909399">
            {{ orbbecStarting ? '正在启动摄像头，请稍候几秒…' : orbbecMessage }}
          </div>
        </div>
      </template>

      <!-- RealSense 深度相机：采集前与录制中显示实时画面(MJPEG 流)，完成后播放预览视频 -->
      <template v-else-if="deviceSource === 'realsense'">
        <!-- 采集前/录制中：实时预览画面（录制中与录制共用相机会话，画面不断流） -->
        <img
          v-if="(phase === 'idle' || phase === 'recording') && realSenseLiveUrl"
          :src="realSenseLiveUrl"
          class="video-el"
          alt="实时预览"
        />
        <!-- 录制完成：播放预览视频（后端提取的 H.264 彩色轨） -->
        <video
          v-else-if="phase === 'done' && realSensePreviewUrl"
          :src="realSensePreviewUrl"
          class="video-el"
          controls
          autoplay
          muted
          loop
        />
        <div v-else class="placeholder">
          <el-icon v-if="realSenseStarting" class="is-loading" :size="40"><Loading /></el-icon>
          <el-icon v-else :size="48"><VideoCamera /></el-icon>
          <div style="margin-top: 12px; color: #909399">
            {{ realSenseStarting ? '正在启动摄像头，请稍候几秒…' : realSenseMessage }}
          </div>
        </div>
      </template>

      <!-- 录制指示器 -->
      <div v-if="phase === 'recording' || phase === 'paused'" class="rec-indicator">
        <span class="rec-dot" :class="{ blink: phase === 'recording' }" />
        <span style="margin-left: 6px">{{ phase === 'recording' ? '录制中' : '已暂停' }}</span>
        <span style="margin-left: 12px; font-variant-numeric: tabular-nums">{{ formattedDuration }}</span>
      </div>

      <!-- 未授权占位 -->
      <div v-if="phase === 'idle' && !stream && deviceSource === 'webcam'" class="placeholder">
        <el-icon :size="48"><VideoCamera /></el-icon>
        <div style="margin-top: 12px; color: #909399">{{ initMessage }}</div>
      </div>
    </div>

    <!-- 控制区 -->
    <div class="controls">
      <!-- Orbbec 模式 -->
      <template v-if="deviceSource === 'orbbec'">
        <template v-if="phase === 'idle'">
          <el-button
            type="danger"
            :icon="VideoPlay"
            :disabled="!orbbecAvailable"
            @click="startOrbbecRecord"
          >开始采集</el-button>
        </template>
        <template v-else-if="phase === 'recording'">
          <el-button type="warning" :icon="CircleClose" :loading="stoppingOrbbec" @click="stopOrbbecRecord">停止采集</el-button>
        </template>
        <template v-else-if="phase === 'done' || phase === 'editing'">
          <span style="color: #909399; font-size: 13px; margin-right: 8px">
            {{ sourceLabel }}：{{ formatTime(orbbecRecordMeta?.duration_sec || 0) }}
          </span>
          <el-button :icon="RefreshLeft" @click="resetOrbbecCapture">重新采集</el-button>
          <el-button
            v-if="phase === 'done'"
            type="warning"
            :icon="Scissor"
            :disabled="!orbbecPreviewReady"
            @click="enterOrbbecEditMode"
          >裁剪片段</el-button>
          <el-button
            v-if="phase === 'editing'"
            :icon="RefreshLeft"
            @click="exitOrbbecEditMode"
          >退出裁剪</el-button>
          <el-button
            v-if="phase === 'editing'"
            type="success"
            :icon="Check"
            :disabled="trimming"
            :loading="trimming"
            @click="applyOrbbecTrim"
          >应用裁剪</el-button>
          <el-button type="primary" :icon="Upload" :loading="uploading" @click="uploadOrbbecRecord">
            {{ hasVideoType(currentVideoType) ? '替换上传' : '上传保存' }}
          </el-button>
        </template>
      </template>

      <!-- RealSense 模式（精简版：无裁剪，仅采集/预览/上传） -->
      <template v-else-if="deviceSource === 'realsense'">
        <template v-if="phase === 'idle'">
          <el-button
            type="danger"
            :icon="VideoPlay"
            :disabled="!realSenseAvailable"
            @click="startRealSenseRecord"
          >开始采集</el-button>
        </template>
        <template v-else-if="phase === 'recording'">
          <el-button type="warning" :icon="CircleClose" :loading="stoppingRealSense" @click="stopRealSenseRecord">停止采集</el-button>
        </template>
        <template v-else-if="phase === 'done'">
          <span style="color: #909399; font-size: 13px; margin-right: 8px">
            {{ sourceLabel }}：{{ formatTime(realSenseRecordMeta?.duration_sec || 0) }}
          </span>
          <el-button :icon="RefreshLeft" @click="resetRealSenseCapture">重新采集</el-button>
          <el-button
            type="primary"
            :icon="Upload"
            :loading="uploading"
            :disabled="!realSensePreviewReady"
            @click="uploadRealSenseRecord"
          >{{ hasVideoType(currentVideoType) ? '替换上传' : '上传保存' }}</el-button>
        </template>
      </template>

      <!-- 普通摄像头模式（原有逻辑） -->
      <template v-else>
        <!-- 空闲态：有摄像头 -->
        <template v-if="phase === 'idle' && stream">
          <el-button type="danger" :icon="VideoPlay" @click="startRecording">开始采集</el-button>
        </template>

        <!-- 空闲态：无摄像头（提供备选方案） -->
        <template v-else-if="phase === 'idle' && !stream">
          <el-button :icon="Upload" @click="pickVideoFile">选择视频文件</el-button>
          <el-button type="primary" :icon="Refresh" @click="initCamera">重试获取摄像头</el-button>
        </template>

        <!-- 录制中 / 暂停态 -->
        <template v-else-if="phase === 'recording' || phase === 'paused'">
          <el-button
            v-if="phase === 'recording'"
            :icon="VideoPause"
            @click="pauseRecording"
          >暂停</el-button>
          <el-button
            v-else
            type="success"
            :icon="VideoPlay"
            @click="resumeRecording"
          >继续</el-button>
          <el-button type="warning" :icon="CircleClose" @click="stopRecording">停止采集</el-button>
        </template>

        <!-- 已完成态：预览 + 裁剪 + 上传 -->
        <template v-else-if="phase === 'done' || phase === 'editing'">
          <span style="color: #909399; font-size: 13px; margin-right: 8px">
            {{ sourceLabel }}：{{ formatTime(currentDurationSec) }}（{{ formatFileSize(blobSize) }}）
          </span>
          <el-button :icon="RefreshLeft" @click="resetCapture">{{ stream ? '重新采集' : '重新选择' }}</el-button>
          <el-button
            v-if="phase === 'done'"
            type="warning"
            :icon="Scissor"
            @click="enterEditMode"
          >裁剪片段</el-button>
          <el-button
            v-if="phase === 'editing'"
            :icon="RefreshLeft"
            @click="exitEditMode"
          >退出裁剪</el-button>
          <el-button
            v-if="phase === 'editing'"
            type="success"
            :icon="Check"
            :disabled="trimming"
            :loading="trimming"
            @click="applyTrim"
          >应用裁剪</el-button>
          <el-button type="primary" :icon="Upload" :loading="uploading" @click="handleUpload">
            {{ hasVideoType(currentVideoType) ? '替换上传' : '上传保存' }}
          </el-button>
        </template>
      </template>

      <div style="flex: 1" />

      <!-- 上传进度 -->
      <span v-if="uploadProgress > 0 && uploading" style="color: #409eff; font-size: 12px">
        上传中 {{ uploadProgress }}%
      </span>
      <span v-if="trimming" style="color: #e6a23c; font-size: 12px">
        裁剪处理中 {{ trimProgress }}%
      </span>
    </div>

    <!-- 裁剪控制区 -->
    <div v-if="phase === 'editing'" class="trim-panel">
      <div class="trim-row">
        <span class="trim-label">起始时间：</span>
        <el-input-number
          v-model="trimStartSec"
          :min="0"
          :max="Math.max(0, currentDurationSec - 0.1)"
          :step="0.5"
          size="small"
          style="width: 120px"
          controls-position="right"
        />
        <el-button size="small" link type="primary" @click="setTrimStartToCurrent">
          <el-icon><Aim /></el-icon> 定位
        </el-button>
      </div>
      <div class="trim-row">
        <span class="trim-label">结束时间：</span>
        <el-input-number
          v-model="trimEndSec"
          :min="trimStartSec + 0.1"
          :max="currentDurationSec"
          :step="0.5"
          size="small"
          style="width: 120px"
          controls-position="right"
        />
        <el-button size="small" link type="primary" @click="setTrimEndToCurrent">
          <el-icon><Aim /></el-icon> 定位
        </el-button>
      </div>
      <div class="trim-row trim-info">
        <span>裁剪后时长：<b>{{ formatTime(trimEndSec - trimStartSec) }}</b></span>
        <el-button size="small" link type="primary" @click="previewTrim">预览片段</el-button>
        <el-button size="small" link @click="resetTrimRange">重置范围</el-button>
      </div>
      <div class="trim-hint">
        提示：拖动视频时间轴到目标位置后点"定位"可快速设置起止时间；"预览片段"将播放选定区间。
      </div>
    </div>

    <!-- 隐藏的文件选择 input -->
    <input
      ref="fileInputRef"
      type="file"
      accept="video/*"
      style="display: none"
      @change="onVideoFileChange"
    />

    <!-- 裁剪用的 canvas（隐藏） -->
    <canvas ref="canvasRef" style="display: none" />

    <!-- 上传进度条 -->
    <el-progress
      v-if="uploading"
      :percentage="uploadProgress"
      :stroke-width="6"
      style="margin-top: 8px"
    />
  </el-dialog>
</template>

<script setup>
import { ref, computed, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import {
  VideoPlay, VideoPause, CircleClose, RefreshLeft, Upload, Refresh,
  Scissor, Check, Aim, Loading,
} from '@element-plus/icons-vue'
import { VideoCamera } from '@element-plus/icons-vue'
import { uploadAssetApi } from '@/api/data'
import {
  getOrbbecStatusApi, startOrbbecRecordApi, stopOrbbecRecordApi,
  uploadOrbbecRecordApi, getOrbbecRecordStatusApi,
  startOrbbecPreviewApi, stopOrbbecPreviewApi,
} from '@/api/orbbec'
import {
  getRealSenseStatusApi, startRealSenseRecordApi, stopRealSenseRecordApi,
  uploadRealSenseRecordApi, getRealSenseRecordStatusApi,
  startRealSensePreviewApi, stopRealSensePreviewApi,
} from '@/api/realsense'

// 视频类型定义（与后端 VIDEO_TYPES 对应）
const VIDEO_TYPE_OPTIONS = [
  { value: 'face', label: '面部' },
  { value: 'body', label: '身体' },
  { value: 'gait', label: '步态' },
]

const props = defineProps({
  modelValue: Boolean,
  subject: Object,
})
const emit = defineEmits(['update:modelValue', 'success'])

// ==================== 状态 ====================
// phase: idle → recording → paused → recording → done → editing → done → (reset → idle)
const phase = ref('idle')           // idle | recording | paused | done | editing
const stream = ref(null)            // 摄像头流
const videoRef = ref(null)           // video 元素
const fileInputRef = ref(null)       // 文件选择 input
const canvasRef = ref(null)          // 裁剪用 canvas
const initMessage = ref('正在请求摄像头权限...')
const sourceLabel = ref('录制完成')  // 录制完成 / 已选文件 / 裁剪片段

// 设备源：webcam（普通摄像头）/ orbbec（Orbbec 深度相机）
const deviceSource = ref('webcam')
// 普通摄像头设备列表（enumerateDevices 的 videoinput，含深度相机 RGB，仅作普通摄像头使用）
const cameraDevices = ref([])
const selectedDeviceId = ref('')
// Orbbec 设备状态
const orbbecAvailable = ref(false)
const orbbecChecked = ref(false)
const orbbecSerial = ref('')
const orbbecMessage = ref('正在检测深度相机...')
// Orbbec 录制状态
const orbbecRecordMeta = ref(null)      // { path, meta: {duration_sec, device_serial, ...} }
const orbbecPreviewReady = ref(false)   // 预览视频是否已生成(录制后后台异步生成)
const orbbecVideoRef = ref(null)        // Orbbec 录制完成后预览视频元素(裁剪用)
const orbbecTrimmedUrl = ref('')        // 裁剪后的预览(本地 blob,仅预览展示,上传仍用原始 mkv)
const stoppingOrbbec = ref(false)       // 停止采集中的 loading 状态
const orbbecPreviewing = ref(false)     // 实时预览会话是否已启动(MJPEG 流)
let orbbecRecordTimerId = null
// RealSense 设备状态
const realSenseAvailable = ref(false)
const realSenseChecked = ref(false)
const realSenseSerial = ref('')
const realSenseMessage = ref('正在检测深度相机...')
// RealSense 录制状态
const realSenseRecordMeta = ref(null)      // { path, preview_rel, meta, duration_sec }
const realSensePreviewReady = ref(false)   // 预览视频是否已生成(录制后后台异步生成)
const stoppingRealSense = ref(false)       // 停止采集中的 loading 状态
const realSensePreviewing = ref(false)     // 实时预览会话是否已启动(MJPEG 流)
let realSenseRecordTimerId = null

// 当前采集的视频类型：face / body / gait（默认 face）
const currentVideoType = ref('face')

// 判断指定类型是否已采集（来自父组件传入的 subject.video_types）
const hasVideoType = (t) => {
  const vtypes = props.subject?.video_types || []
  return Array.isArray(vtypes) && vtypes.includes(t)
}

let mediaRecorder = null
let chunks = []
let startTime = 0
let elapsedBeforePause = 0           // 暂停前累计已录时长（ms）
const durationMs = ref(0)            // 当前总录制时长（ms）
const currentDurationSec = ref(0)   // 当前 video.duration（秒，仅预览阶段使用）
let timerId = null

// 录制结果
const blobSize = ref(0)
let recordedBlob = null

// 上传
const uploading = ref(false)
const uploadProgress = ref(0)

// 裁剪
const trimStartSec = ref(0)
const trimEndSec = ref(0)
const trimming = ref(false)
const trimProgress = ref(0)
const orbbecTrimmed = ref(false)  // 是否已裁剪（上传时把裁剪区间传给后端对 mkv 双轨生效）

// ==================== 计算属性 ====================
const formattedDuration = computed(() => formatTime(durationMs.value / 1000))
// 仅在已完成/编辑态显示原生控制器（时间轴），录制中不显示
const canSeek = computed(() => phase.value === 'done' || phase.value === 'editing')

// ==================== 生命周期 ====================
const onOpen = async () => {
  // 并行检测两种深度相机（互不依赖，各自后端 status 均已子进程化、有超时），
  // 避免串行等待拖慢弹窗打开/页面卡顿
  await Promise.all([checkOrbbecStatus(), checkRealSenseStatus()])
  // 记住上次设备源：上传成功后界面关闭、相机可能仍开着，
  // 重开时若上次用 Orbbec 且设备可用，自动恢复并直接出画面（无需手动切换）
  const lastSource = localStorage.getItem('orbbec_last_source') || 'webcam'
  if (lastSource === 'orbbec' && orbbecAvailable.value) {
    deviceSource.value = 'orbbec'
    if (orbbecBackendPreviewing.value) {
      // 后端相机会话仍开着（上次 stop 被忽略/未执行），直接复用，秒出画面
      orbbecPreviewing.value = true
    } else {
      startOrbbecLive()
    }
    return
  }
  await initCamera()
}

const onClosed = () => {
  releaseCamera()
  releaseOrbbec()
  releaseRealSense()
  resetState()
}

onBeforeUnmount(() => {
  releaseCamera()
  releaseOrbbec()
  releaseRealSense()
})

// ==================== Orbbec 设备管理 ====================
const orbbecBackendPreviewing = ref(false)  // 后端相机会话是否还开着（status.previewing）
const checkOrbbecStatus = async () => {
  orbbecChecked.value = false
  orbbecMessage.value = '正在检测深度相机...'
  try {
    const res = await getOrbbecStatusApi()
    const d = res.data || {}
    // available=true 仅代表后端可用；count>0 才代表接上了相机
    orbbecAvailable.value = !!d.available && (d.count || 0) > 0
    orbbecSerial.value = d.serial || ''
    // 仅容器内直连（无宿主机降级），后端恒返回 container 模式
    orbbecBackendPreviewing.value = !!d.previewing
    if (orbbecAvailable.value) {
      orbbecMessage.value = `已检测到深度相机（序列号：${d.serial || '—'}）`
    } else if (d.available && (d.count || 0) === 0) {
      orbbecMessage.value = '深度相机驱动正常，但未检测到设备（请检查 USB 连接）'
    } else {
      orbbecMessage.value = d.message || '未检测到深度相机'
    }
  } catch (e) {
    orbbecAvailable.value = false
    orbbecMessage.value = '深度相机检测失败'
  } finally {
    orbbecChecked.value = true
  }
}

// ==================== RealSense 设备管理 ====================
const checkRealSenseStatus = async () => {
  realSenseChecked.value = false
  realSenseMessage.value = '正在检测深度相机...'
  try {
    const res = await getRealSenseStatusApi()
    const d = res.data || {}
    // available=true 仅代表 pyrealsense2 库可用；count>0 才代表真正接上了相机
    realSenseAvailable.value = !!d.available && (d.count || 0) > 0
    realSenseSerial.value = d.serial || ''
    if (realSenseAvailable.value) {
      realSenseMessage.value = `已检测到深度相机（型号：${d.name || 'RealSense'}，序列号：${d.serial || '—'}）`
    } else if (d.available && (d.count || 0) === 0) {
      realSenseMessage.value = '深度相机驱动正常，但未检测到设备（请检查 USB 连接）'
    } else {
      realSenseMessage.value = d.message || '未检测到深度相机'
    }
  } catch (e) {
    realSenseAvailable.value = false
    realSenseMessage.value = '深度相机检测失败'
  } finally {
    realSenseChecked.value = true
  }
}

// 切换设备源
const onDeviceSourceChange = (val) => {
  // 切到 orbbec 时若不可用，回退 webcam
  if (val === 'orbbec' && !orbbecAvailable.value) {
    ElMessage.warning('未检测到深度相机，已回退普通摄像头')
    deviceSource.value = 'webcam'
    return
  }
  // 切到 realsense 时若不可用，回退 webcam
  if (val === 'realsense' && !realSenseAvailable.value) {
    ElMessage.warning('未检测到 RealSense 深度相机，已回退普通摄像头')
    deviceSource.value = 'webcam'
    return
  }
  // 记住当前设备源，下次打开对话框自动恢复
  localStorage.setItem('orbbec_last_source', val)
  resetState()
  if (val === 'orbbec') {
    // 深度模式：启动实时预览(MJPEG 流)。切回普通摄像头时停止预览释放设备
    stopRealSenseLive()
    releaseCamera()
    startOrbbecLive()
  } else if (val === 'realsense') {
    // RealSense 模式：与 orbbec 相同，启动实时预览(MJPEG 流)
    stopOrbbecLive()
    releaseCamera()
    startRealSenseLive()
  } else {
    stopOrbbecLive()
    stopRealSenseLive()
    selectedDeviceId.value = ''
    setTimeout(() => initCamera(), 600)
  }
}

const releaseOrbbec = () => {
  if (orbbecRecordTimerId) {
    clearInterval(orbbecRecordTimerId)
    orbbecRecordTimerId = null
  }
  stopOrbbecLive()
  // 录制中关闭弹窗：通知后端停止录制，避免相机会话/录制进程泄漏
  if (phase.value === 'recording') {
    stopOrbbecRecordApi().catch(() => {})
  }
}

const releaseRealSense = () => {
  if (realSenseRecordTimerId) {
    clearInterval(realSenseRecordTimerId)
    realSenseRecordTimerId = null
  }
  stopRealSenseLive()
  // 录制中关闭弹窗：通知后端停止录制子进程，避免进程泄漏
  if (phase.value === 'recording') {
    stopRealSenseRecordApi().catch(() => {})
  }
}

// 录制完成后的预览视频 URL(后端从 mkv 提取的彩色轨 mp4;裁剪后优先显示本地裁剪版)
const orbbecPreviewUrl = computed(() => {
  if (orbbecTrimmedUrl.value) return orbbecTrimmedUrl.value
  const rel = orbbecRecordMeta.value?.preview_rel
  if (!rel) return ''
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = localStorage.getItem('token') || ''
  return `${base}/orbbec/preview?path=${encodeURIComponent(rel)}&access_token=${encodeURIComponent(token)}&_t=${Date.now()}`
})

// 实时预览 MJPEG 流 URL(<img> 直接播放;img 无法带请求头,鉴权走 access_token query)
const orbbecLiveUrl = computed(() => {
  if (!orbbecPreviewing.value) return ''
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = localStorage.getItem('token') || ''
  return `${base}/orbbec/preview/stream?access_token=${encodeURIComponent(token)}&_t=${Date.now()}`
})

// ==================== RealSense 计算属性 ====================
// 录制完成后的预览视频 URL(后端提取的 H.264 彩色轨 mp4)
const realSensePreviewUrl = computed(() => {
  const rel = realSenseRecordMeta.value?.preview_rel
  if (!rel) return ''
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = localStorage.getItem('token') || ''
  return `${base}/realsense/preview?path=${encodeURIComponent(rel)}&access_token=${encodeURIComponent(token)}&_t=${Date.now()}`
})

// 实时预览 MJPEG 流 URL(<img> 直接播放;img 无法带请求头,鉴权走 access_token query)
const realSenseLiveUrl = computed(() => {
  if (!realSensePreviewing.value) return ''
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  const token = localStorage.getItem('token') || ''
  return `${base}/realsense/preview/stream?access_token=${encodeURIComponent(token)}&_t=${Date.now()}`
})

// 启动实时预览会话(与录制共用相机会话;开始采集无需停预览)
let orbbecStartSeq = 0          // 竞态序号:防止"start 返回时已被 stop"导致状态错乱
const orbbecStarting = ref(false)  // 相机启动中(首次启动约需几秒),用于画面区提示
const startOrbbecLive = async () => {
  const seq = ++orbbecStartSeq
  if (orbbecPreviewing.value) return
  orbbecStarting.value = true
  try {
    await startOrbbecPreviewApi()
    if (seq !== orbbecStartSeq) return  // 期间被停止/重开,以后端状态为准
    orbbecPreviewing.value = true
  } catch (e) {
    if (seq !== orbbecStartSeq) return
    orbbecPreviewing.value = false
    ElMessage.warning(e?.response?.data?.message || '实时预览启动失败')
  } finally {
    if (seq === orbbecStartSeq) orbbecStarting.value = false
  }
}

// 停止实时预览会话,释放设备
const stopOrbbecLive = async () => {
  ++orbbecStartSeq
  if (!orbbecPreviewing.value) return
  orbbecPreviewing.value = false
  try { await stopOrbbecPreviewApi() } catch { /* 忽略 */ }
}

// ==================== RealSense 实时预览 ====================
// 启动实时预览会话(与录制共用相机会话;开始采集无需停预览)
let realSenseStartSeq = 0          // 竞态序号:防止"start 返回时已被 stop"导致状态错乱
const realSenseStarting = ref(false)  // 相机启动中(首次启动约需几秒),用于画面区提示
const startRealSenseLive = async () => {
  const seq = ++realSenseStartSeq
  if (realSensePreviewing.value) return
  realSenseStarting.value = true
  try {
    await startRealSensePreviewApi()
    if (seq !== realSenseStartSeq) return  // 期间被停止/重开,以后端状态为准
    realSensePreviewing.value = true
  } catch (e) {
    if (seq !== realSenseStartSeq) return
    realSensePreviewing.value = false
    ElMessage.warning(e?.response?.data?.message || '实时预览启动失败')
  } finally {
    if (seq === realSenseStartSeq) realSenseStarting.value = false
  }
}

// 停止实时预览会话,释放设备
const stopRealSenseLive = async () => {
  ++realSenseStartSeq
  if (!realSensePreviewing.value) return
  realSensePreviewing.value = false
  try { await stopRealSensePreviewApi() } catch { /* 忽略 */ }
}

// ==================== Orbbec 录制 ====================
const startOrbbecRecord = async () => {
  if (!orbbecAvailable.value) {
    ElMessage.warning('深度相机未就绪')
    return
  }
  // 乐观进入录制中：相机已在实时预览会话上运行，record/start 秒开
  phase.value = 'recording'
  sourceLabel.value = '录制完成'
  durationMs.value = 0
  orbbecRecordMeta.value = null
  orbbecPreviewReady.value = false
  orbbecTrimmedUrl.value = ''
  orbbecTrimmed.value = false
  try {
    await startOrbbecRecordApi({
      color_res: '1080P',
      depth_mode: 'NFOV_UNBINNED',
      fps: 30,
    })
    // 计时器
    const startTs = Date.now()
    if (orbbecRecordTimerId) clearInterval(orbbecRecordTimerId)
    orbbecRecordTimerId = setInterval(() => {
      durationMs.value = Date.now() - startTs
    }, 200)
    ElMessage.success('录制已开始')
  } catch (e) {
    phase.value = 'idle'
    ElMessage.error(e?.response?.data?.message || '启动录制失败')
  }
}

const stopOrbbecRecord = async () => {
  if (stoppingOrbbec.value) return
  stoppingOrbbec.value = true
  if (orbbecRecordTimerId) {
    clearInterval(orbbecRecordTimerId)
    orbbecRecordTimerId = null
  }
  try {
    const res = await stopOrbbecRecordApi()
    orbbecRecordMeta.value = res.data
    // 后端录制结束已自动关闭相机会话（SDK k4a.stop()），实时预览流随之断开
    orbbecPreviewing.value = false
    phase.value = 'done'
    ElMessage.success('录制已停止，正在生成预览...')
    // 预览由后端 ffmpeg 后台生成，轮询就绪后显示
    pollOrbbecPreview()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '停止录制失败')
    phase.value = 'idle'
  } finally {
    stoppingOrbbec.value = false
  }
}

// 轮询录制后处理状态（预览生成），最多约 90s
const pollOrbbecPreview = async () => {
  orbbecPreviewReady.value = false
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 1500))
    try {
      const res = await getOrbbecRecordStatusApi()
      const d = res.data || {}
      if (d.done) {
        if (d.preview_rel && orbbecRecordMeta.value) {
          orbbecRecordMeta.value.preview_rel = d.preview_rel
          // 后台剥离 IR 后原 mkv 被删除，path 更新为最终 *_cd.mkv（上传用）
          if (d.mkv) orbbecRecordMeta.value.path = d.mkv
          if (d.meta) {
            orbbecRecordMeta.value.meta = d.meta
            // 时长优先用 ffprobe 读出的预览视频真实时长（与播放器一致）；
            // 无则回退开始/结束时间差（该差值包含录制器启动/保存开销，会偏大）
            if (d.meta.duration_sec && isFinite(d.meta.duration_sec)) {
              orbbecRecordMeta.value.duration_sec = d.meta.duration_sec
            } else if (d.meta.start_time && d.meta.end_time) {
              const s = new Date(d.meta.start_time).getTime()
              const e = new Date(d.meta.end_time).getTime()
              if (e > s) orbbecRecordMeta.value.duration_sec = Math.round((e - s) / 1000)
            }
          }
          orbbecPreviewReady.value = true
          ElMessage.success('预览已生成')
        } else {
          orbbecPreviewReady.value = false
          ElMessage.warning('预览生成失败，仍可上传录制数据')
        }
        return
      }
    } catch { /* 继续轮询 */ }
  }
  ElMessage.warning('预览生成超时，仍可上传录制数据')
}

const resetOrbbecCapture = () => {
  if (orbbecTrimmedUrl.value) {
    URL.revokeObjectURL(orbbecTrimmedUrl.value)
    orbbecTrimmedUrl.value = ''
  }
  orbbecTrimmed.value = false
  orbbecRecordMeta.value = null
  orbbecPreviewReady.value = false
  durationMs.value = 0
  phase.value = 'idle'
  // 相机会话已在录制结束时自动关闭，重新采集需重新启动实时预览
  startOrbbecLive()
}

// ==================== Orbbec 预览裁剪（仅影响预览展示，上传仍用原始 mkv） ====================
const enterOrbbecEditMode = () => {
  if (!orbbecVideoRef.value) {
    ElMessage.warning('预览视频未就绪，暂不支持裁剪')
    return
  }
  phase.value = 'editing'
  const v = orbbecVideoRef.value
  v.loop = false
  v.pause()
  const d = v.duration
  if (d && isFinite(d)) {
    trimStartSec.value = 0
    trimEndSec.value = Math.floor(d * 10) / 10
    currentDurationSec.value = d
  }
}

const exitOrbbecEditMode = () => {
  phase.value = 'done'
  const v = orbbecVideoRef.value
  if (v) {
    v.loop = true
    v.currentTime = 0
    v.play().catch(() => {})
  }
}

const applyOrbbecTrim = async () => {
  const v = orbbecVideoRef.value
  if (!v) return
  const start = trimStartSec.value
  const end = trimEndSec.value
  if (!start && !end) { ElMessage.warning('请先设置裁剪范围'); return }
  if (end - start < 0.1) { ElMessage.warning('裁剪区间过短'); return }
  trimming.value = true
  trimProgress.value = 0
  try {
    const canvas = canvasRef.value
    const w = v.videoWidth || 1280
    const h = v.videoHeight || 720
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    const canvasStream = canvas.captureStream(30)
    const mime = getSupportedMime() || 'video/webm'
    const rec = new MediaRecorder(canvasStream, { mimeType: mime })
    const parts = []
    rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) parts.push(e.data) }
    rec.onstop = () => {
      const blob = new Blob(parts, { type: mime })
      if (orbbecTrimmedUrl.value) URL.revokeObjectURL(orbbecTrimmedUrl.value)
      orbbecTrimmedUrl.value = URL.createObjectURL(blob)
      orbbecTrimmed.value = true  // 标记已裁剪：上传时后端按此区间裁剪 mkv（彩色+深度同步）
      phase.value = 'done'
      trimming.value = false
      trimProgress.value = 100
      ElMessage.success(`已裁剪 ${formatTime(end - start)} 片段（上传时将生效）`)
    }
    v.pause()
    v.loop = false
    await seekTo(v, start)
    rec.start(200)
    v.play().catch(() => {})
    const t0 = Date.now()
    const totalMs = (end - start) * 1000
    const onTimeUpdate = () => {
      if (v.currentTime >= end - 0.05) {
        v.pause()
        v.removeEventListener('timeupdate', onTimeUpdate)
        if (rec.state !== 'inactive') rec.stop()
        canvasStream.getTracks().forEach(t => t.stop())
        return
      }
      ctx.drawImage(v, 0, 0, w, h)
      trimProgress.value = Math.min(99, Math.round(((Date.now() - t0) / totalMs) * 100))
    }
    v.addEventListener('timeupdate', onTimeUpdate)
    const drawFrame = () => {
      if (!v.paused) ctx.drawImage(v, 0, 0, w, h)
      if (rec.state === 'recording') requestAnimationFrame(drawFrame)
    }
    requestAnimationFrame(drawFrame)
  } catch (e) {
    trimming.value = false
    ElMessage.error(`裁剪失败：${e.message || e}`)
  }
}

const uploadOrbbecRecord = async () => {
  if (!orbbecRecordMeta.value?.path) {
    ElMessage.warning('无录制内容')
    return
  }
  if (!props.subject?.id) {
    ElMessage.warning('受试者信息缺失')
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  try {
    const payload = {
      path: orbbecRecordMeta.value.path,
      subject_id: props.subject.id,
      video_type: currentVideoType.value,
      meta: orbbecRecordMeta.value.meta || {},
    }
    // 已裁剪：把区间传给后端，对原始 mkv 彩色+深度双轨同步裁剪后再入库
    if (orbbecTrimmed.value) {
      payload.trim_start = trimStartSec.value
      payload.trim_end = trimEndSec.value
    }
    await uploadOrbbecRecordApi(payload)
    const typeLabel = VIDEO_TYPE_OPTIONS.find(t => t.value === currentVideoType.value)?.label || currentVideoType.value
    ElMessage.success(`${typeLabel}视频已上传${hasVideoType(currentVideoType.value) ? '（已替换原视频）' : ''}`)
    emit('success')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// ==================== RealSense 录制（精简版：无裁剪） ====================
const startRealSenseRecord = async () => {
  if (!realSenseAvailable.value) {
    ElMessage.warning('深度相机未就绪')
    return
  }
  // 乐观进入录制中：相机已在实时预览会话上运行，record/start 秒开
  phase.value = 'recording'
  sourceLabel.value = '录制完成'
  durationMs.value = 0
  realSenseRecordMeta.value = null
  realSensePreviewReady.value = false
  try {
    await startRealSenseRecordApi({ fps: 30 })
    // 后端 record/start 会先停预览子进程（录制与预览互斥），实时流已断开
    realSensePreviewing.value = false
    // 计时器
    const startTs = Date.now()
    if (realSenseRecordTimerId) clearInterval(realSenseRecordTimerId)
    realSenseRecordTimerId = setInterval(() => {
      durationMs.value = Date.now() - startTs
    }, 200)
    ElMessage.success('录制已开始')
  } catch (e) {
    phase.value = 'idle'
    ElMessage.error(e?.response?.data?.message || '启动录制失败')
  }
}

const stopRealSenseRecord = async () => {
  if (stoppingRealSense.value) return
  stoppingRealSense.value = true
  if (realSenseRecordTimerId) {
    clearInterval(realSenseRecordTimerId)
    realSenseRecordTimerId = null
  }
  try {
    const res = await stopRealSenseRecordApi()
    realSenseRecordMeta.value = res.data
    phase.value = 'done'
    ElMessage.success('录制已停止，正在生成预览...')
    // 预览由后端 ffmpeg 后台生成，轮询就绪后显示
    pollRealSensePreview()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '停止录制失败')
    phase.value = 'idle'
  } finally {
    stoppingRealSense.value = false
  }
}

// 轮询录制后处理状态（预览生成），最多约 90s
const pollRealSensePreview = async () => {
  realSensePreviewReady.value = false
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 1500))
    try {
      const res = await getRealSenseRecordStatusApi()
      const d = res.data || {}
      if (d.done) {
        if (d.preview_rel && realSenseRecordMeta.value) {
          realSenseRecordMeta.value.preview_rel = d.preview_rel
          if (d.meta) {
            realSenseRecordMeta.value.meta = d.meta
            // 时长优先用 ffprobe 读出的预览视频真实时长（与播放器一致）；
            // 无则回退开始/结束时间差（该差值包含录制器启动/保存开销，会偏大）
            if (d.meta.duration_sec && isFinite(d.meta.duration_sec)) {
              realSenseRecordMeta.value.duration_sec = d.meta.duration_sec
            } else if (d.meta.start_time && d.meta.end_time) {
              const s = new Date(d.meta.start_time).getTime()
              const e = new Date(d.meta.end_time).getTime()
              if (e > s) realSenseRecordMeta.value.duration_sec = Math.round((e - s) / 1000)
            }
          }
          realSensePreviewReady.value = true
          ElMessage.success('预览已生成')
        } else {
          realSensePreviewReady.value = false
          ElMessage.warning('预览生成失败，仍可上传')
        }
        return
      }
    } catch { /* 继续轮询 */ }
  }
  ElMessage.warning('预览生成超时，仍可上传录制数据')
}

const resetRealSenseCapture = () => {
  realSenseRecordMeta.value = null
  realSensePreviewReady.value = false
  durationMs.value = 0
  phase.value = 'idle'
  // 子进程随录制结束已退出（相机自动关闭），重新采集需重新启动实时预览
  startRealSenseLive()
}

const uploadRealSenseRecord = async () => {
  if (!realSenseRecordMeta.value?.path) {
    ElMessage.warning('无录制内容')
    return
  }
  if (!props.subject?.id) {
    ElMessage.warning('受试者信息缺失')
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  try {
    await uploadRealSenseRecordApi({
      dir: realSenseRecordMeta.value.path,
      subject_id: props.subject.id,
      video_type: currentVideoType.value,
    })
    const typeLabel = VIDEO_TYPE_OPTIONS.find(t => t.value === currentVideoType.value)?.label || currentVideoType.value
    ElMessage.success(`${typeLabel}视频已上传${hasVideoType(currentVideoType.value) ? '（已替换原视频）' : ''}`)
    // 上传成功后清空状态并提示（重置后画面恢复实时预览）
    resetRealSenseCapture()
    emit('success')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// ==================== 摄像头初始化 ====================
// 深度相机（Femto Bolt）的 RGB 接口是标准 UVC 摄像头，浏览器可直接作为
// 普通摄像头使用。这里枚举所有 videoinput 设备并支持选择，保证：
//  - 只插深度相机时，普通摄像头模式也能正常出彩色画面
//  - 同时插多个摄像头时，可手动选择用哪个
const initCamera = async () => {
  phase.value = 'idle'
  initMessage.value = '正在请求摄像头权限...'
  if (!navigator.mediaDevices?.getUserMedia) {
    initMessage.value = '当前浏览器不支持摄像头，请使用 Chrome/Edge'
    return
  }
  try {
    // 先请求授权（任意设备），授权后 enumerateDevices 才能拿到设备名称
    const s = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: true,
    })
    stream.value = s
    await refreshDeviceList()

    // 选择设备：沿用上次选择 > 第一个设备（深度相机 RGB 亦作普通摄像头）
    const valid = cameraOptions.value.some(d => d.deviceId === selectedDeviceId.value)
    if (!valid) {
      selectedDeviceId.value = cameraOptions.value[0]?.deviceId || ''
    }
    // 若当前实际使用的设备与所选不一致，切换过去
    if (selectedDeviceId.value && getStreamDeviceId(s) !== selectedDeviceId.value) {
      await switchToDevice(selectedDeviceId.value)
    } else {
      await playPreview(s)
    }
    initMessage.value = ''
  } catch (e) {
    initMessage.value = `无法访问摄像头：${e.name === 'NotAllowedError' ? '已拒绝授权，请在浏览器设置中允许摄像头' : e.message}`
  }
}

// 摄像头下拉选项 = 枚举到的 UVC 设备（含深度相机 RGB，均作为普通摄像头使用）
const cameraOptions = computed(() => [...cameraDevices.value])

// 枚举视频输入设备（需在授权后调用才有名称）
const refreshDeviceList = async () => {
  try {
    const all = await navigator.mediaDevices.enumerateDevices()
    const inputs = all.filter(d => d.kind === 'videoinput')
    // 去重：同一物理设备常以多种格式暴露多个 deviceId 且同名，
    // 界面只保留一个，避免出现"两个一样的摄像头"
    const seen = new Set()
    const unique = []
    for (const d of inputs) {
      const key = (d.label || '').trim().toLowerCase()
      if (key) {
        if (seen.has(key)) continue
        seen.add(key)
      }
      unique.push(d)
    }
    cameraDevices.value = unique
  } catch {
    cameraDevices.value = []
  }
}

// 设备显示名（label 可能为空）
const cameraLabel = (d) => {
  const label = d.label?.trim()
  if (label) return label
  return `摄像头 ${String(d.deviceId).slice(0, 8)}`
}

// 获取当前媒体流的视频设备 id
const getStreamDeviceId = (s) => {
  const track = s?.getVideoTracks?.()[0]
  try {
    return track?.getSettings?.()?.deviceId || ''
  } catch {
    return ''
  }
}

// 仅停止媒体流的所有轨道（不清理 UI/计时器，供切换时复用）
const stopStream = (s) => {
  if (s) {
    s.getTracks().forEach(t => t.stop())
  }
}

// 切换到指定设备：先获取新流，成功后再停旧流；失败保留原摄像头
const switchToDevice = async (deviceId) => {
  if (!deviceId) return
  try {
    const s = await navigator.mediaDevices.getUserMedia({
      video: {
        deviceId: { exact: deviceId },
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: true,
    })
    stopStream(stream.value)
    stream.value = s
    initMessage.value = ''
    await playPreview(s)
  } catch (e) {
    // 新设备不可用：旧流保持不动，仅提示
    initMessage.value = `无法使用所选摄像头：${e.message}`
  }
}

// 下拉选择变化
const onDeviceChange = (deviceId) => {
  if (!deviceId || deviceId === getStreamDeviceId(stream.value)) return
  switchToDevice(deviceId)
}

const playPreview = async (s) => {
  await nextTick()
  if (videoRef.value) {
    videoRef.value.srcObject = s
    try { await videoRef.value.play() } catch { /* muted 自动播放 */ }
  }
}

// ==================== 录制控制 ====================
const startRecording = () => {
  if (!stream.value) {
    ElMessage.warning('摄像头未就绪')
    return
  }
  chunks = []
  const options = { mimeType: getSupportedMime() }
  try {
    mediaRecorder = new MediaRecorder(stream.value, options)
  } catch {
    mediaRecorder = new MediaRecorder(stream.value)
  }
  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data)
  }
  mediaRecorder.onstop = () => {
    const type = mediaRecorder.mimeType || 'video/webm'
    recordedBlob = new Blob(chunks, { type })
    blobSize.value = recordedBlob.size
    phase.value = 'done'
    switchToPlayback(recordedBlob)
  }
  mediaRecorder.start(1000)
  startTime = Date.now()
  elapsedBeforePause = 0
  durationMs.value = 0
  phase.value = 'recording'
  startTimer()
}

const pauseRecording = () => {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.pause()
    elapsedBeforePause += Date.now() - startTime
    phase.value = 'paused'
    stopTimer()
  }
}

const resumeRecording = () => {
  if (mediaRecorder && mediaRecorder.state === 'paused') {
    mediaRecorder.resume()
    startTime = Date.now()
    phase.value = 'recording'
    startTimer()
  }
}

const stopRecording = () => {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    if (phase.value === 'recording') {
      elapsedBeforePause += Date.now() - startTime
    }
    durationMs.value = elapsedBeforePause
    mediaRecorder.stop()
    stopTimer()
  }
}

const resetCapture = async () => {
  if (videoRef.value?.src) {
    URL.revokeObjectURL(videoRef.value.src)
    videoRef.value.src = ''
  }
  if (stream.value) {
    await playPreview(stream.value)
  }
  resetState()
}

const resetState = () => {
  phase.value = 'idle'
  recordedBlob = null
  blobSize.value = 0
  durationMs.value = 0
  currentDurationSec.value = 0
  elapsedBeforePause = 0
  uploadProgress.value = 0
  uploading.value = false
  trimStartSec.value = 0
  trimEndSec.value = 0
  trimming.value = false
  trimProgress.value = 0
  sourceLabel.value = '录制完成'
}

// 切换到回放模式（录制完成/选择文件/裁剪后）
const switchToPlayback = (blob) => {
  if (videoRef.value) {
    videoRef.value.srcObject = null
    if (videoRef.value.src) URL.revokeObjectURL(videoRef.value.src)
    const url = URL.createObjectURL(blob)
    videoRef.value.src = url
    videoRef.value.loop = true
    videoRef.value.onloadedmetadata = () => {
      const d = videoRef.value.duration
      if (d && isFinite(d)) {
        currentDurationSec.value = d
        // 默认裁剪范围为整段
        trimStartSec.value = 0
        trimEndSec.value = Math.floor(d * 10) / 10
      } else {
        // 录制时长回退到 durationMs
        currentDurationSec.value = durationMs.value / 1000
        trimStartSec.value = 0
        trimEndSec.value = currentDurationSec.value
      }
    }
    videoRef.value.play().catch(() => {})
  }
}

// ==================== 文件选择（无摄像头时的备选方案） ====================
const pickVideoFile = () => {
  fileInputRef.value?.click()
}

const onVideoFileChange = (e) => {
  const file = e.target.files?.[0]
  if (!file) return
  if (!file.type.startsWith('video/')) {
    ElMessage.warning('请选择视频文件')
    e.target.value = ''
    return
  }
  if (videoRef.value?.src) {
    URL.revokeObjectURL(videoRef.value.src)
    videoRef.value.src = ''
  }
  recordedBlob = file
  blobSize.value = file.size
  sourceLabel.value = '已选文件'
  phase.value = 'done'
  switchToPlayback(file)
  e.target.value = ''
}

// ==================== 裁剪功能 ====================
// 获取当前激活的视频元素：Orbbec 用 orbbecVideoRef，普通摄像头用 videoRef。
// 裁剪控制区是共用的，而两种模式的 video 元素 ref 不同，直接取 videoRef 在
// Orbbec 模式下为 null 会导致"定位/预览片段"失效。
const getActiveVideo = () => {
  if (deviceSource.value === 'orbbec') return orbbecVideoRef.value
  return videoRef.value
}

const enterEditMode = () => {
  if (!currentDurationSec.value) {
    ElMessage.warning('无法获取视频时长，不支持裁剪')
    return
  }
  phase.value = 'editing'
  // 暂停循环播放，便于定位
  if (videoRef.value) {
    videoRef.value.loop = false
    videoRef.value.pause()
  }
}

const exitEditMode = () => {
  phase.value = 'done'
  if (videoRef.value) {
    videoRef.value.loop = true
    videoRef.value.currentTime = 0
    videoRef.value.play().catch(() => {})
  }
}

// 将当前播放位置设为裁剪起点
const setTrimStartToCurrent = () => {
  const v = getActiveVideo()
  if (!v) return
  const t = v.currentTime
  trimStartSec.value = Math.min(t, (trimEndSec.value || currentDurationSec.value) - 0.1)
  // 如果起点 >= 结束，往后推结束
  if (trimStartSec.value >= trimEndSec.value) {
    trimEndSec.value = Math.min(currentDurationSec.value, trimStartSec.value + 0.5)
  }
}

// 将当前播放位置设为裁剪终点
const setTrimEndToCurrent = () => {
  const v = getActiveVideo()
  if (!v) return
  const t = v.currentTime
  trimEndSec.value = Math.max(t, (trimStartSec.value || 0) + 0.1)
  if (trimEndSec.value > currentDurationSec.value) {
    trimEndSec.value = currentDurationSec.value
  }
}

// 预览裁剪区间
const previewTrim = () => {
  const v = getActiveVideo()
  if (!v) return
  v.currentTime = trimStartSec.value
  v.play().catch(() => {})
  // 到达结束时间自动暂停
  const onTimeUpdate = () => {
    if (v.currentTime >= trimEndSec.value) {
      v.pause()
      v.removeEventListener('timeupdate', onTimeUpdate)
    }
  }
  v.addEventListener('timeupdate', onTimeUpdate)
}

const resetTrimRange = () => {
  trimStartSec.value = 0
  trimEndSec.value = Math.floor(currentDurationSec.value * 10) / 10
}

// 应用裁剪：使用 canvas + MediaRecorder 重新录制选定区间
const applyTrim = async () => {
  if (!recordedBlob || !videoRef.value) return
  const start = trimStartSec.value
  const end = trimEndSec.value
  if (end - start < 0.1) {
    ElMessage.warning('裁剪区间过短')
    return
  }
  trimming.value = true
  trimProgress.value = 0
  try {
    const video = videoRef.value
    const canvas = canvasRef.value
    const w = video.videoWidth || 1280
    const h = video.videoHeight || 720
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    const fps = 30

    // 从视频捕获流（含视频轨）
    const canvasStream = canvas.captureStream(fps)
    // 尝试从原视频携带音频轨道
    let audioStream = null
    try {
      audioStream = video.captureStream ? video.captureStream() : (video.mozCaptureStream ? video.mozCaptureStream() : null)
    } catch { /* 某些浏览器不支持 */ }
    const mixedStream = new MediaStream()
    canvasStream.getVideoTracks().forEach(t => mixedStream.addTrack(t))
    if (audioStream) {
      audioStream.getAudioTracks().forEach(t => {
        // 仅添加音频轨道，避免与视频流冲突
        try { mixedStream.addTrack(t) } catch {}
      })
    }

    const mime = getSupportedMime() || 'video/webm'
    const rec = new MediaRecorder(mixedStream, { mimeType: mime })
    const parts = []
    rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) parts.push(e.data) }
    rec.onstop = () => {
      const trimmedBlob = new Blob(parts, { type: mime })
      recordedBlob = trimmedBlob
      blobSize.value = trimmedBlob.size
      durationMs.value = (end - start) * 1000
      currentDurationSec.value = end - start
      sourceLabel.value = '裁剪片段'
      // 切换回放为新 Blob
      switchToPlayback(trimmedBlob)
      phase.value = 'done'
      ElMessage.success(`已裁剪 ${formatTime(end - start)} 片段`)
    }

    // 暂停视频并定位到起点
    video.pause()
    video.loop = false
    await seekTo(video, start)

    rec.start(200)
    video.play().catch(() => {})

    const totalMs = (end - start) * 1000
    const t0 = Date.now()
    const onTimeUpdate = () => {
      const cur = video.currentTime
      if (cur >= end - 0.05) {
        video.pause()
        video.removeEventListener('timeupdate', onTimeUpdate)
        // 停止录制
        if (rec.state !== 'inactive') rec.stop()
        canvasStream.getTracks().forEach(t => t.stop())
        trimming.value = false
        trimProgress.value = 100
        return
      }
      // 绘制当前帧到 canvas
      ctx.drawImage(video, 0, 0, w, h)
      // 更新进度
      const elapsed = Date.now() - t0
      trimProgress.value = Math.min(99, Math.round((elapsed / totalMs) * 100))
    }
    video.addEventListener('timeupdate', onTimeUpdate)
    // 同时用 requestAnimationFrame 持续绘制
    const drawFrame = () => {
      if (!video.paused) ctx.drawImage(video, 0, 0, w, h)
      if (rec.state === 'recording') requestAnimationFrame(drawFrame)
    }
    requestAnimationFrame(drawFrame)
  } catch (e) {
    trimming.value = false
    ElMessage.error(`裁剪失败：${e.message || e}`)
  }
}

// 安全 seek：兼容 loadeddata 事件
const seekTo = (video, time) => new Promise((resolve) => {
  const onSeeked = () => { video.removeEventListener('seeked', onSeeked); resolve() }
  video.addEventListener('seeked', onSeeked)
  video.currentTime = time
  // 超时保险
  setTimeout(() => { video.removeEventListener('seeked', onSeeked); resolve() }, 1500)
})

// ==================== 计时器 ====================
const startTimer = () => {
  stopTimer()
  timerId = setInterval(() => {
    durationMs.value = elapsedBeforePause + (Date.now() - startTime)
  }, 200)
}
const stopTimer = () => {
  if (timerId) { clearInterval(timerId); timerId = null }
}

// ==================== 上传 ====================
const handleUpload = async () => {
  if (!recordedBlob || !props.subject?.id) {
    ElMessage.warning('无录制内容或受试者信息缺失')
    return
  }
  if (!currentVideoType.value) {
    ElMessage.warning('请选择采集类型（面部/身体/步态）')
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  const ext = (recordedBlob.type && recordedBlob.type.includes('mp4')) ? 'mp4' : 'webm'
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const filename = `${props.subject.pseudo_id}_${currentVideoType.value}_${ts}.${ext}`

  try {
    // 后端按 video_type 精准重采：自动删除同类型旧视频，不影响其他类型
    // 前端无需再调用 deleteAssetApi，避免误删其他类型
    const formData = new FormData()
    formData.append('file', recordedBlob, filename)
    formData.append('subject_id', props.subject.id)
    formData.append('data_type', 'video')
    formData.append('layer', 'raw')
    formData.append('video_type', currentVideoType.value)

    await uploadAssetApi(formData, (e) => {
      if (e.total) uploadProgress.value = Math.round((e.loaded / e.total) * 100)
    })
    const typeLabel = VIDEO_TYPE_OPTIONS.find(t => t.value === currentVideoType.value)?.label || currentVideoType.value
    ElMessage.success(`${typeLabel}视频已上传${hasVideoType(currentVideoType.value) ? '（已替换原视频）' : ''}`)
    emit('success')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// 历史函数 removeExistingVideoAssets 已废弃：
// 新规则下后端按 video_type 精准重采，前端不再主动删除资产，避免误删其他类型

// ==================== 工具函数 ====================
const getSupportedMime = () => {
  // 优先 mp4（Chrome/Edge 较新版本支持），不支持则回退 webm
  const types = [
    'video/mp4;codecs=h264,aac',
    'video/mp4;codecs=h264',
    'video/mp4',
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm',
  ]
  for (const t of types) {
    if (MediaRecorder.isTypeSupported(t)) return t
  }
  return ''
}

const formatTime = (sec) => {
  if (!sec || !isFinite(sec)) return '00:00:00'
  const total = Math.floor(sec)
  const h = String(Math.floor(total / 3600)).padStart(2, '0')
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const s = String(total % 60).padStart(2, '0')
  return `${h}:${m}:${s}`
}

const formatFileSize = (bytes) => {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i]
}

// ==================== 资源释放 ====================
const releaseCamera = () => {
  stopTimer()
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try { mediaRecorder.stop() } catch {}
  }
  mediaRecorder = null
  if (videoRef.value?.src) {
    URL.revokeObjectURL(videoRef.value.src)
    videoRef.value.src = ''
  }
  if (stream.value) {
    stopStream(stream.value)
    stream.value = null
  }
}
</script>

<style scoped>
/* 视频采集对话框：内容超高时内部滚动，避免超出屏幕 */
:deep(.el-dialog) {
  max-height: 94vh;
  display: flex;
  flex-direction: column;
}
:deep(.el-dialog__body) {
  overflow-y: auto;
}
.subject-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 4px;
}

.video-status-label {
  color: #606266;
  font-size: 13px;
  margin-right: 4px;
}

.type-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
}

.source-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 4px;
  flex-wrap: wrap;
}

.type-label {
  color: #606266;
  font-size: 13px;
  font-weight: 500;
}

.video-stage {
  position: relative;
  background: #000;
  border-radius: 6px;
  overflow: hidden;
  aspect-ratio: 16 / 9;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 深度相机模式（Orbbec / RealSense）：与普通摄像头一致 16:9 满宽，画面无黑边 */
.video-stage.is-orbbec,
.video-stage.is-realsense {
  height: auto;
}

.video-el {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.video-el.recording {
  box-shadow: inset 0 0 0 3px #f56c6c;
}

.video-el.paused {
  box-shadow: inset 0 0 0 3px #e6a23c;
}

.rec-indicator {
  position: absolute;
  top: 12px;
  left: 12px;
  display: flex;
  align-items: center;
  padding: 4px 10px;
  background: rgba(0, 0, 0, 0.6);
  border-radius: 12px;
  color: #fff;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.rec-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #f56c6c;
}

.rec-dot.blink {
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.2; }
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  color: #909399;
}

.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.trim-panel {
  margin-top: 12px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
}

.trim-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.trim-row.trim-info {
  margin-top: 8px;
  margin-bottom: 0;
}

.trim-label {
  width: 80px;
  color: #606266;
  font-size: 13px;
}

.trim-info {
  color: #909399;
  font-size: 13px;
}

.trim-hint {
  margin-top: 8px;
  color: #c0c4cc;
  font-size: 12px;
  line-height: 1.5;
}
</style>
