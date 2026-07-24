<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  createQualityEvaluation,
  getGoldDatasets,
  getQualityEvaluation,
  getQualityEvaluations,
  importWenjiashanGoldDataset,
} from '@/api/modules/extraction'
import { useI18n } from '@/i18n'
import type { GoldDataset, QualityEvaluationRun } from '@/types/quality-evaluation'

interface Props {
  jobId?: string
  documentId?: string
}

const props = withDefaults(defineProps<Props>(), { jobId: '', documentId: '' })
const { t } = useI18n()
const run = ref<QualityEvaluationRun | null>(null)
const loading = ref(false)
const importing = ref(false)
const goldDataset = ref<GoldDataset | null>(null)
const detailsOpen = ref(false)
let pollTimer: number | undefined

const running = computed(() => ['queued', 'running'].includes(run.value?.status ?? ''))
const summary = computed(() => run.value?.summary)
const sortedFields = computed(() =>
  [...(run.value?.field_metrics ?? [])]
    .filter((metric) => metric.evaluated > 0)
    .sort((left, right) => (left.score ?? 1) - (right.score ?? 1))
    .slice(0, 8),
)

function stopPolling() {
  if (pollTimer !== undefined) {
    globalThis.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function formatScore(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function scoreClass(value: number | null | undefined) {
  if (value == null) return 'quality-score--empty'
  if (value >= 0.95) return 'quality-score--good'
  if (value >= 0.85) return 'quality-score--warning'
  return 'quality-score--danger'
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

async function poll(evaluationId: string) {
  if (!props.jobId) return
  try {
    const latest = await getQualityEvaluation(props.jobId, evaluationId)
    if (run.value && run.value.id !== evaluationId) return
    run.value = latest
    if (['queued', 'running'].includes(latest.status)) {
      pollTimer = globalThis.setTimeout(() => void poll(evaluationId), 700)
    } else if (latest.status === 'failed') {
      ElMessage.error(latest.error || t('quality.failed'))
    } else {
      detailsOpen.value = true
      ElMessage.success(t('quality.completed'))
    }
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('quality.loadFailed'))
  }
}

async function loadLatest() {
  stopPolling()
  run.value = null
  if (!props.jobId) return
  loading.value = true
  try {
    const [runs, datasets] = await Promise.all([
      getQualityEvaluations(props.jobId),
      getGoldDatasets(),
    ])
    run.value = runs[0] ?? null
    goldDataset.value = datasets.find((dataset) => dataset.document_id === props.documentId) ?? null
    if (run.value && ['queued', 'running'].includes(run.value.status)) {
      await poll(run.value.id)
    }
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('quality.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function importGoldDataset() {
  if (!props.documentId || importing.value) return
  importing.value = true
  try {
    goldDataset.value = await importWenjiashanGoldDataset(props.documentId)
    ElMessage.success(t('quality.imported'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('quality.importFailed'))
  } finally {
    importing.value = false
  }
}

async function startEvaluation() {
  if (!props.jobId || running.value || loading.value) return
  if (!goldDataset.value) {
    ElMessage.warning(t('quality.needDataset'))
    return
  }
  stopPolling()
  loading.value = true
  try {
    run.value = await createQualityEvaluation(props.jobId)
    detailsOpen.value = false
    await poll(run.value.id)
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('quality.startFailed'))
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.jobId, props.documentId],
  () => void loadLatest(),
  { immediate: true },
)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="quality-panel panel">
    <header class="quality-panel__header">
      <div>
        <span class="quality-panel__eyebrow">{{ t('quality.eyebrow') }}</span>
        <h2>{{ t('quality.title') }}</h2>
        <p>{{ t('quality.description') }}</p>
      </div>
      <div class="quality-panel__actions">
        <small v-if="run">
          {{ t(`quality.status.${run.status}`) }} · {{ formatTimestamp(run.created_at) }}
        </small>
        <span
          v-if="goldDataset"
          class="quality-dataset-badge"
        >
          {{ t('quality.datasetReady', { count: goldDataset.record_count }) }}
        </span>
        <button
          v-else
          type="button"
          class="quality-import-button"
          :disabled="!documentId || importing"
          @click="importGoldDataset"
        >
          {{ importing ? t('quality.importing') : t('quality.importDataset') }}
        </button>
        <button
          type="button"
          :disabled="!jobId || !goldDataset || running || loading"
          @click="startEvaluation"
        >
          {{ running ? t('quality.running') : t('quality.run') }}
        </button>
      </div>
    </header>

    <div
      v-if="running"
      class="quality-progress"
    >
      <div><i :style="{ width: `${run?.progress.percent ?? 0}%` }" /></div>
      <span>{{ run?.progress.percent ?? 0 }}%</span>
    </div>

    <div
      v-if="summary"
      class="quality-score-grid"
    >
      <article>
        <span>{{ t('quality.identifierPrecision') }}</span>
        <strong :class="scoreClass(summary.artifact_id_precision)">
          {{ formatScore(summary.artifact_id_precision) }}
        </strong>
        <small>{{ summary.matched_records }}/{{ summary.predicted_records }}</small>
      </article>
      <article>
        <span>{{ t('quality.fieldScore') }}</span>
        <strong :class="scoreClass(summary.field_macro_score)">
          {{ formatScore(summary.field_macro_score) }}
        </strong>
      </article>
      <article>
        <span>{{ t('quality.ocrScore') }}</span>
        <strong :class="scoreClass(summary.ocr_anchor_score)">
          {{ formatScore(summary.ocr_anchor_score) }}
        </strong>
      </article>
      <article>
        <span>{{ t('quality.detectionScore') }}</span>
        <strong :class="scoreClass(summary.detection_macro_f1)">
          {{ formatScore(summary.detection_macro_f1) }}
        </strong>
      </article>
      <article>
        <span>{{ t('quality.relationScore') }}</span>
        <strong :class="scoreClass(summary.relation_score)">
          {{ formatScore(summary.relation_score) }}
        </strong>
      </article>
      <button
        type="button"
        class="quality-details-toggle"
        @click="detailsOpen = !detailsOpen"
      >
        {{ detailsOpen ? t('quality.hideDetails') : t('quality.showDetails') }}
      </button>
    </div>

    <div
      v-if="summary && detailsOpen"
      class="quality-details"
    >
      <section>
        <h3>{{ t('quality.lowScoreFields') }}</h3>
        <div
          v-for="metric in sortedFields"
          :key="metric.key"
          class="quality-metric-row"
        >
          <span>{{ t(`quality.field.${metric.key}`) }}</span>
          <div><i :style="{ width: formatScore(metric.score) }" /></div>
          <b>{{ formatScore(metric.score) }}</b>
          <small>{{ t('quality.metricCounts', {
            matched: metric.matched,
            missing: metric.missing,
            mismatched: metric.mismatched,
          }) }}</small>
        </div>
      </section>
      <section>
        <h3>{{ t('quality.detectionDetails') }}</h3>
        <div
          v-for="metric in run?.detection_metrics"
          :key="metric.kind"
          class="quality-compact-row"
        >
          <span>{{ t(`quality.region.${metric.kind}`) }}</span>
          <strong>{{ formatScore(metric.f1) }}</strong>
          <small>P {{ formatScore(metric.precision) }} · R {{ formatScore(metric.recall) }}</small>
        </div>
      </section>
      <section>
        <h3>{{ t('quality.scope') }}</h3>
        <p>
          {{ summary.full_document_scope
            ? t('quality.fullScope')
            : t('quality.partialScope', { count: summary.evaluated_pages.length }) }}
        </p>
        <p
          v-for="warning in run?.warnings"
          :key="warning"
          class="quality-warning"
        >
          {{ warning }}
        </p>
      </section>
    </div>

    <p
      v-else-if="!loading && !run"
      class="quality-empty"
    >
      {{ t('quality.empty') }}
    </p>
  </section>
</template>

<style scoped lang="scss">
.quality-panel {
  grid-column: 1 / -1;
  min-width: 0;
  padding: 12px 14px;
}

.quality-panel__header {
  display: flex;
  gap: 18px;
  align-items: center;
  justify-content: space-between;
}

.quality-panel__eyebrow {
  font-size: 9px;
  color: #ae6b3b;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.quality-panel__header h2 {
  margin-top: 1px;
  font-size: 15px;
  color: var(--af-heading);
}

.quality-panel__header p,
.quality-panel__actions small,
.quality-empty {
  font-size: 10px;
  color: var(--af-muted);
}

.quality-panel__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
  align-items: center;
}

.quality-panel__actions button,
.quality-details-toggle {
  height: 31px;
  padding: 0 13px;
  color: #fff;
  cursor: pointer;
  background: #af5c22;
  border: 1px solid #af5c22;
  border-radius: 6px;
}

.quality-panel__actions .quality-import-button {
  color: #80502d;
  background: #fff;
  border-color: #dfc7b3;
}

.quality-dataset-badge {
  padding: 4px 8px;
  font-size: 9px;
  color: #397c4d;
  background: #e9f6ec;
  border-radius: 12px;
}

.quality-panel__actions button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.quality-progress {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
  margin-top: 9px;
  font-size: 10px;
  color: #80502d;
}

.quality-progress > div {
  height: 5px;
  overflow: hidden;
  background: #eee5dc;
  border-radius: 5px;
}

.quality-progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #b65c21, #e29b5d);
  transition: width 180ms ease;
}

.quality-score-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(105px, 1fr)) auto;
  gap: 8px;
  margin-top: 11px;
}

.quality-score-grid article {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 7px;
  align-items: center;
  min-width: 0;
  padding: 8px 10px;
  background: #faf7f3;
  border: 1px solid #eee3d8;
  border-radius: 7px;
}

.quality-score-grid article span,
.quality-score-grid article small {
  overflow: hidden;
  font-size: 9px;
  color: #85796e;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quality-score-grid article small {
  grid-column: 1 / -1;
}

.quality-score-grid strong {
  font-size: 16px;
}

.quality-score--good { color: #3f9e58; }
.quality-score--warning { color: #c58422; }
.quality-score--danger { color: #d4534b; }
.quality-score--empty { color: #aaa19a; }

.quality-details-toggle {
  height: auto;
  color: #80502d;
  background: #fff;
  border-color: #dfc7b3;
}

.quality-details {
  display: grid;
  grid-template-columns: minmax(420px, 1.5fr) minmax(240px, 0.8fr) minmax(260px, 1fr);
  gap: 14px;
  margin-top: 12px;
  padding-top: 11px;
  border-top: 1px solid #eee3d8;
}

.quality-details h3 {
  margin-bottom: 7px;
  font-size: 11px;
  color: #5f554d;
}

.quality-metric-row {
  display: grid;
  grid-template-columns: 72px minmax(80px, 1fr) 38px minmax(140px, auto);
  gap: 6px;
  align-items: center;
  margin-top: 5px;
  font-size: 9px;
  color: #756b62;
}

.quality-metric-row > div {
  height: 5px;
  overflow: hidden;
  background: #eee6df;
  border-radius: 5px;
}

.quality-metric-row i {
  display: block;
  height: 100%;
  background: #d48a4f;
}

.quality-metric-row b {
  font-size: 9px;
  text-align: right;
}

.quality-metric-row small,
.quality-compact-row small,
.quality-details p {
  font-size: 9px;
  color: #948a81;
}

.quality-compact-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 8px;
  padding: 4px 0;
  font-size: 10px;
  border-bottom: 1px dashed #eee3d8;
}

.quality-compact-row small {
  grid-column: 1 / -1;
}

.quality-warning {
  margin-top: 6px;
  padding: 5px 7px;
  line-height: 1.35;
  color: #9a623b !important;
  background: #fff4ea;
  border-radius: 5px;
}

.quality-empty {
  padding: 10px 0 2px;
}

@media (max-width: 1180px) {
  .quality-score-grid {
    grid-template-columns: repeat(3, minmax(110px, 1fr));
  }

  .quality-details {
    grid-template-columns: 1fr 1fr;
  }

  .quality-details > :last-child {
    grid-column: 1 / -1;
  }
}
</style>
