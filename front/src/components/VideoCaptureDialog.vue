<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="视频采集"
    width="920px"
    top="5vh"
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
          <el-tag v-if="hasVideoType(t.value)" type="warning" size="small" style="margin-left: 4px">
            重采
          </el-tag>
        </el-radio-button>
      </el-radio-group>
      <span v-if="hasVideoType(currentVideoType)" style="margin-left: 12px; color: #e6a23c; font-size: 12px">
        当前类型已有视频，上传后将替换
      </span>
    </div>

    <!-- 视频区 -->
    <div class="video-stage">
      <video
        ref="videoRef"
        class="video-el"
        :class="{ recording: phase === 'recording', paused: phase === 'paused' }"
        :controls="canSeek"
        :autoplay="!canSeek"
        muted
        playsinline
      />

      <!-- 录制指示器 -->
      <div v-if="phase === 'recording' || phase === 'paused'" class="rec-indicator">
        <span class="rec-dot" :class="{ blink: phase === 'recording' }" />
        <span style="margin-left: 6px">{{ phase === 'recording' ? '录制中' : '已暂停' }}</span>
        <span style="margin-left: 12px; font-variant-numeric: tabular-nums">{{ formattedDuration }}</span>
      </div>

      <!-- 未授权占位 -->
      <div v-if="phase === 'idle' && !stream" class="placeholder">
        <el-icon :size="48"><VideoCamera /></el-icon>
        <div style="margin-top: 12px; color: #909399">{{ initMessage }}</div>
      </div>
    </div>

    <!-- 控制区 -->
    <div class="controls">
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
  Scissor, Check, Aim,
} from '@element-plus/icons-vue'
import { VideoCamera } from '@element-plus/icons-vue'
import { uploadAssetApi } from '@/api/data'

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

// ==================== 计算属性 ====================
const formattedDuration = computed(() => formatTime(durationMs.value / 1000))
// 仅在已完成/编辑态显示原生控制器（时间轴），录制中不显示
const canSeek = computed(() => phase.value === 'done' || phase.value === 'editing')

// ==================== 生命周期 ====================
const onOpen = async () => {
  await initCamera()
}

const onClosed = () => {
  releaseCamera()
  resetState()
}

onBeforeUnmount(() => {
  releaseCamera()
})

// ==================== 摄像头初始化 ====================
const initCamera = async () => {
  phase.value = 'idle'
  initMessage.value = '正在请求摄像头权限...'
  if (!navigator.mediaDevices?.getUserMedia) {
    initMessage.value = '当前浏览器不支持摄像头，请使用 Chrome/Edge'
    return
  }
  try {
    const s = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: true,
    })
    stream.value = s
    await playPreview(s)
  } catch (e) {
    initMessage.value = `无法访问摄像头：${e.name === 'NotAllowedError' ? '已拒绝授权，请在浏览器设置中允许摄像头' : e.message}`
  }
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
  if (!videoRef.value) return
  const t = videoRef.value.currentTime
  trimStartSec.value = Math.min(t, (trimEndSec.value || currentDurationSec.value) - 0.1)
  // 如果起点 >= 结束，往后推结束
  if (trimStartSec.value >= trimEndSec.value) {
    trimEndSec.value = Math.min(currentDurationSec.value, trimStartSec.value + 0.5)
  }
}

// 将当前播放位置设为裁剪终点
const setTrimEndToCurrent = () => {
  if (!videoRef.value) return
  const t = videoRef.value.currentTime
  trimEndSec.value = Math.max(t, (trimStartSec.value || 0) + 0.1)
  if (trimEndSec.value > currentDurationSec.value) {
    trimEndSec.value = currentDurationSec.value
  }
}

// 预览裁剪区间
const previewTrim = () => {
  if (!videoRef.value) return
  videoRef.value.currentTime = trimStartSec.value
  videoRef.value.play().catch(() => {})
  // 到达结束时间自动暂停
  const onTimeUpdate = () => {
    if (videoRef.value.currentTime >= trimEndSec.value) {
      videoRef.value.pause()
      videoRef.value.removeEventListener('timeupdate', onTimeUpdate)
    }
  }
  videoRef.value.addEventListener('timeupdate', onTimeUpdate)
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
    stream.value.getTracks().forEach(t => t.stop())
    stream.value = null
  }
}
</script>

<style scoped>
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
