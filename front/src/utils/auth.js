// 认证信息统一读写封装
// 集中管理 token / refresh_token / userInfo / menus 的 localStorage 存取，
// 避免散落的裸 localStorage 访问；配合短期签名 URL 机制，
// 长期 JWT 只存在于 localStorage 与 Authorization 头，绝不进入 URL。
const TOKEN_KEY = 'token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const USER_INFO_KEY = 'userInfo'
const MENUS_KEY = 'menus'

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
