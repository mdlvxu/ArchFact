import { createRouter, createWebHistory } from 'vue-router'

const workspaceRoute = () => import('@/views/HomeView.vue')

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'extraction',
      component: workspaceRoute,
      meta: { title: '数据提取', workspaceTab: 'Data Extraction' },
    },
    {
      path: '/preview',
      name: 'preview',
      component: workspaceRoute,
      meta: { title: '数据预览', workspaceTab: 'Data Preview' },
    },
    {
      path: '/machine-verification',
      name: 'machine-verification',
      component: workspaceRoute,
      meta: { title: '机器校验', workspaceTab: 'Machine Verification' },
    },
    {
      path: '/gt-evaluation',
      name: 'gt-evaluation',
      component: workspaceRoute,
      meta: { title: 'GT 评测', workspaceTab: 'Cross-report Evaluation' },
    },
    {
      path: '/insights',
      name: 'insights',
      component: workspaceRoute,
      meta: { title: '数据洞察', workspaceTab: 'Data Insights' },
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
