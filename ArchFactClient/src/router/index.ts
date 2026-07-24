import { createRouter, createWebHistory } from 'vue-router'

// 路由配置：页面级组件统一放在 views 目录
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: '首页' },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { title: '页面不存在' },
    },
  ],
})

// 路由切换时同步更新浏览器标题
router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : 'ArchFact'
  document.title = `${title} - ArchFact`
})

export default router
