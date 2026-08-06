import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { getToken, getRefreshToken, setToken, clearAuth } from '@/utils/auth'

const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
})

// 请求拦截：携带 token
service.interceptors.request.use(
  (config) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

let isRefreshing = false
let failedQueue = []

const processQueue = (error, token = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token)
    }
  })
  failedQueue = []
}

const redirectToLogin = () => {
  clearAuth()
  router.push('/login')
}

// 响应拦截：统一处理业务码与 token 刷新
service.interceptors.response.use(
  (response) => {
    const res = response.data
    if (res.code && !(res.code >= 200 && res.code < 300)) {
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message || 'Error'))
    }
    return res
  },
  async (error) => {
    const status = error.response?.status
    const reqUrl = error.config?.url || ''
    const originalRequest = error.config

    // 登录接口的 401 表示用户名或密码错误
    if (status === 401 && reqUrl.includes('/auth/login')) {
      ElMessage.error(error.response?.data?.message || '用户名或密码错误')
      return Promise.reject(error)
    }

    // refresh 接口本身 401，说明 refresh token 也过期了
    if (status === 401 && reqUrl.includes('/auth/refresh')) {
      isRefreshing = false
      processQueue(new Error('refresh failed'), null)
      ElMessage.error('登录已过期，请重新登录')
      redirectToLogin()
      return Promise.reject(error)
    }

    // 其他 401：尝试用 refresh token 刷新
    if (status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        // 已有刷新请求进行中，排队等待；刷新失败时排队链同步失败
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject })
        }).then((token) => {
          if (typeof token !== 'string' || !token) {
            return Promise.reject(new Error('token 刷新失败'))
          }
          originalRequest.headers.Authorization = `Bearer ${token}`
          return service(originalRequest)
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        const refreshToken = getRefreshToken()
        if (!refreshToken) {
          throw new Error('no refresh token')
        }
        const res = await axios.post(
          (import.meta.env.VITE_API_BASE_URL || '/api') + '/auth/refresh',
          {},
          { headers: { Authorization: `Bearer ${refreshToken}` } }
        )
        const newToken = res?.data?.data?.token
        if (typeof newToken !== 'string' || !newToken) {
          throw new Error('刷新响应缺少 token')
        }
        setToken(newToken)
        processQueue(null, newToken)
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return service(originalRequest)
      } catch (refreshError) {
        processQueue(refreshError, null)
        ElMessage.error('登录已过期，请重新登录')
        redirectToLogin()
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    // 调用方可通过 config.skipErrorHandler=true 自行处理错误（如 blob 响应解析）
    if (!originalRequest?.skipErrorHandler) {
      ElMessage.error(error.response?.data?.message || '网络异常')
    }
    return Promise.reject(error)
  }
)

export default service
