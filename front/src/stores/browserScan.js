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
  runBrowserScan, loadSubjectCache, loadUploadedMap, saveUploadedMap, clearPendingMap,
} from '@/utils/browserScan'

// 用户主动停止标志：持久化到 localStorage。
// 用户点击"停止监控"后刷新页面，autoResumeIfGranted 检测到该标志将不再自动恢复
//（目录句柄仍在 IndexedDB 中，但需用户再次点击"开始监控"才恢复）。
const USER_STOPPED_KEY = 'browser_scan_user_stopped'

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
    // 本轮处于写入观察期、尚未上传的文件数（首见只登记，下一轮读数未变才上传）
    observingCount: 0,
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

  /** 初始化：恢复持久化的目录句柄与已上传记录（增量去重，避免全量重传） */
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
   * 用户主动点击过"停止监控"时不会自动恢复（保持停止状态）。
   */
  const autoResumeIfGranted = async () => {
    if (!state.handle) return false
    // 用户主动停止过：刷新后保持停止状态，不自动恢复
    if (localStorage.getItem(USER_STOPPED_KEY)) {
      state.pendingRestore = false
      return false
    }
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
      // 不重置「已上传文件记录」：避免重新选目录导致所有文件全量重传。
      // 换成新目录后，不存在于新目录的旧路径会被 runScanFromFiles 的失效清理自动移除，
      // 只会上传真正的新增/重放文件。
      state.pendingRestore = false
      // 重新选择目录视为新的开始：清除"主动停止"标志
      localStorage.removeItem(USER_STOPPED_KEY)
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
    // 观察期基线一并清空：换目录后不应沿用旧路径的读数
    clearPendingMap()
    state.observingCount = 0
    state.totalNewSubjects = 0
    state.totalUploaded = 0
    state.failures = []
    state.pendingRestore = false
    state.lastScanAt = ''
    // 目录已移除，清除"主动停止"标志（后续选择新目录后刷新可自动恢复）
    localStorage.removeItem(USER_STOPPED_KEY)
  }

  const _doScan = async () => {
    if (!state.handle || state.scanning) return
    state.scanning = true
    state.progress = { phase: 'scan', current: 0, total: 0, currentFile: '扫描目录…' }
    try {
      const result = await runBrowserScan(state.handle, {
        // 增量扫描：带上已上传记录做 diff（正常文件跳过不重传）；
        // 目录/文件删除后记录在 runScanFromFiles 内自动失效，重放后能重新入库
        uploadedMap: _uploadedMap,
        collectionBatch: state.collectionBatch || undefined,
        collectionScene: state.collectionScene || undefined,
        onProgress: (p) => { state.progress = p },
      })
      state.totalNewSubjects += result.newSubjects
      state.totalUploaded += result.uploadedPaths.length
      state.totalUpdatedSubjects = (state.totalUpdatedSubjects || 0) + (result.updatedSubjects || 0)
      _uploadedMap = result.uploadedMap
      saveUploadedMap(_uploadedMap)
      state.observingCount = result.observingCount || 0
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

  /**
   * 开始监控
   * @param {Object} [opts]
   * @param {boolean} [opts.skipVerify] 自动恢复（刷新页面后权限仍 granted）时不弹授权框
   * @param {boolean} [opts.resetObservation] 用户主动点击「开始监控」：清空写入观察期
   *   基线，让目录内所有尚未上传的文件重新走一轮观察。若沿用上一会话残留的基线，
   *   这些文件在第一轮就会被判为"读数稳定"直接上传 —— 表现为点击开始监控即全量上传。
   *   自动恢复监控（刷新页面）时不重置，避免刷新一次就白等一轮。
   */
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
    if (opts.resetObservation) {
      clearPendingMap()
      state.observingCount = 0
    }
    try { await loadSubjectCache() } catch { /* 缓存加载失败不阻断 */ }
    state.running = true
    state.pendingRestore = false
    // 用户明确重新开始监控：清除"主动停止"标志，允许后续刷新自动恢复
    localStorage.removeItem(USER_STOPPED_KEY)
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
    // 持久化"用户主动停止"标志：刷新页面后不再自动恢复监控
    localStorage.setItem(USER_STOPPED_KEY, '1')
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
