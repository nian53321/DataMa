import { defineStore } from 'pinia'
import { loginApi, getMeApi } from '@/api/auth'
import {
  getToken, getRefreshToken, setToken, setRefreshToken,
  setUserInfo, getStoredUserInfo, setMenus, getStoredMenus, clearAuth,
} from '@/utils/auth'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: getToken(),
    refreshToken: getRefreshToken(),
    userInfo: getStoredUserInfo(),
    menus: getStoredMenus(),
  }),
  getters: {
    isLogin: (state) => !!state.token,
    role: (state) => state.userInfo?.role || '',
    // 是否拥有某菜单权限
    hasMenu: (state) => (menuKey) => {
      // admin 角色始终拥有全部菜单
      if (state.userInfo?.role === 'admin') return true
      return state.menus.includes(menuKey)
    },
  },
  actions: {
    async login(payload) {
      const res = await loginApi(payload)
      this.token = res.data.token
      this.refreshToken = res.data.refresh_token || ''
      this.userInfo = res.data.user
      this.menus = res.data.menus || []
      setToken(this.token)
      setRefreshToken(this.refreshToken)
      setUserInfo(this.userInfo)
      setMenus(this.menus)
      return res
    },
    async fetchMe() {
      const res = await getMeApi()
      this.userInfo = res.data
      this.menus = res.data.menus || []
      setUserInfo(this.userInfo)
      setMenus(this.menus)
      return res
    },
    logout() {
      this.token = ''
      this.refreshToken = ''
      this.userInfo = null
      this.menus = []
      clearAuth()
    },
  },
})
