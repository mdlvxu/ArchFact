// 应用入口：挂载 Vue 实例并加载全局插件
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import pinia from './stores'
import '@/styles/index.scss'

const app = createApp(App)

app.use(pinia)
app.use(router)

app.mount('#app')
