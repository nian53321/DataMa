// 隐私信息可见性 store（仅对管理员生效）
//
// 背景：后端对非 admin 角色已在接口层完成真实脱敏；admin 拿到的是明文。
// 本 store 为 admin 提供一个「是否可见隐私信息」的视图开关：
//   showSensitive = false（默认）→ 在渲染层按脱敏规则打码
//   showSensitive = true          → 展示真实明文
//
// 设计要点：
// - 遮罩规则来自后端 /system/desensitize/config，改规则后前端遮罩同步变化
// - 遮罩只发生在展示层，数据本身保持明文（编辑表单不受影响，不会写回脱敏值）
// - 开关状态持久化到 localStorage；因是纯展示计算，切换后可即时生效、无需重新请求
import { defineStore } from 'pinia'
import { getDesensConfigApi } from '@/api/system'
import { maskValue, buildRuleMap } from '@/utils/mask'
import { getStoredShowSensitive, setStoredShowSensitive } from '@/utils/auth'
import { useUserStore } from '@/stores/user'

export const usePrivacyStore = defineStore('privacy', {
  state: () => ({
    // 默认 false = 隐藏隐私信息（打码显示）
    showSensitive: getStoredShowSensitive(),
    // 后端脱敏总开关；关闭时任何角色都不脱敏，前端遮罩同步失效
    enabled: true,
    // field_key -> rule
    ruleMap: {},
    loaded: false,
    loading: false,
  }),
  getters: {
    // 是否具备使用该开关的权限（仅管理员）
    canToggle() {
      return useUserStore().role === 'admin'
    },
    // 按字段遮罩：模板中直接调用 privacy.mask('real_name', row.real_name)
    // 返回函数形式以支持按字段传参；内部读取 state，切换开关会触发重渲染
    mask(state) {
      const userStore = useUserStore()
      return (fieldKey, value) => {
        if (userStore.role !== 'admin') return value // 非 admin：后端已脱敏，前端不再叠加
        if (state.showSensitive) return value // 开关打开：显示明文
        if (!state.enabled) return value // 全局脱敏关闭：不做遮罩
        const rule = state.ruleMap[fieldKey]
        if (!rule) return value // 该字段无启用规则
        return maskValue(value, rule)
      }
    },
  },
  actions: {
    /** 加载脱敏配置（幂等；force=true 强制刷新，用于系统管理页改规则后同步） */
    async loadConfig(force = false) {
      if (this.loading) return
      if (this.loaded && !force) return
      this.loading = true
      try {
        const res = await getDesensConfigApi()
        const data = res?.data || {}
        this.enabled = data.enabled !== false
        this.ruleMap = buildRuleMap(data.rules)
        this.loaded = true
      } catch (e) {
        // 拉取失败时不做遮罩（宁可少遮，也不要误遮出难以理解的乱码）；
        // 仍标记 loaded，避免开关长期处于禁用态
        this.ruleMap = {}
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    /** 设置「是否可见隐私信息」并持久化 */
    setShowSensitive(value) {
      this.showSensitive = !!value
      setStoredShowSensitive(this.showSensitive)
    },
  },
})
