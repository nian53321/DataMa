import request from '@/utils/request'

export const getDashboardStatsApi = (params) => request.get('/dashboard/stats', { params })
