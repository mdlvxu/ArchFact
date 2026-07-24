<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  applyRematch,
  cancelRematch,
  createRematch,
  createVerificationSession,
  getRematch,
  getRematchChanges,
  getVerificationVersions,
} from '@/api/modules/extraction'
import AssertionRules from '@/components/business/AssertionRules.vue'
import RematchReportDialog from '@/components/business/RematchReportDialog.vue'
import QualityEvaluationPanel from '@/components/business/QualityEvaluationPanel.vue'
import VerificationSummary from '@/components/business/VerificationSummary.vue'
import VersionHistory from '@/components/business/VersionHistory.vue'
import { useI18n } from '@/i18n'
import type { RematchRelationChange, RematchRun } from '@/types/extraction'
import type {
  VerificationReport,
  VerificationRule,
  VerificationSession,
  VerificationVersion,
  VerificationVersionSnapshot,
} from '@/types/verification'

interface Props {
  jobId?: string
  documentId?: string
  activeMatchingVersionId?: string
}

const props = withDefaults(defineProps<Props>(), {
  jobId: '',
  documentId: '',
  activeMatchingVersionId: 'M0',
})
const emit = defineEmits<{
  startVerification: [session: VerificationSession]
  matchingVersionApplied: [matchingVersionId: string]
}>()
const { t } = useI18n()

const rules = ref<VerificationRule[]>([
  {
    id: 1,
    title: 'ID Uniqueness',
    description: 'The unique ID of each artifact must not be duplicated.',
    enabled: true,
  },
  {
    id: 2,
    title: 'Color Null Value Logic',
    description: '“None” is no longer flagged as an error when the source has no color record.',
    enabled: true,
    updated: true,
  },
  {
    id: 3,
    title: 'Figure Caption Check',
    description: 'The figure caption number must match the figure order on the corresponding PDF page.',
    enabled: true,
  },
  {
    id: 4,
    title: 'Size Precision',
    description: 'Dimensions may include length, width, height or any measurement with an accepted unit.',
    enabled: true,
  },
  {
    id: 5,
    title: 'Material / Vessel Type Alignment',
    description: 'Synonymous terms are allowed, but missing material or vessel attributes are prohibited.',
    enabled: true,
  },
])

const running = ref(false)
const loadingVersions = ref(false)
const versionSnapshots = ref<VerificationVersionSnapshot[]>([])
const selectedVersionNumber = ref<number | null>(null)
const rematchRun = ref<RematchRun | null>(null)
const rematchCreating = ref(false)
const rematchApplying = ref(false)
const rematchReportOpen = ref(false)
const rematchChangesLoading = ref(false)
const rematchChanges = ref<RematchRelationChange[]>([])
let loadedRulesForJob = ''
let rematchPollTimer: number | undefined

const rematchBusy = computed(() =>
  ['queued', 'running', 'applying', 'cancelling'].includes(rematchRun.value?.status ?? ''),
)

function stopRematchPolling() {
  if (rematchPollTimer !== undefined) {
    globalThis.clearTimeout(rematchPollTimer)
    rematchPollTimer = undefined
  }
}

async function pollRematch(rematchId: string) {
  if (!props.jobId) return
  try {
    const run = await getRematch(props.jobId, rematchId)
    if (rematchRun.value && rematchRun.value.id !== rematchId) return
    rematchRun.value = run
    if (['queued', 'running', 'applying', 'cancelling'].includes(run.status)) {
      rematchPollTimer = globalThis.setTimeout(() => void pollRematch(rematchId), 600)
    } else if (run.status === 'failed') {
      ElMessage.error(run.error || t('matching.failed'))
    } else if (run.status === 'completed') {
      ElMessage.success(t('matching.previewReady'))
    }
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('matching.failed'))
  }
}

async function previewMatching() {
  if (!props.jobId || rematchCreating.value || rematchBusy.value) return
  rematchCreating.value = true
  stopRematchPolling()
  try {
    const created = await createRematch(props.jobId)
    rematchRun.value = await getRematch(props.jobId, created.rematch_id)
    await pollRematch(created.rematch_id)
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('matching.failed'))
  } finally {
    rematchCreating.value = false
  }
}

