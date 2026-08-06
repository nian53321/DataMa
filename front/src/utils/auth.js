// 认证信息统一读写封装
// 集中管理 token / refresh_token / userInfo / menus 的 localStorage 存取，
// 避免散落的裸 localStorage 访问；配合短期签名 URL 机制，
// 长期 JWT 只存在于 localStorage 与 Authorization 头，绝不进入 URL。
const TOKEN_KEY = 'token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const USER_INFO_KEY = 'userInfo'
const MENUS_KEY = 'menus'
// 部署版本标记：由 vite define 注入构建时间戳，用于检测前端是否已重新构建/部署
const BUILD_TIME_KEY = 'app_build_time'
const APP_BUILD_TIME = __APP_BUILD_TIME__

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const getRefreshToken = () => localStorage.getItem(REFRESH_TOKEN_KEY) || ''
export const setToken = (token) => {
  if (token) localStorage.setItem(TOKEN_KEY, token)
}
export const setRefreshToken = (token) => {
  if (token) localStorage.setItem(REFRESH_TOKEN_KEY, token)
}
export const setUserInfo = (info) => {
  if (info != null) localStorage.setItem(USER_INFO_KEY, JSON.stringify(info))
}
export const getStoredUserInfo = () => {
  try {
    return JSON.parse(localStorage.getItem(USER_INFO_KEY) || 'null')
  } catch {
    return null
  }
}
export const setMenus = (menus) => {
  if (menus != null) localStorage.setItem(MENUS_KEY, JSON.stringify(menus))
}
export const getStoredMenus = () => {
  try {
    return JSON.parse(localStorage.getItem(MENUS_KEY) || '[]')
  } catch {
    return []
  }
}
export const clearAuth = () => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(USER_INFO_KEY)
  localStorage.removeItem(MENUS_KEY)
}

// 检测前端是否重新构建/部署。构建时间戳变化说明新版本已上线，
// 旧 token 由旧 JWT_SECRET_KEY 签发必然失效（接口会 422/401），
// 且旧 userInfo/menus 与当前部署的数据可能不一致，必须清空让用户重新登录。
// 在应用初始化（store/router 使用前）调用，返回 true 表示本次清空了旧缓存。
export const checkDeploymentReset = () => {
  const current = String(APP_BUILD_TIME)
  const stored = localStorage.getItem(BUILD_TIME_KEY)
  if (stored === current) return false
  clearAuth()
  localStorage.setItem(BUILD_TIME_KEY, current)
  return true
}
