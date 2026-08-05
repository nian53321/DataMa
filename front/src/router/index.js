import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '工作台', icon: 'Odometer', menuKey: 'dashboard' },
      },
      {
        path: 'data',
        name: 'DataCleaning',
        component: () => import('@/views/data/index.vue'),
        meta: { title: '数据清洗及标准化', icon: 'Brush', menuKey: 'data' },
      },
      {
        path: 'annotation',
        name: 'Annotation',
        component: () => import('@/views/annotation/index.vue'),
        meta: { title: '数据标注', icon: 'EditPen', menuKey: 'annotation' },
      },
      {
        path: 'label',
        name: 'Label',
        component: () => import('@/views/label/index.vue'),
        meta: { title: '标签管理', icon: 'PriceTag', menuKey: 'label' },
      },
      {
        path: 'management',
        name: 'Management',
        component: () => import('@/views/management/index.vue'),
        meta: { title: '数据管理', icon: 'Coin', menuKey: 'management' },
      },
      {
        path: 'visualization',
        name: 'Visualization',
        component: () => import('@/views/visualization/index.vue'),
        meta: { title: '数据对齐与可视化', icon: 'DataLine', menuKey: 'visualization' },
      },
      {
        path: 'system',
        name: 'System',
        component: () => import('@/views/system/index.vue'),
        meta: { title: '系统管理', icon: 'Setting', menuKey: 'system' },
      },
      {
        path: 'operation-log',
        name: 'OperationLog',
        component: () => import('@/views/operation-log/index.vue'),
        meta: { title: '操作日志', icon: 'Document', menuKey: 'operation_log' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/404.vue'),
    meta: { title: '页面不存在', public: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 多模态数据平台`
    : '多模态数据标注与分析处理平台'

  const userStore = useUserStore()

  // 已登录用户访问 /login 自动跳首页
  if (to.path === '/login' && userStore.token) {
    next('/dashboard')
    return
  }

  // 公开页面或已登录
  if (to.meta.public || userStore.token) {
    // 菜单权限校验：基于管理员配置的角色菜单授权
    if (to.meta.menuKey && !userStore.hasMenu(to.meta.menuKey)) {
      next('/dashboard')
      return
    }
    next()
  } else {
    // 未登录跳转登录页，携带 redirect 参数
    next({ path: '/login', query: { redirect: to.fullPath } })
  }
})

export default router
