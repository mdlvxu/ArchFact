<script setup lang="ts">
import { Delete, Download, RefreshRight, VideoPause } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { buildProcessingMetrics } from '@/domain/processing-metrics'
import { useI18n } from '@/i18n'

/** 处理日志记录 */
export interface ProcessLog {
  id: number
  status: 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR'
  text: string
}

interface Props {
  progress: number
  logs: ProcessLog[]
  stopped: boolean
  running: boolean
  startedAt: string | null
  endedAt: string | null
  processedPages: number
  totalPages: number
  failedPages: number
}

const props = defineProps<Props>()
const { locale, t } = useI18n()

const emit = defineEmits<{
  clear: []
  export: []
  stop: []
  retry: []
}>()

const nowMs = ref(Date.now())
let clockTimer: number | undefined

const metrics = computed(() => buildProcessingMetrics({
  startedAt: props.startedAt,
  endedAt: props.endedAt,
  processedPages: props.processedPages,
  totalPages: props.totalPages,
  progress: props.progress,
  running: props.running,
  nowMs: nowMs.value,
  locale: locale.value,
}))

onMounted(() => {
  clockTimer = globalThis.setInterval(() => {
    if (props.running) nowMs.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (clockTimer !== undefined) globalThis.clearInterval(clockTimer)
})
</script>

<template>
  <!-- 处理进度区：展示任务状态、实时日志和快捷操作 -->
  <section class="processing panel">
    <h2 class="panel-title">
      {{ t('processing.title') }}
    </h2>
    <el-progress
      class="processing__progress"
      :percentage="progress"
      :show-text="false"
      :stroke-width="11"
      color="#b76616"
    />

    <div class="processing__body">
      <div
        class="log-box"
        :aria-label="t('processing.title')"
      >
        <p
          v-for="log in logs"
          :key="log.id"
          class="log-line"
        >
          <span
            class="log-status"
            :class="`log-status--${log.status.toLowerCase()}`"
          >
            {{ log.status }}
          </span>
          <span>{{ log.text }}</span>
        </p>
        <p
          v-if="logs.length === 0"
          class="log-empty"
        >
          {{ t('processing.noLogs') }}
        </p>
      </div>

      <dl class="metrics">
        <div>
          <dt>{{ t('processing.startTime') }}</dt>
          <dd>
            <span>{{ metrics.startDate }}</span>
            <span v-if="metrics.startTime">{{ metrics.startTime }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('processing.elapsedTime') }}</dt>
          <dd>{{ metrics.elapsedTime }}</dd>
        </div>
        <div>
          <dt>{{ t('processing.timeLeft') }}</dt>
          <dd>{{ metrics.timeLeft }}</dd>
        </div>
        <div>
          <dt>{{ t('processing.rate') }}</dt>
          <dd>{{ metrics.processingRate }}</dd>
        </div>
      </dl>

      <div class="processing__actions">
        <el-button
          size="small"
          :icon="Delete"
          @click="emit('clear')"
        >
          {{ t('processing.clear') }}
        </el-button>
        <el-button
          size="small"
          :icon="Download"
          @click="emit('export')"
        >
          {{ t('processing.export') }}
        </el-button>
        <el-button
          v-if="failedPages > 0"
          size="small"
          :icon="RefreshRight"
          type="warning"
          plain
          :disabled="running"
          @click="emit('retry')"
        >
          {{ t('processing.retryFailed', { count: failedPages }) }}
        </el-button>
        <el-button
          size="small"
          :icon="VideoPause"
          type="danger"
          plain
          :disabled="!running || stopped"
          @click="emit('stop')"
        >
          {{ stopped ? t('processing.stopped') : running ? t('processing.stop') : t('processing.noActive') }}
        </el-button>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.processing {
  height: 100%;
  padding: 12px 11px 10px;
  overflow: hidden;
}

.panel-title {
  margin-bottom: 8px;
  font-size: var(--af-font-panel-title);
  font-weight: 500;
  color: var(--af-heading);
}

.processing__progress {
  margin-bottom: 12px;

  :deep(.el-progress-bar__outer) {
    background-color: #ead9c8;
  }
}

.processing__body {
  display: grid;
  grid-template-columns: minmax(180px, 1.35fr) minmax(175px, 1fr) minmax(125px, 0.65fr);
  gap: 12px;
  align-items: center;
  height: calc(100% - 51px);
  min-height: 0;
}

.log-box {
  height: 100%;
  min-height: 0;
  padding: 7px;
  overflow: auto;
  background: #fff;
  border: 1px solid #dedbd6;
  border-radius: 7px;
  box-shadow: inset 0 1px 3px rgb(0 0 0 / 4%);
}

.log-line {
  display: flex;
  gap: 5px;
  margin-bottom: 3px;
  font-size: var(--af-font-caption);
  line-height: 1.4;
  color: #69645e;
  white-space: nowrap;
}

.log-status {
  min-width: 34px;
  padding: 0 3px;
  text-align: center;
  border-radius: 2px;
}

.log-status--info {
  color: #6c736e;
  background: #edf0ee;
}

.log-status--success {
  color: #5c8759;
  background: #e5f3df;
}

.log-status--warning {
  color: #946018;
  background: #fff1cd;
}

.log-status--error {
  color: #a43d36;
  background: #f8e5e2;
}

.log-empty {
  font-size: var(--af-font-caption);
  color: var(--af-muted);
}

.metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 11px 18px;
}

.metrics dt {
  margin-bottom: 2px;
  font-size: var(--af-font-caption);
  color: #97918a;
}

.metrics dd {
  display: grid;
  font-size: var(--af-font-body);
  font-weight: 500;
  line-height: 1.25;
  color: #25221f;
}

.processing__actions {
  display: grid;
  gap: 8px;
}

.processing__actions .el-button {
  width: 100%;
  margin-left: 0;
  font-size: var(--af-font-body);
}

@media (max-width: 760px) {
  .processing__body {
    grid-template-columns: 1fr;
  }

  .processing__actions {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
