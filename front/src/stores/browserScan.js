/**
 * 浏览器目录扫描 Pinia store（全局单例）
 *
 * 核心价值：定时器与扫描状态从组件提升到 store，
 * 路由切换时组件卸载但 store 不销毁，定时器持续运行。
 * 页面刷新后整个 JS 运行时重置，通过 IndexedDB 持久化的目录句柄检测恢复状态。
 */
import { defineStore } from 'pinia'
import { reactive } from 'vue'
import {
  supportsFsAccess, pickDirectory, loadHandle, clearHandle, verifyPermission,
  queryPermission,
} from '@/utils/dirWatcher'
import {
  runBrowserScan, loadSubjectCache, loadUploadedMap, saveUploadedMap,
} from '@/utils/browserScan'

export const useBrowserScanStore = defineStore('browserScan', () => {
  const state = reactive({
    supported: supportsFsAccess(),
    handle: null,
    dirName: '',
    running: false,
    scanning: false,
    intervalSec: 60,
    collectionBatch: '',  // 默认采集批次（新建受试者兜底）
    collectionScene: '',  // 默认采集场景（新建受试者兜底）
    lastScanAt: '',
    totalNewSubjects: 0,
    totalUploaded: 0,
    failures: [],
    progress: { phase: '', current: 0, total: 0, currentFile: '' },
    // 刷新页面后检测到持久化句柄但定时器已丢失，需用户点击恢复
    pendingRestore: false,
    lastResult: null, // 最近一次扫描结果（供组件 watch 触发刷新）
  })

  let _watchTimer = null
  let _uploadedMap = {}
  // 扫描完成回调（由组件设置，组件卸载时置 null）
  let _onScanComplete = null

  const setOnScanComplete = (fn) => { _onScanComplete = fn }

  /** 初始化：恢复持久化的目录句柄与已上传记录（不自动启动扫描） */
  const init = async () => {
    if (!state.supported) return
    _uploadedMap = loadUploadedMap()
    try {
      const h = await loadHandle()
      if (h) {
        state.handle = h
        state.dirName = h.name
        // 刷新页面后定时器已丢失。不在此处自动启动扫描——此时组件尚未注册
        // 扫描完成回调，首次扫描结果无法通知列表刷新；由组件在注册回调后
        // 调用 autoResumeIfGranted() 恢复。
        state.pendingRestore = true
      }
    } catch { /* 忽略 */ }
  }

  /**
   * 权限仍有效则自动恢复监控（刷新页面后调用，无需用户点击）。
   * 仅权限降级（prompt/denied，需用户手势授权）时保留 pendingRestore。
   */
  const autoResumeIfGranted = async () => {
    if (!state.handle) return false
    if (state.running || _watchTimer) {
      state.pendingRestore = false
      return true
    }
    if ((await queryPermission(state.handle)) !== 'granted') return false
    await startWatch({ skipVerify: true })
    state.pendingRestore = false
    return true
  }

  const pickDir = async () => {
    try {
      const h = await pickDirectory()
      state.handle = h
      state.dirName = h.name
      state.totalNewSubjects = 0
      state.totalUploaded = 0
      state.failures = []
      _uploadedMap = {}
      saveUploadedMap(_uploadedMap)
      state.pendingRestore = false
      return true
    } catch (e) {
      if (e?.name === 'AbortError') return false
      throw e
    }
  }

  const clearDir = async () => {
    stopWatch()
    await clearHandle()
    state.handle = null
    state.dirName = ''
    _uploadedMap = {}
    saveUploadedMap(_uploadedMap)
    state.totalNewSubjects = 0
    state.totalUploaded = 0
    state.failures = []
    state.pendingRestore = false
    state.lastScanAt = ''
  }

  const _doScan = async () => {
    if (!state.handle || state.scanning) return
    state.scanning = true
    state.progress = { phase: 'scan', current: 0, total: 0, currentFile: '扫描目录…' }
    try {
      const result = await runBrowserScan(state.handle, {
        uploadedMap: _uploadedMap,
        collectionBatch: state.collectionBatch || undefined,
        collectionScene: state.collectionScene || undefined,
        onProgress: (p) => { state.progress = p },
      })
      state.totalNewSubjects += result.newSubjects
      state.totalUploaded += result.uploadedPaths.length
      _uploadedMap = result.uploadedMap
      saveUploadedMap(_uploadedMap)
      state.failures = result.failures
      state.lastScanAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      state.lastResult = { ...result, ts: Date.now() }
      // 通知组件刷新（组件可能已卸载，_onScanComplete 为 null 时跳过）
      if (_onScanComplete) {
        try { _onScanComplete(result) } catch { /* 组件刷新失败不影响扫描 */ }
      }
    } catch (e) {
      state.failures = [{ name: '扫描异常', reason: e.message || String(e) }]
    } finally {
      state.scanning = false
    }
  }

  const scanOnce = async () => { await _doScan() }

  const startWatch = async (opts = {}) => {
    // 已有定时器在运行则直接返回，避免重复创建定时器导致扫描并发堆积
    if (_watchTimer) return
    if (!state.handle) throw new Error('请先选择监控目录')
    // 自动恢复（刷新页面后权限仍 granted）时直接使用已有授权，不弹授权框
    if (opts.skipVerify) {
      if ((await queryPermission(state.handle)) !== 'granted') throw new Error('未获得目录读取权限')
    } else if (!(await verifyPermission(state.handle))) {
      throw new Error('未获得目录读取权限')
    }
    try { await loadSubjectCache() } catch { /* 缓存加载失败不阻断 */ }
    state.running = true
    state.pendingRestore = false
    await _doScan()
    const sec = Math.max(1, state.intervalSec)
    _watchTimer = setInterval(_doScan, sec * 1000)
  }

  const stopWatch = () => {
    state.running = false
    if (_watchTimer) {
      clearInterval(_watchTimer)
      _watchTimer = null
    }
  }

  /** 刷新页面后恢复监控（需用户点击触发，FSA 权限需要 user activation） */
  const restoreWatch = async () => {
    if (!state.handle) throw new Error('无持久化的目录句柄')
    if (!(await verifyPermission(state.handle))) {
      state.pendingRestore = true
      throw new Error('未获得目录读取权限，请在浏览器授权弹窗中点击"允许"')
    }
    await startWatch()
  }

  return {
    state, init, autoResumeIfGranted, pickDir, clearDir, scanOnce,
    startWatch, stopWatch, restoreWatch, setOnScanComplete,
  }
})
