import request from '@/utils/request'

// 受试者
export const getSubjectsApi = (params) => request.get('/data/subjects', { params })
// 获取所有不重复的采集批次（用于下拉筛选）
export const getSubjectBatchesApi = () => request.get('/data/subjects/batches')
// 获取所有不重复的采集批次与场景列表（最新优先，用于表单下拉选择）
export const getSubjectBatchesAndScenesApi = () =>
  request.get('/data/subjects/batches-and-scenes')
export const createSubjectApi = (data) => request.post('/data/subjects', data)
export const updateSubjectApi = (id, data) => request.put(`/data/subjects/${id}`, data)
export const deleteSubjectApi = (id) => request.delete(`/data/subjects/${id}`)

// 数据资产
export const getAssetsApi = (params) => request.get('/data/assets', { params })
export const createAssetApi = (data) => request.post('/data/assets', data)
export const updateAssetApi = (id, data) => request.put(`/data/assets/${id}`, data)
export const deleteAssetApi = (id) => request.delete(`/data/assets/${id}`)
export const batchCreateAssetsApi = (data) => request.post('/data/assets/batch', data)
// 真实文件上传（multipart），按受试者分目录存储
export const uploadAssetApi = (formData, onProgress) =>
  request.post('/data/assets/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress,
    timeout: 0, // 不超时，大文件（视频等）上传不受 30s 限制
  })

// 按伪ID批量查询资产导入摘要（浏览器扫描对账：识别平台侧已删除的资产/受试者）
export const getIngestDigestApi = (pseudoIds) =>
  request.post('/data/assets/ingest-digest', { pseudo_ids: pseudoIds })

// 解析 userInfo.json 文件（支持明文/外部加密），返回受试者字段映射
export const parseUserInfoApi = (formData) =>
  request.post('/data/parse-userinfo', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

// 解析 sync_data.json 眼动评估数据（仅返回原始 JSON，不映射字段）
export const parseSyncDataApi = (formData) =>
  request.post('/data/parse-sync-data', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

// 解析量表数据 JSON（MoCA 等），返回原始字段与得分摘要
export const parseScaleApi = (formData) =>
  request.post('/data/parse-scale', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

// 清洗与标准化
export const triggerCleaningApi = (data) => request.post('/data/cleaning/tasks', data)
export const triggerStandardizationApi = (data) =>
  request.post('/data/standardization/tasks', data)

// 操作日志
export const getOperationLogsApi = (params) => request.get('/data/operation-logs', { params })
export const getOperationLogStatsApi = (params) => request.get('/data/operation-logs/stats', { params })

// 数据管理综合统计
export const getDataStatsApi = (params) => request.get('/data/stats', { params })

// 数据快照与版本回滚
export const getSnapshotsApi = (params) => request.get('/data/snapshots', { params })
export const getSnapshotApi = (id) => request.get(`/data/snapshots/${id}`)
export const rollbackSnapshotApi = (id) => request.post(`/data/snapshots/${id}/rollback`)

// 异步导出（三步流程）：start → 轮询 progress → download
// 启动异步导出任务，返回 task_id
export const exportStartApi = (data) =>
  request.post('/data/assets/export/start', data)

// 查询异步导出任务进度
export const exportProgressApi = (taskId) =>
  request.get(`/data/assets/export/progress/${taskId}`)

// 下载已完成的导出 zip（blob）
export const exportDownloadApi = (taskId) =>
  request.get(`/data/assets/export/download/${taskId}`, {
    responseType: 'blob',
    skipErrorHandler: true,
    timeout: 0,
  })
