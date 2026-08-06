import request from '@/utils/request'

// 检测 Orbbec K4A 设备状态（静默处理，不弹"网络异常"）
export const getOrbbecStatusApi = () =>
  request.get('/orbbec/status', { skipErrorHandler: true })

// 启动/停止实时预览（MJPEG 流，与录制互斥）
export const startOrbbecPreviewApi = () =>
  request.post('/orbbec/preview/start', {}, { skipErrorHandler: true })
export const stopOrbbecPreviewApi = () =>
  request.post('/orbbec/preview/stop', {}, { skipErrorHandler: true })

// 启动后台录制
export const startOrbbecRecordApi = (data) =>
  request.post('/orbbec/record/start', data, { skipErrorHandler: true })

// 停止录制并返回文件路径
export const stopOrbbecRecordApi = () =>
  request.post('/orbbec/record/stop', {}, { skipErrorHandler: true })

// 查询录制后处理状态（预览是否生成）
export const getOrbbecRecordStatusApi = () =>
  request.get('/orbbec/record/status', { skipErrorHandler: true })

// 将已录制的 mkv 入库为数据资产
export const uploadOrbbecRecordApi = (data) => request.post('/orbbec/upload', data)
