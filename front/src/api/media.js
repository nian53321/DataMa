import request from '@/utils/request'

// 获取媒体资源短期签名 URL（后端签发，资源绑定、5 分钟过期）
// 返回 Promise<{ url: string }>；用于 <video>/<audio>/<img> 标签加载，
// 避免长期 JWT 进入 URL（防止泄漏到浏览器历史/代理日志/Referer）
export const fetchSignedUrlApi = (params) =>
  request.get('/media/signed-url', { params })
