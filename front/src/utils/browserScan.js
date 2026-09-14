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
 * - 每次扫描与后端资产摘要对账（/data/assets/ingest-digest），
 *   作废平台侧已删除的资产/受试者对应的本地记录，保证删除过的文件可重新入库
 * - 后端 upload_asset 本身有幂等去重（同受试者+同模态+同原始文件名+同原始大小），双保险
 */
import { scanDirectory, verifyPermission, diffFiles } from '@/utils/dirWatcher'
import { splitByObservation } from '@/utils/writeObserve'
import {
  parseUserInfoApi, createSubjectApi, updateSubjectApi, uploadAssetApi,
  getSubjectsApi, getIngestDigestApi,
} from '@/api/data'

// 伪ID合法格式：3-64位字母/数字/下划线/短横线（与后端 scanner 一致）
const PSEUDO_ID_RE = /^[A-Za-z0-9_\-]{3,64}$/

// 单文件上传大小上限（与后端 MAX_CONTENT_LENGTH 2GB 一致）
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

// 写入观察期基线：{ [path]: { size, lastModified } }
// 判据实现见 utils/writeObserve.js（与后端 scanner._observe_file_state 同口径）——
// 首见文件只登记基线、不上传；下一轮扫描读到**完全相同**的（大小 + 修改时间）
// 才认为写入已结束并上传。观察期天然等于一个自动扫描周期，不需要额外的绝对秒数下限。
const PENDING_KEY = 'browser_scan_pending_map'

// 跳过的密钥文件（小写比对）；userInfo 需作为 json 资产入库，不在跳过清单
const META_FILES = new Set([
  '密钥.txt', 'key.txt', 'secret.txt',
])

/**
 * 原始文件名归一化：去相对路径前缀 + 去 .enc 后缀 + 小写
 * 与后端 app/utils/source_identity.normalize_original_filename 口径一致，
 * 用于「本地已上传记录 ↔ 后端资产摘要」对账时消除同一文件的形态差异。
 */
function normName(name) {
  if (!name) return ''
  let n = String(name).trim().replace(/\\/g, '/').split('/').pop() || ''
  if (n.toLowerCase().endsWith('.enc')) n = n.slice(0, -4)
  return n.toLowerCase()
}

/** 读取写入观察期基线 */
export function loadPendingMap() {
  try {
    return JSON.parse(localStorage.getItem(PENDING_KEY) || '{}') || {}
  } catch {
    return {}
  }
}

/** 保存写入观察期基线（容量超限时静默丢弃） */
export function savePendingMap(map) {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(map))
  } catch { /* localStorage 不可用时静默 */ }
}

/** 清空写入观察期基线（移除监控目录时调用） */
export function clearPendingMap() {
  try {
    localStorage.removeItem(PENDING_KEY)
  } catch { /* 忽略 */ }
}

// localStorage 持久化 key
const UPLOADED_KEY = 'browser_scan_uploaded_map'
const SUBJECT_CACHE_KEY = 'browser_scan_subject_cache'
// 受试者信息同步指纹 { [pseudoId]: 'size-lastModified' }：记录上次从
// userInfo.json 同步进受试者表的文件指纹，指纹没变则跳过同步
const USERINFO_SYNC_KEY = 'browser_scan_userinfo_sync'

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
  if (stripped === 'userinfo.json') return 'json'
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
      map.get(pseudoId).userInfo = f        // 仍用于解析受试者字段
      map.get(pseudoId).files.push(f)       // 同时作为 json 数据资产入库上传
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

