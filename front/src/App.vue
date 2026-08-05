<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

onMounted(async () => {
  // 应用启动时若已登录，拉取最新用户信息与菜单权限（保证权限变更后刷新即生效）
  if (userStore.token) {
    try {
      await userStore.fetchMe()
    } catch (e) {
      // token 失效等由请求拦截器处理
    }
  }
})
</script>
