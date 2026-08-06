import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { checkDeploymentReset } from '@/utils/auth'
import './styles/main.scss'

// 在 store/router 初始化前检测：若前端已重新构建/部署，自动清空旧登录缓存
// （旧 token 由旧 JWT_SECRET_KEY 签发已失效），避免浏览器残留旧信息导致"伪登录"
checkDeploymentReset()

const app = createApp(App)

// 注册 Element Plus 图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