/** 从后端实时确认受试者ID（不信任本地缓存，处理"受试者已被删除"场景） */
async function resolveSubjectId(pseudoId) {
  // 受试者可能在平台被删除后需重新扫描创建：本地缓存里的旧 id 已失效，
  // 必须实时向后端确认该伪ID是否仍存在——存在返回 id，不存在返回 null（走"新建受试者"分支）。
  const res = await getSubjectsApi({ page: 1, page_size: 50, keyword: pseudoId })
  const hit = (res.data?.items || []).find((s) => s.pseudo_id === pseudoId)
  const id = hit?.id || null
  if (_subjectCache) _subjectCache[pseudoId] = id // 回填缓存（仅作展示，不再影响存在性判断）
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

// ==================== 受试者信息同步 ====================

/** 读取受试者信息同步指纹 */
function loadUserinfoSyncMap() {
  try {
    return JSON.parse(localStorage.getItem(USERINFO_SYNC_KEY) || '{}') || {}
  } catch {
    return {}
  }
}

/** 保存受试者信息同步指纹（容量超限时静默丢弃） */
function saveUserinfoSyncMap(map) {
  try {
    localStorage.setItem(USERINFO_SYNC_KEY, JSON.stringify(map))
  } catch { /* localStorage 不可用时静默 */ }
}

/**
 * 从 userInfo.json 同步已存在受试者的信息（列表信息随录入的 userinfo 更新）
 *
 * 背景：受试者创建时解析过一次 userinfo，之后采集端修改 userinfo 并重新扫描，
 * 受试者表的信息不会更新，列表一直是旧值。
 *
 * 策略：
 * - 以文件指纹（size+lastModified）缓存上次同步内容，指纹没变跳过，
 *   每轮扫描的指纹比对是纯内存操作，不产生网络请求
 * - 首次运行（本地无缓存）会对每个有 userInfo 的受试者做一次全量同步，
 *   顺带修复历史存量数据（userinfo 已入库但受试者信息陈旧）
 * - 只更新基本信息字段；伪ID以目录名为准、批次/场景保留平台侧配置不覆盖
 * - 受试者尚不存在时记指纹即跳过（后续新建流程解析的就是同一文件）
 */
async function syncSubjectInfoFromUserinfo(allGroups) {
  const result = { updated: 0, failures: [] }
  const withInfo = allGroups.filter((g) => g.userInfo)
  if (!withInfo.length) return result

  const syncMap = loadUserinfoSyncMap()
  let dirty = false
  for (const g of withInfo) {
    const fp = `${g.userInfo.size || 0}-${g.userInfo.lastModified || 0}`
    if (syncMap[g.pseudoId] === fp) continue
    // 受试者不存在：记指纹跳过（新建流程会解析当前文件填充信息）
    let subjectId = await resolveSubjectId(g.pseudoId)
    if (!subjectId) {
      syncMap[g.pseudoId] = fp
      dirty = true
      continue
    }
    try {
      const fd = new FormData()
      fd.append('file', g.userInfo.file, g.userInfo.name)
      const res = await parseUserInfoApi(fd)
      const fields = { ...(res.data?.fields || {}) }
      // 伪ID以目录名为准；批次/场景是平台侧配置，不随 userinfo 覆盖
      delete fields.pseudo_id
      delete fields.collection_batch
      delete fields.collection_scene
      if (Object.keys(fields).length) {
        await updateSubjectApi(subjectId, fields)
        result.updated++
      }
      syncMap[g.pseudoId] = fp
      dirty = true
    } catch (e) {
      // 失败不记指纹，下一轮扫描重试
      result.failures.push({
        name: `${g.pseudoId}/userInfo.json`,
        reason: '受试者信息同步失败：' + (e.response?.data?.message || e.message),
      })
    }
  }
  if (dirty) saveUserinfoSyncMap(syncMap)
  return result
}

// ==================== 与后端资产对账 ====================

/**
 * 作废「后端已不存在对应资产」的本地已上传记录（就地修改 effectiveMap）
 *
 * localStorage 的已上传记录无法感知平台侧删除动作：资产删除/受试者级联
 * 删除后，磁盘上未变化的文件仍会命中本地记录被 diff 跳过，表现为
 * "删除过的文件再也扫不到"。按（原始文件名+原始大小，与后端 upload_asset
 * 幂等键一致）与后端资产摘要对账，失效记录作废后由 diffFiles 重新发现：
 * - 单个资产被删 → 该文件重新上传入库
 * - 受试者被删（级联删资产）→ 该受试者全部记录作废，触发重建受试者 + 全量上传
 *
 * 对账失败（网络错误/旧版后端无此接口）时静默降级为纯本地增量，不阻断扫描。
 */
async function reconcileUploadedMap(effectiveMap) {
  // 被作废的路径：调用方据此同步清理写入观察期基线，让这些文件重新以
  // 「首见」身份进入观察期，而不是拿着旧读数直接放行。
  const invalidated = []
  const paths = Object.keys(effectiveMap)
  if (!paths.length) return invalidated
  const pseudoIds = [...new Set(
    paths.map((p) => p.split('/')[0]).filter(Boolean)
  )]
  let digest
  try {
    const res = await getIngestDigestApi(pseudoIds)
    digest = res.data || {}
  } catch {
    return invalidated
  }
  for (const p of paths) {
    const segs = p.split('/')
    const entries = digest[segs[0]]
    const fname = segs[segs.length - 1]
    const { size } = effectiveMap[p]
    // 摘要条目缺 original_size（老数据）时仅按文件名匹配，保守视为仍在库，
    // 避免作废重传与库内既有资产形成重复记录
    const hit = Array.isArray(entries) && entries.some(
      ([fn, sz]) => normName(fn) === normName(fname) && (sz == null || sz === size)
    )
    if (!hit) {
      delete effectiveMap[p]
      invalidated.push(p)
    }
  }
  return invalidated
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
 * @param {Object} [options]
 * @param {boolean} [options.observe=true] 是否启用写入观察期（首见只登记、下轮未变才上传）
 * @returns {Promise<{newSubjects, uploadedPaths, failures, uploadedMap, updatedSubjects, observingCount}>}
 */
export async function runScanFromFiles(allFiles, options = {}) {
  const {
    uploadedMap = {},
    collectionBatch,
    collectionScene,
    onProgress,
    // 写入观察期开关：默认开启（首见文件只登记、下一轮读数未变才上传）。
    // 仅用于单测/调试关闭。
    observe = true,
  } = options

  // 3. diff 出新增/变更文件（增量扫描，避免全量重传）
  const effectiveMap = { ...uploadedMap }
  // 清理已上传记录中「当前扫描树已不存在」的路径（目录/文件被删除、受试者目录被移除等）。
  // 若不清理，陈旧记录会让删除后重新出现的同名目录/文件被永久判定为"已上传"而跳过，
  // 表现为"目录删掉后重新扫描扫不到"。这里每次扫描同步一次，记录只保留当前仍存在的文件。
  const currentPaths = new Set(allFiles.map((f) => f.path))
  for (const p of Object.keys(effectiveMap)) {
    if (!currentPaths.has(p)) delete effectiveMap[p]
  }
  // 与后端资产对账：平台侧删除过的资产/受试者，本地记录未感知会永久跳过
  // 这些文件，作废失效记录后由 diff 重新发现（重新上传入库/重建受试者）
  const invalidatedPaths = await reconcileUploadedMap(effectiveMap)

  // 4. 写入观察期（双轮读数比对，观察期 = 一个自动扫描周期）
  //
  // 首见文件只登记基线不上传；下一轮读到完全相同的（大小+修改时间）才上传。
  // 这样目录被整体拷入 / 采集端仍在录制时，半成品不会先入库、完整版再入库一条。
  // 基线持久化在 localStorage：页面刷新、路由切换都不丢，只有「移除目录」才清空。
  const pending = observe ? loadPendingMap() : {}
  // 基线里已不在当前扫描树的路径直接丢弃，避免无界增长
  for (const p of Object.keys(pending)) {
    if (!currentPaths.has(p)) delete pending[p]
  }
  // 资产在平台侧被删过的文件：基线读数是上一轮（甚至上一次会话）留下的，
  // 若沿用会让这些文件在第一轮就直接判稳、立即上传。清掉基线使其重新观察一轮
  //（受试者被删后重扫的场景，否则"删受试者 → 重新扫描"会立刻全量重传）。
  for (const p of invalidatedPaths) delete pending[p]

  // 判据：首见登记基线，下一轮读数完全一致（且不在 30s 静默窗口内）才放行
  const { ready: newFiles, observing, pending: nextPending } = splitByObservation(
    diffFiles(allFiles, effectiveMap), pending, { observe }
  )
  if (observe) savePendingMap(nextPending)

  // 全量文件按受试者分组（供受试者信息同步、补回 userInfo 和新建受试者的数据文件）
  const allGroups = groupBySubject(allFiles)
  const allGroupMap = new Map(allGroups.map((g) => [g.pseudoId, g]))

  // 恢复受试者缓存（信息同步与建受试者都需要）
  restoreSubjectCache()

  // 受试者信息同步：从 userInfo.json 刷新已存在受试者的信息（放在新增文件
  // 判断之前——即使本轮没有新文件，userinfo 变化/存量陈旧也能同步）。
  // 指纹增量：每轮纯内存比对，只有指纹变化/首次才发起解析与更新请求
  const infoSync = await syncSubjectInfoFromUserinfo(allGroups)
  const failures = [...infoSync.failures]
  let updatedSubjects = infoSync.updated

  if (!newFiles.length && !observing.length) {
    onProgress?.({ phase: 'done', current: 0, total: 0, currentFile: '' })
    return {
      newSubjects: 0, uploadedPaths: [], failures,
      uploadedMap: effectiveMap, updatedSubjects, observingCount: 0,
    }
  }

  // 5. 按受试者分组
  // 首见受试者目录时本轮无文件可传，但仍需创建受试者（目录名 = 伪ID），
  // 所以分组取「有待上传文件的」∪「仅在观察期的」，后者 files 为空。
  const readyGroups = groupBySubject(newFiles)
  const readyMap = new Map(readyGroups.map((g) => [g.pseudoId, g]))
  const groups = []
  const groupedIds = new Set()
  for (const g of [...groupBySubject(observing), ...readyGroups]) {
    if (groupedIds.has(g.pseudoId)) continue
    groupedIds.add(g.pseudoId)
    groups.push({
      pseudoId: g.pseudoId,
      userInfo: g.userInfo,
      files: readyMap.get(g.pseudoId)?.files || [],
    })
  }
  if (!groups.length) {
    onProgress?.({ phase: 'done', current: 0, total: 0, currentFile: '' })
    return {
      newSubjects: 0, uploadedPaths: [], failures,
      uploadedMap: effectiveMap, updatedSubjects, observingCount: 0,
    }
  }

  // diff 可能过滤掉未变更的 userInfo，从全量文件补回（新建受试者需要）
  for (const g of groups) {
    if (!g.userInfo) g.userInfo = allGroupMap.get(g.pseudoId)?.userInfo || null
  }

  let newSubjects = 0
  const uploadedPaths = []
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

      // 新建受试者：本地 uploadedMap 中该受试者的记录必然过期（受试者刚创建，
      // 旧资产已随受试者删除），清掉陈旧记录让这些文件下一轮重新被 diff 发现。
      //
      // 这里**只清记录、不补文件**。曾经的做法是把 allG.files 整体补进 g.files
      // 直接上传，效果是：首见受试者时该组文件全在写入观察期（g.files 为空），
      // 于是命中本分支被强行全量上传 —— 双轮判据被完全绕过，表现为
      //「点击开始监控后日志里 create subject 与全部 upload 同秒完成」。
      // 正确行为是：本轮只建受试者（files 为空，不上传），下一轮读数稳定才上传。
      const allG = allGroupMap.get(g.pseudoId)
      if (allG && allG.files.length > 0 && g.files.length === 0) {
        for (const f of allG.files) delete updatedMap[f.path]
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
      if (f.size > MAX_UPLOAD_BYTES) {
        failures.push({
          name: f.path,
          reason: '文件超过 2GB 上限，已跳过',
        })
        done++
        continue
      }
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
  return {
    newSubjects,
    uploadedPaths,
    failures,
    uploadedMap: updatedMap,
    updatedSubjects,
    // 本轮仍处于写入观察期、未上传的文件数（供 UI 提示"正在观察 N 个文件"）。
    // 早期只在两个提前返回分支里给了该字段，正常完成路径漏了 —— 表现为
    // "首见受试者只建受试者不上传"这种最典型的场景反而看不到观察期提示。
    observingCount: observing.length,
  }
}
