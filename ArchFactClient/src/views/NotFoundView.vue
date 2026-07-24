<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from '@/i18n'

const router = useRouter()
const { t } = useI18n()

/** 返回首页 */
function goHome() {
  router.push({ name: 'home' })
}
</script>

<template>
  <!-- 404 页面：访问不存在的路由时展示 -->
  <div class="not-found">
    <el-result icon="warning" title="404" :sub-title="t('notFound.message')">
      <template #extra>
        <el-button type="primary" @click="goHome">{{ t('notFound.home') }}</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped lang="scss">
.not-found {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}
</style>