async function stopMatching() {
  if (!props.jobId || !rematchRun.value || !rematchBusy.value) return
  stopRematchPolling()
  try {
    rematchRun.value = await cancelRematch(props.jobId, rematchRun.value.id)
    ElMessage.info(t('matching.cancelled'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('matching.cancelFailed'))
  }
}

async function applyMatchingPreview() {
  if (!props.jobId || !rematchRun.value || rematchRun.value.status !== 'completed') return
  rematchApplying.value = true
  try {
    rematchRun.value = await applyRematch(props.jobId, rematchRun.value.id)
    rematchReportOpen.value = false
    emit('matchingVersionApplied', rematchRun.value.id)
    ElMessage.success(t('matching.applied'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('matching.applyFailed'))
  } finally {
    rematchApplying.value = false
  }
}

async function openRematchReport() {
  if (!props.jobId || !rematchRun.value?.report) return
  rematchReportOpen.value = true
  rematchChangesLoading.value = true
  try {
    const result = await getRematchChanges(props.jobId, rematchRun.value.id)
    rematchChanges.value = result.items
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('matching.changesFailed'))
  } finally {
    rematchChangesLoading.value = false
  }
}

function discardMatchingPreview() {
  stopRematchPolling()
  rematchReportOpen.value = false
  rematchChanges.value = []
  rematchRun.value = null
  ElMessage.info(t('matching.discarded'))
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat('sv-SE', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function reportFromVersion(version?: VerificationVersionSnapshot): VerificationReport {
  if (!version) {
    return {
      sampleCount: 0,
      errorCoverage: 0,
      precision: 0,
      alignment: 0,
      totalArtifacts: 0,
      passed: 0,
      errors: 0,
      stale: 0,
      relationChanged: 0,
      fields: [],
    }
  }
  const failureCounts = new Map<string, number>()
  version.items
    .filter((item) => item.verdict === 'failed')
    .forEach((item) => {
      const failureLabels: Record<string, string> = {
        field_error: t('catalog.failure.field'),
        text_evidence_error: t('catalog.failure.textEvidence'),
        caption_match_error: t('catalog.failure.captionMatch'),
        number_match_error: t('catalog.failure.numberMatch'),
        artifact_crop_error: t('catalog.failure.artifactCrop'),
        color_plate_error: t('catalog.failure.colorPlate'),
        other: t('catalog.failure.other'),
      }
      const label = failureLabels[item.failure_code ?? '']
        || item.failure_reason
        || t('verification.unspecifiedFailure')
      failureCounts.set(label, (failureCounts.get(label) ?? 0) + 1)
    })
  const passRate = Math.round(version.report.pass_rate * 100)
  return {
    sampleCount: version.report.sample_count,
    errorCoverage: 100 - passRate,
    precision: passRate,
    alignment: passRate,
    totalArtifacts: version.report.total_artifacts,
    passed: version.report.pass_count,
    errors: version.report.fail_count,
    stale: version.report.stale_count ?? 0,
    relationChanged: version.report.relation_changed_count ?? 0,
    fields: [...failureCounts.entries()].map(([label, count]) => ({ label, count })),
  }
}

const sortedSnapshots = computed(() =>
  [...versionSnapshots.value].sort((left, right) => right.version - left.version),
)
const selectedSnapshot = computed(() =>
  sortedSnapshots.value.find((version) => version.version === selectedVersionNumber.value)
    ?? sortedSnapshots.value[0],
)
const report = computed(() => reportFromVersion(selectedSnapshot.value))

const versions = computed<VerificationVersion[]>(() =>
  sortedSnapshots.value.map((version, index) => {
    const previous = sortedSnapshots.value[index + 1]
    const beforeRate = previous ? Math.round(previous.report.pass_rate * 100) : 0
    const afterRate = Math.round(version.report.pass_rate * 100)
    const enabledCount = version.rules.filter((rule) => rule.enabled).length
    return {
      version: version.version,
      createdAt: formatTimestamp(version.created_at),
      title: version.version === 1 ? 'Initial version' : 'Assertion execution',
      summary: `${enabledCount} active assertions · ${version.report.sample_count} fixed samples · ${version.matching_version_id ?? 'M0'}`,
      matchingVersionId: version.matching_version_id ?? 'M0',
      staleCount: version.report.stale_count ?? 0,
      relationChangedCount: version.report.relation_changed_count ?? 0,
      exportable: (version.report.stale_count ?? 0) === 0
        && version.report.pass_count + version.report.fail_count === version.report.sample_count,
      before: previous
        ? `Alignment ${beforeRate}%, ${previous.report.fail_count} records require review.`
        : 'No previous verification baseline.',
      after: `Alignment ${afterRate}%, ${version.report.fail_count} records require review.`,
      impact: {
        alignmentBefore: beforeRate,
        alignmentAfter: afterRate,
        errorsBefore: previous?.report.fail_count ?? 0,
        errorsAfter: version.report.fail_count,
        passedBefore: previous?.report.pass_count ?? 0,
        passedAfter: version.report.pass_count,
      },
    }
  }),
)

async function loadVersions() {
  if (!props.jobId) {
    versionSnapshots.value = []
    return
  }
  loadingVersions.value = true
  try {
    versionSnapshots.value = await getVerificationVersions(props.jobId)
    const latest = sortedSnapshots.value[0]
    if (!sortedSnapshots.value.some((version) => version.version === selectedVersionNumber.value)) {
      selectedVersionNumber.value = latest?.version ?? null
    }
    if (latest && loadedRulesForJob !== props.jobId) {
      rules.value = latest.rules.map((rule) => ({ ...rule, updated: false }))
      loadedRulesForJob = props.jobId
    }
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.loadFailed'))
  } finally {
    loadingVersions.value = false
  }
}

/** 创建真实校验会话；第一版固定随机18条，后续版本复用同一组样本。 */
async function executeVerification() {
  if (running.value) return
  if (!props.jobId) {
    ElMessage.warning(t('verification.needJob'))
    return
  }
  const enabledRules = rules.value.filter((rule) => rule.enabled)
  if (!enabledRules.length) {
    ElMessage.warning(t('verification.needRule'))
    return
  }

  running.value = true
  try {
    const session = await createVerificationSession(props.jobId, rules.value, 18)
    emit('startVerification', session)
    ElMessage.success(t('verification.sessionCreated', {
      version: session.target_version,
      count: session.sample_count,
    }))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  } finally {
    running.value = false
  }
}

/** 导出当前规则、报告和版本历史，供顶部 Output 按钮调用。 */
function exportResult() {
  const version = selectedSnapshot.value
  if (!version) {
    ElMessage.warning(t('verification.noVersionToExport'))
    return
  }
  const staleCount = version.report.stale_count ?? 0
  const completedCount = version.report.pass_count + version.report.fail_count
  if (staleCount > 0 || completedCount !== version.report.sample_count) {
    ElMessage.warning(t('verification.exportBlocked', {
      stale: staleCount,
      remaining: Math.max(0, version.report.sample_count - completedCount - staleCount),
    }))
    return
  }
  const content = JSON.stringify(
    {
      jobId: props.jobId,
      exportedAt: new Date().toISOString(),
      verificationVersion: `V${version.version}`,
      verificationVersionId: version.id,
      matchingVersionId: version.matching_version_id ?? 'M0',
      rules: version.rules,
      report: version.report,
      items: version.items,
    },
    null,
    2,
  )
  const blob = new globalThis.Blob([content], { type: 'application/json;charset=utf-8' })
  const url = globalThis.URL.createObjectURL(blob)
  const link = globalThis.document.createElement('a')
  link.href = url
  const matchingVersion = (version.matching_version_id ?? 'M0').replace(/[^a-zA-Z0-9_-]/g, '-')
  link.download = `archfact-V${version.version}-${matchingVersion}.json`
  link.click()
  globalThis.URL.revokeObjectURL(url)
  ElMessage.success(t('verification.exported'))
}

defineExpose({ exportResult, refreshVersions: loadVersions })

watch(
  () => props.jobId,
  () => {
    stopRematchPolling()
    rematchRun.value = null
    rematchReportOpen.value = false
    rematchChanges.value = []
    selectedVersionNumber.value = null
    loadedRulesForJob = ''
    void loadVersions()
  },
  { immediate: true },
)

onBeforeUnmount(stopRematchPolling)
</script>

<template>
  <div class="machine-verification-workspace">
    <section class="matching-panel panel">
      <div class="matching-panel__identity">
        <span>{{ t('matching.title') }}</span>
        <strong>{{ activeMatchingVersionId || 'M0' }}</strong>
        <small>{{ t('matching.currentHint') }}</small>
      </div>
      <div
        v-if="rematchRun"
        class="matching-panel__progress"
      >
        <span>{{ t(`matching.status.${rematchRun.status}`) }}</span>
        <div><i :style="{ width: `${rematchRun.progress.percent}%` }" /></div>
        <small v-if="rematchRun.report">
          {{ t('matching.report', {
            linked: rematchRun.report.linked_records,
            total: rematchRun.report.total_records,
            added: rematchRun.report.delta.added,
            removed: rematchRun.report.delta.removed,
          }) }}
        </small>
      </div>
      <div class="matching-panel__actions">
        <button
          type="button"
          :disabled="rematchCreating || rematchBusy"
          @click="previewMatching"
        >
          {{ rematchCreating || rematchBusy ? t('matching.running') : t('matching.preview') }}
        </button>
        <button
          v-if="rematchBusy"
          type="button"
          class="matching-button--danger"
          @click="stopMatching"
        >
          {{ t('matching.cancel') }}
        </button>
        <button
          v-if="rematchRun?.status === 'completed'"
          type="button"
          @click="openRematchReport"
        >
          {{ t('matching.viewChanges') }}
        </button>
        <button
          v-if="rematchRun?.status === 'completed'"
          type="button"
          class="matching-button--apply"
          :disabled="rematchApplying"
          @click="applyMatchingPreview"
        >
          {{ rematchApplying ? t('matching.applying') : t('matching.apply') }}
        </button>
      </div>
    </section>

    <RematchReportDialog
      v-if="rematchReportOpen && rematchRun"
      :run="rematchRun"
      :changes="rematchChanges"
      :loading="rematchChangesLoading"
      :applying="rematchApplying"
      @close="rematchReportOpen = false"
      @discard="discardMatchingPreview"
      @apply="applyMatchingPreview"
    />

    <QualityEvaluationPanel
      :job-id="jobId"
      :document-id="documentId"
    />

    <AssertionRules
      v-model:rules="rules"
      :running="running"
      @execute="executeVerification"
    />

    <section class="verification-result panel">
      <h2>{{ t('verification.result') }}</h2>
      <div class="verification-result__content">
        <VerificationSummary
          :report="report"
          :running="running || loadingVersions"
        />
        <VersionHistory
          :versions="versions"
          :selected-version="selectedVersionNumber"
          @select-version="selectedVersionNumber = $event"
        />
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.machine-verification-workspace {
  display: grid;
  grid-template-columns: minmax(330px, 0.9fr) minmax(650px, 1.8fr);
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 16px;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.matching-panel {
  display: grid;
  grid-template-columns: minmax(220px, 0.7fr) minmax(320px, 1.7fr) auto;
  grid-column: 1 / -1;
  gap: 14px;
  align-items: center;
  min-height: 72px;
  padding: 10px 14px;
}

.matching-panel__identity {
  display: grid;
  grid-template-columns: auto auto;
  gap: 2px 8px;
  align-items: center;
  justify-content: start;
}

.matching-panel__identity span {
  font-size: 13px;
  font-weight: 600;
}

.matching-panel__identity strong {
  padding: 3px 8px;
  font-size: 11px;
  color: #98501e;
  background: #fff0e3;
  border-radius: 12px;
}

.matching-panel__identity small {
  grid-column: 1 / -1;
  color: var(--af-muted);
}

.matching-panel__progress {
  display: grid;
  gap: 4px;
  min-width: 0;
  font-size: 10px;
  color: #6c6259;
}

.matching-panel__progress > div {
  height: 6px;
  overflow: hidden;
  background: #eee6df;
  border-radius: 5px;
}

.matching-panel__progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #c06424, #e8944e);
  border-radius: inherit;
  transition: width 180ms ease;
}

.matching-panel__progress small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.matching-panel__actions {
  display: flex;
  gap: 7px;
}

.matching-panel__actions button {
  height: 31px;
  padding: 0 11px;
  font-size: 10px;
  color: #80502d;
  cursor: pointer;
  background: #fff;
  border: 1px solid #dfc7b3;
  border-radius: 6px;
}

.matching-panel__actions button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.matching-panel__actions .matching-button--apply {
  color: #fff;
  background: #af5c22;
  border-color: #af5c22;
}

.matching-panel__actions .matching-button--danger {
  color: #b64a42;
  border-color: #e4b5b0;
}

.verification-result {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 12px 14px 14px;
  overflow: hidden;
}

.verification-result > h2 {
  flex: 0 0 auto;
  padding: 0 2px 12px;
  font-size: var(--af-font-page-title);
  font-weight: 600;
  color: var(--af-heading);
}

.verification-result__content {
  display: grid;
  grid-template-columns: minmax(360px, 1.35fr) minmax(310px, 1fr);
  gap: 12px;
  flex: 1;
  min-height: 0;
}

@media (max-width: 1120px) {
  .machine-verification-workspace {
    grid-template-columns: minmax(300px, 0.85fr) minmax(560px, 1.6fr);
  }

  .verification-result__content {
    grid-template-columns: minmax(320px, 1.25fr) minmax(250px, 0.9fr);
  }
}

@media (max-width: 900px) {
  .machine-verification-workspace {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto 620px 780px;
    height: auto;
  }

  .matching-panel {
    grid-column: 1;
  }
}

@media (max-width: 650px) {
  .verification-result__content {
    grid-template-columns: 1fr;
    grid-template-rows: 620px 620px;
  }

  .machine-verification-workspace {
    grid-template-rows: auto auto 620px 1280px;
  }
}
</style>
