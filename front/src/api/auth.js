import request from '@/utils/request'

export const loginApi = (data) => request.post('/auth/login', data)
export const getMeApi = () => request.get('/auth/me')

// 刷新 token
export function refreshToken() {
  return request.post('/auth/refresh')
}
