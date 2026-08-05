/**
 * 浏览器目录扫描编排逻辑
 *
 * 流程：扫描根目录 → diff 新增文件 → 按受试者子文件夹分组 →
 *      解析 userInfo → 创建受试者 → 逐文件上传到后端
 *
 * 设计要点：
 * - 纯前端编排，文件通过 HTTP 上传，前后端可部署在不同设备
 * - 复用后端现有接口：/data/parse-userinfo、/data/subjects、/data/assets/upload
 * - 本地持久化「已上传文件记录」与「受试者缓存」，避免重复请求/上传
 * - 后端 upload_asset 本身有幂等去重（同受试者+同模态+同原始文件名+同原始大小），双保险
 */
import { scanDirectory, verifyPermission, diffFiles } from '@/utils/dirWatcher'
import {
  parseUserInfoApi, createSubjectApi, uploadAssetApi, getSubjectsApi,
} from '@/api/data'

// 伪ID合法格式：3-64位字母/数字/下划线/短横线（与后端 scanner 一致）
const PSEUDO_ID_RE = /^[A-Za-z0-9_\-]{3,64}$/

// 跳过的元数据/密钥文件名（小写比对）
const META_FILES = new Set([
  'userinfo.json', 'userinfo.json.enc',
  '密钥.txt', 'key.txt', 'secret.txt',
])

// localStorage 持久化 key
const UPLOADED_KEY = 'browser_scan_uploaded_map'
const SUBJECT_CACHE_KEY = 'browser_scan_subject_cache'

// 模块级受试者缓存：{ [pseudoId]: subjectId }
let _subjectCache = null

/**
 * 带重试的异步执行（用于应对后端 MySQL 死锁等暂时性错误）
 * - 500 / 网络错误：延迟后重试
 * - 422 / 4xx 业务错误：不重试直接抛出
 */
async function withRetry(fn, retries = 1, delayMs = 600) {
  try {
    return await fn()
  } catch (e) {
    if (retries <= 0) throw e
    const status = e.response?.status
    // 仅对 500 / 502 / 503 / 504 / 网络错误（无 response）重试
    const retryable = !status || status >= 500
    if (!retryable) throw e
    await new Promise((r) => setTimeout(r, delayMs))
    return withRetry(fn, retries - 1, delayMs)
  }
}

/**
 * 文件名 → 数据类型识别（与后端 scanner._detect_data_type 一致）
 */
function detectDataType(name) {
  const n = (name || '').toLowerCase()
  const stripped = n.endsWith('.enc') ? n.slice(0, -4) : n
  if (/_sync_data\.json$/.test(stripped)) return 'eye'
  if (/^(moca|mmse|ad8)_.+_\d{8}\.json$/.test(stripped)) return 'scale'
  if (stripped.startsWith('eeg_') || stripped.startsWith('eeg-')) return 'eeg'
  if (stripped.startsWith('ecg_') || stripped.startsWith('ecg-')) return 'ecg'
  if (stripped.startsWith('gait_') || stripped.startsWith('gait-')) return 'gait'
  if (stripped.startsWith('eye_') || stripped.startsWith('eye-')) return 'eye'
  const ext = stripped.includes('.') ? stripped.split('.').pop() : ''
  if (['mp4', 'avi', 'mov', 'mkv'].includes(ext)) return 'video'
  if (['wav', 'mp3', 'm4a', 'flac', 'aac', 'ogg'].includes(ext)) return 'audio'
  if (['edf', 'bdf'].includes(ext)) return 'eeg'
  if (['ecg', 'dat'].includes(ext)) return 'ecg'
  return 'task'
}

/**
 * 按受试者子文件夹分组文件
 * 根目录下第一级子文件夹名 = pseudo_id
 * @returns {Array<{pseudoId, userInfo, files}>}
 */
function groupBySubject(files) {
  const map = new Map()
  for (const f of files) {
    const segs = f.path.split('/')
    if (segs.length < 2) continue // 根目录直接放的文件忽略
    const pseudoId = segs[0].trim()
    if (!PSEUDO_ID_RE.test(pseudoId)) continue
    if (!map.has(pseudoId)) {
      map.set(pseudoId, { pseudoId, userInfo: null, files: [] })
    }
    const lower = f.name.toLowerCase()
    if (lower === 'userinfo.json' || lower === 'userinfo.json.enc') {
      map.get(pseudoId).userInfo = f
    } else if (
      !META_FILES.has(lower) &&
      !lower.endsWith('_sync_fields_zh.json') &&
      !f.name.startsWith('~$') &&
      !f.name.startsWith('.')
    ) {
      map.get(pseudoId).files.push(f)
    }
  }
  return [...map.values()]
}

