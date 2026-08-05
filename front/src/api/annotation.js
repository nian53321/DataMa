import request from '@/utils/request'

export const getTasksApi = (params) => request.get('/annotation/tasks', { params })
export const createTaskApi = (data) => request.post('/annotation/tasks', data)
export const batchCreateTasksApi = (data) => request.post('/annotation/tasks/batch', data)
export const updateTaskApi = (taskId, data) => request.put(`/annotation/tasks/${taskId}`, data)
export const deleteTaskApi = (taskId) => request.delete(`/annotation/tasks/${taskId}`)
export const getTaskDetailApi = (taskId) => request.get(`/annotation/tasks/${taskId}`)
export const getLabelsApi = (params) => request.get('/annotation/labels', { params })
export const preAnnotateApi = (taskId) =>
  request.post(`/annotation/tasks/${taskId}/pre-annotate`)
export const assignTaskApi = (taskId, data) =>
  request.post(`/annotation/tasks/${taskId}/assign`, data)
export const submitAnnotationApi = (taskId, data) =>
  request.post(`/annotation/tasks/${taskId}/annotate`, data)
export const reviewTaskApi = (taskId, data) =>
  request.post(`/annotation/tasks/${taskId}/review`, data)
export const getQualityDashboardApi = () =>
  request.get('/annotation/quality/dashboard')
export const getAnnotatorsApi = () => request.get('/annotation/annotators')
export const getAvailableAssetsApi = (params) =>
  request.get('/annotation/available-assets', { params })
export const getTaskGroupApi = (groupId) =>
  request.get(`/annotation/tasks/group/${groupId}`)
export const getMyTasksApi = (params) =>
  request.get('/annotation/my-tasks', { params })
export const batchAssignApi = (data) =>
  request.post('/annotation/tasks/batch-assign', data)
export const batchDeleteTasksApi = (data) =>
  request.post('/annotation/tasks/batch-delete', data)
