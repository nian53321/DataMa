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

// 查询违反「每受试者每类只允许一条」约束的受试者（心电/脑电/音频/个人信息）
// 返回 { pseudo_id: ['ecg', 'audio'] }，供浏览器扫描作废本地记录后重扫验证
export const getSingletonViolationsApi = (pseudoIds) =>
  request.post('/data/assets/singleton-violations', { pseudo_ids: pseudoIds })

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

// 预览导出范围：返回命中资产统计（总数/大小/类型分布/超限标志）与明细
export const exportPreviewApi = (data) =>
  request.post('/data/assets/export/preview', data)

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

// ==================== 批量脱敏（导出复用的前置加工） ====================
//
// 与导出的脱敏实现完全同一套（video/audio/eeg/ecg/userinfo），只是把结果落盘成
// 「脱敏产物」挂在原资产下，导出时可勾选直接复用，避免每次导出都重跑分钟级的
// 视频人脸脱敏。产物在数据湖里仍是 DMEC 密文。

// 查询指定资产已有的脱敏产物（供列表打「已脱敏」标记）
// 返回 { "<asset_id>": { video: {...}, eeg: {...} }, ... }
export const desensitizeExistingApi = (assetIds) =>
  request.post('/data/assets/desensitize/existing', { asset_ids: assetIds })

// 预览批量脱敏：每个资产会被怎么处理（重做 / 复用 / 跳过 / 失败），无副作用
export const desensitizePreviewApi = (data) =>
  request.post('/data/assets/desensitize/preview', data)

// 启动异步批量脱敏任务，返回 task_id（视频是分钟级，必须异步）
export const desensitizeStartApi = (data) =>
  request.post('/data/assets/desensitize/start', data)

// 查询批量脱敏任务进度
export const desensitizeProgressApi = (taskId) =>
  request.get(`/data/assets/desensitize/progress/${taskId}`)

// 列出最近提交的批量脱敏任务（任务状态在服务端 Redis 里，所以"挂到后台"后
// 即使关了弹窗、切了页面、刷新浏览器，回来也能靠它找回未完成的任务继续看进度）
export const desensitizeTasksApi = (params) =>
  request.get('/data/assets/desensitize/tasks', { params })

// 删除某资产的脱敏产物（记录 + 磁盘文件）；modality 可选，只删指定模态
export const desensitizeDeleteArtifactApi = (assetId, modality) =>
  request.delete(`/data/assets/desensitize/artifact/${assetId}`, {
    params: modality ? { modality } : {},
  })
