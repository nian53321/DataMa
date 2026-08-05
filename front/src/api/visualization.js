import request from '@/utils/request'

// 可视化
export const getSubjectOverviewApi = (subjectId) =>
  request.get(`/visualization/subjects/${subjectId}`)
export const alignModalitiesApi = (data) => request.post('/visualization/align', data)
export const getTimelineApi = (subjectId) =>
  request.get(`/visualization/timeline/${subjectId}`)

// 解析指定脑电数据资产（支持 CSV/JSON 格式）
export const getEegAssetApi = (assetId) =>
  request.get(`/visualization/eeg-asset/${assetId}`)

// 解析指定心电数据资产（CSV 格式）
export const getEcgAssetApi = (assetId) =>
  request.get(`/visualization/ecg-asset/${assetId}`)

// 解析指定眼动数据资产（sync_data.json 指标）
export const getEyeAssetApi = (assetId) =>
  request.get(`/visualization/eye-asset/${assetId}`)

// 解析指定量表数据资产（MoCA 等）
export const getScaleAssetApi = (assetId) =>
  request.get(`/visualization/scale-asset/${assetId}`)
