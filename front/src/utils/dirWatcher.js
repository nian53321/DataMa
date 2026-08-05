/**
 * 浏览器目录监控工具（File System Access API）
 *
 * 能力：
 * - 目录句柄 IndexedDB 持久化，刷新/重开页面后免重新选择（仍需重新授权）
 * - 权限校验、递归扫描、文件 diff（基于大小+修改时间）
 *
 * 限制：
 * - 仅 Chrome/Edge/Opera 86+ 支持 showDirectoryPicker，Firefox/Safari 不支持
 * - 需要安全上下文（HTTPS 或 localhost），通过 IP+HTTP 访问时不可用
 * - 句柄持久化后，浏览器策略仍可能要求每次会话重新授权（queryPermission/requestPermission）
 * - 页面关闭后定时器停止，无法后台扫描
 */

const DB_NAME = 'dm_dir_watcher'
const STORE = 'handles'
const KEY = 'monitor_root'

/**
 * 是否支持 File System Access API 目录选择
 * 需要：Chrome/Edge 86+ 且安全上下文（HTTPS 或 localhost）
 */
export const supportsFsAccess = () =>
  typeof window !== 'undefined' &&
  'showDirectoryPicker' in window &&
  window.isSecureContext

/**
 * 获取不支持原因（用于 UI 提示）
 */
export const getUnsupportedReason = () => {
  if (typeof window === 'undefined') return ''
  if (window.isSecureContext && 'showDirectoryPicker' in window) return ''
  if (!window.isSecureContext) {
    return '当前页面非安全上下文（需通过 HTTPS 或 localhost 访问）。请使用 https:// 地址或通过 http://localhost:8080 访问'
  }
  return '当前浏览器不支持 File System Access API，需 Chrome/Edge 86+'
}

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE)
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/** 持久化目录句柄到 IndexedDB */
export async function saveHandle(handle) {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).put(handle, KEY)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

/** 读取已持久化的目录句柄（可能因会话失效需要重新授权） */
export async function loadHandle() {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const req = tx.objectStore(STORE).get(KEY)
    req.onsuccess = () => resolve(req.result || null)
    req.onerror = () => reject(req.error)
  })
}

/** 清除持久化的目录句柄 */
export async function clearHandle() {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).delete(KEY)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

/** 校验/请求目录读取权限，返回是否已授权 */
export async function verifyPermission(handle, write = false) {
  if (!handle) return false
  const opts = { mode: write ? 'readwrite' : 'read' }
  try {
    if ((await handle.queryPermission(opts)) === 'granted') return true
    if ((await handle.requestPermission(opts)) === 'granted') return true
  } catch {
    return false
  }
  return false
}

/** 弹出目录选择器并持久化句柄 */
export async function pickDirectory() {
  if (!supportsFsAccess()) {
    throw new Error('当前浏览器不支持目录选择（需 Chrome/Edge 86+），请改用「手动一次性扫描」')
  }
  const handle = await window.showDirectoryPicker({ mode: 'read' })
  await saveHandle(handle)
  return handle
}

/**
 * 递归扫描目录句柄，返回所有文件
 * @param {FileSystemDirectoryHandle} handle 根目录句柄
 * @returns {Promise<Array<{file: File, path: string, name: string, size: number, lastModified: number}>>}
 *   path 为相对根目录的路径，用 / 分隔，第一段为受试者子文件夹名
 */
export async function scanDirectory(handle) {
  const files = []
  async function walk(dirHandle, prefix = '') {
    for await (const entry of dirHandle.values()) {
      const path = prefix ? `${prefix}/${entry.name}` : entry.name
      if (entry.kind === 'file') {
        try {
          const file = await entry.getFile()
          files.push({
            file,
            path,
            name: entry.name,
            size: file.size,
            lastModified: file.lastModified,
          })
        } catch {
          // 单个文件读取失败（权限/占用），跳过不中断整体扫描
        }
      } else if (entry.kind === 'directory') {
        await walk(entry, path)
      }
    }
  }
  await walk(handle)
  return files
}

/**
 * 计算新增/变更文件（基于本地已上传记录）
 * @param {Array} allFiles scanDirectory 返回的文件列表
 * @param {Object} uploadedMap { [path]: { size, lastModified } }
 * @returns {Array} 需要处理的文件（新增或大小/修改时间变化）
 */
export function diffFiles(allFiles, uploadedMap = {}) {
  return allFiles.filter((f) => {
    const rec = uploadedMap[f.path]
    if (!rec) return true
    return rec.size !== f.size || rec.lastModified !== f.lastModified
  })
}
