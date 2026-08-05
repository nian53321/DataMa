import request from '@/utils/request'

// 标签库管理
export const getLabelListApi = (params) => request.get('/annotation/labels-manage', { params })
export const createLabelApi = (data) => request.post('/annotation/labels-manage', data)
export const updateLabelApi = (id, data) => request.put(`/annotation/labels-manage/${id}`, data)
export const deleteLabelApi = (id) => request.delete(`/annotation/labels-manage/${id}`)

// 标签使用统计
export const getLabelUsageApi = (params) => request.get('/annotation/labels-manage/usage', { params })

// 标签替换（将某标签下所有标注替换为目标标签）
export const replaceLabelApi = (id, data) => request.post(`/annotation/labels-manage/${id}/replace`, data)

// 样本标签情况查看（按数据资产/受试者聚合）
export const getSampleLabelsApi = (params) => request.get('/annotation/sample-labels', { params })