// ==================== 受试者缓存 ====================

/** 拉取全部受试者，填充 pseudo_id → id 缓存（首次启动监控时调用） */
export async function loadSubjectCache() {
  _subjectCache = {}
  let page = 1
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const res = await getSubjectsApi({ page, page_size: 200 })
    const items = res.data?.items || []
    for (const s of items) {
      if (s.pseudo_id) _subjectCache[s.pseudo_id] = s.id
    }
    if (items.length < 200) break
    page++
  }
  // 同步到 localStorage（供下次会话快速恢复，减少全量拉取）
  try {
    localStorage.setItem(SUBJECT_CACHE_KEY, JSON.stringify(_subjectCache))
  } catch { /* localStorage 不可用时静默 */ }
  return _subjectCache
}

/** 从缓存或后端查询受试者ID */
async function resolveSubjectId(pseudoId) {
  if (_subjectCache && _subjectCache[pseudoId]) return _subjectCache[pseudoId]
  // 缓存未命中：按关键词查并精确比对 pseudo_id
  const res = await getSubjectsApi({ page: 1, page_size: 50, keyword: pseudoId })
  const hit = (res.data?.items || []).find((s) => s.pseudo_id === pseudoId)
  const id = hit?.id || null
  if (_subjectCache) _subjectCache[pseudoId] = id // 回填缓存
  return id
}

/** 尝试从 localStorage 恢复缓存（不触发网络请求） */
export function restoreSubjectCache() {
  if (_subjectCache) return _subjectCache
  try {
    const raw = localStorage.getItem(SUBJECT_CACHE_KEY)
    if (raw) _subjectCache = JSON.parse(raw) || {}
  } catch {
    _subjectCache = {}
  }
  return _subjectCache
}

// ==================== 已上传文件记录 ====================

/** 读取本地已上传文件记录 { [path]: { size, lastModified } } */
export function loadUploadedMap() {
  try {
    return JSON.parse(localStorage.getItem(UPLOADED_KEY) || '{}') || {}
  } catch {
    return {}
  }
}

/** 保存已上传文件记录（超出 localStorage 容量时静默丢弃） */
export function saveUploadedMap(map) {
  try {
    localStorage.setItem(UPLOADED_KEY, JSON.stringify(map))
  } catch {
    // 容量超限：仅保留最近变更，避免持久化失败影响本次扫描
    try {
      const keys = Object.keys(map)
      const trimmed = {}
      // 保留最后 1000 条
      for (const k of keys.slice(-1000)) trimmed[k] = map[k]
      localStorage.setItem(UPLOADED_KEY, JSON.stringify(trimmed))
    } catch { /* 仍失败则放弃持久化 */ }
  }
}

// ==================== 单次扫描编排 ====================

/**
 * 执行一次浏览器目录扫描导入
 * @param {FileSystemDirectoryHandle} handle 根目录句柄
 * @param {Object} options
 * @param {Object} options.uploadedMap 本地已上传文件记录
 * @param {string} options.collectionBatch 默认采集批次（新建受试者兜底）
 * @param {string} options.collectionScene 默认采集场景（新建受试者兜底）
 * @param {Function} options.onProgress 进度回调
 *   ({ phase: 'scan'|'upload'|'done', current, total, currentFile })
 * @returns {Promise<{newSubjects, uploadedPaths, failures, uploadedMap}>}
 */
export async function runBrowserScan(handle, options = {}) {
  const { onProgress } = options
  // 1. 校验权限
  if (!(await verifyPermission(handle))) {
    throw new Error('未获得目录读取权限，请在浏览器授权弹窗中点击"允许"')
  }
  // 2. 扫描全量文件
  onProgress?.({ phase: 'scan', current: 0, total: 0, currentFile: '扫描目录…' })
  const allFiles = await scanDirectory(handle)
  return runScanFromFiles(allFiles, options)
}

/**
 * 直接基于文件列表执行扫描编排（供不支持 FS Access API 的手动一次性扫描复用）
 * @param {Array<{file: File, path: string, name: string, size: number, lastModified: number}>} allFiles 全量文件
 * @returns {Promise<{newSubjects, uploadedPaths, failures, uploadedMap}>}
 */
