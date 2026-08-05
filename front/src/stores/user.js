import { defineStore } from 'pinia'
import { loginApi, getMeApi } from '@/api/auth'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    refreshToken: localStorage.getItem('refresh_token') || '',
    userInfo: JSON.parse(localStorage.getItem('userInfo') || 'null'),
    menus: JSON.parse(localStorage.getItem('menus') || '[]'),
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
      localStorage.setItem('token', this.token)
      if (this.refreshToken) {
        localStorage.setItem('refresh_token', this.refreshToken)
      }
      localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
      localStorage.setItem('menus', JSON.stringify(this.menus))
      return res
    },
    async fetchMe() {
      const res = await getMeApi()
      this.userInfo = res.data
      this.menus = res.data.menus || []
      localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
      localStorage.setItem('menus', JSON.stringify(this.menus))
      return res
    },
    logout() {
      this.token = ''
      this.refreshToken = ''
      this.userInfo = null
      this.menus = []
      localStorage.removeItem('token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('userInfo')
      localStorage.removeItem('menus')
    },
  },
})
