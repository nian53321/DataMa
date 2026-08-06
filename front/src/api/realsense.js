import request from '@/utils/request'

// Intel RealSense D455f 深度相机（接口协议与 orbbec 一致，子进程隔离 pyrealsense2）

// 检测 RealSense 设备状态（静默处理，不弹"网络异常"）
export const getRealSenseStatusApi = () =>
  request.get('/realsense/status', { skipErrorHandler: true })

// 启动/停止实时预览（MJPEG 流）
export const startRealSensePreviewApi = () =>
  request.post('/realsense/preview/start', {}, { skipErrorHandler: true })
export const stopRealSensePreviewApi = () =>
  request.post('/realsense/preview/stop', {}, { skipErrorHandler: true })

// 启动/停止后台录制
export const startRealSenseRecordApi = (data) =>
  request.post('/realsense/record/start', data, { skipErrorHandler: true })
export const stopRealSenseRecordApi = () =>
  request.post('/realsense/record/stop', {}, { skipErrorHandler: true })

// 查询录制后处理状态（预览是否生成）
export const getRealSenseRecordStatusApi = () =>
  request.get('/realsense/record/status', { skipErrorHandler: true })

// 将录制的 color.mp4 入库为数据资产
export const uploadRealSenseRecordApi = (data) => request.post('/realsense/upload', data)