export async function runScanFromFiles(allFiles, options = {}) {
  const {
    uploadedMap = {},
    collectionBatch,
    collectionScene,
    onProgress,
  } = options

  // 3. diff 出新增/变更文件（增量扫描，避免全量重传）
  const effectiveMap = { ...uploadedMap }
  const newFiles = diffFiles(allFiles, effectiveMap)

  if (!newFiles.length) {
    onProgress?.({ phase: 'done', current: 0, total: 0, currentFile: '' })
    return { newSubjects: 0, uploadedPaths: [], failures: [], uploadedMap: effectiveMap }
  }

  // 4. 按受试者分组（基于新增文件）
  const groups = groupBySubject(newFiles)
  if (!groups.length) {
    onProgress?.({ phase: 'done', current: 0, total: 0, currentFile: '' })
    return { newSubjects: 0, uploadedPaths: [], failures: [], uploadedMap: effectiveMap }
  }

  // 全量文件按受试者分组（用于补回 userInfo 和新建受试者的数据文件）
  const allGroups = groupBySubject(allFiles)
  const allGroupMap = new Map(allGroups.map((g) => [g.pseudoId, g]))

  // diff 可能过滤掉未变更的 userInfo，从全量文件补回（新建受试者需要）
  for (const g of groups) {
    if (!g.userInfo) g.userInfo = allGroupMap.get(g.pseudoId)?.userInfo || null
  }

  // 5. 恢复受试者缓存
  restoreSubjectCache()

  let newSubjects = 0
  const uploadedPaths = []
  const failures = []
  const updatedMap = { ...effectiveMap }

  // 计算总文件数用于进度
  const totalFiles = groups.reduce((n, g) => n + g.files.length, 0)
  let done = 0

  // 6. 逐受试者处理
  for (const g of groups) {
    let subjectId = await resolveSubjectId(g.pseudoId)

    // 6.1 新建受试者（含解析 userInfo）
    if (!subjectId) {
      const fields = {}
      if (g.userInfo) {
        try {
          const fd = new FormData()
          fd.append('file', g.userInfo.file, g.userInfo.name)
          const res = await parseUserInfoApi(fd)
          Object.assign(fields, res.data?.fields || {})
        } catch (e) {
          failures.push({
            name: `${g.pseudoId}/userInfo.json`,
            reason: '解析失败：' + (e.response?.data?.message || e.message),
          })
        }
      }
      const payload = { pseudo_id: g.pseudoId, status: 'collecting' }
      Object.assign(payload, fields)
      // pseudo_id 以目录名为准，不覆盖
      payload.pseudo_id = g.pseudoId
      if (collectionBatch) payload.collection_batch = collectionBatch
      if (collectionScene) payload.collection_scene = collectionScene
      try {
        const res = await withRetry(() => createSubjectApi(payload))
        subjectId = res.data?.id
        if (subjectId) {
          if (_subjectCache) _subjectCache[g.pseudoId] = subjectId
          newSubjects++
        }
      } catch (e) {
        failures.push({
          name: `受试者 ${g.pseudoId}`,
          reason: '创建失败：' + (e.response?.data?.message || e.message),
        })
        continue // 受试者未建成功，跳过其文件
      }

      // 新建受试者：uploadedMap 中该受试者的缓存必然过期（受试者刚创建，不可能上传过）
      // 从全量文件补回该受试者的所有数据文件
      const allG = allGroupMap.get(g.pseudoId)
      if (allG && allG.files.length > 0 && g.files.length === 0) {
        // 清除该受试者的旧缓存记录
        for (const f of allG.files) {
          delete updatedMap[f.path]
        }
        // 补回数据文件
        g.files = [...allG.files]
      }
    }

    // 6.2 逐文件上传
    for (const f of g.files) {
      onProgress?.({
        phase: 'upload',
        current: done,
        total: totalFiles,
        currentFile: f.path,
      })
      const data_type = detectDataType(f.name)
      try {
        const fd = new FormData()
        fd.append('file', f.file, f.name)
        fd.append('subject_id', subjectId)
        fd.append('data_type', data_type)
        fd.append('layer', 'raw')
        await withRetry(() => uploadAssetApi(fd))
        uploadedPaths.push(f.path)
        updatedMap[f.path] = { size: f.size, lastModified: f.lastModified }
      } catch (e) {
        failures.push({
          name: f.path,
          reason: '上传失败：' + (e.response?.data?.message || e.message),
        })
      }
      done++
    }
  }

  onProgress?.({ phase: 'done', current: done, total: totalFiles, currentFile: '' })
  return { newSubjects, uploadedPaths, failures, uploadedMap: updatedMap }
}
