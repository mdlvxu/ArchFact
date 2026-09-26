<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  applyRematch,
  cancelRematch,
  createVerificationExperiment,
  createRematch,
  getActiveMachineVerificationRun,
  getActiveVerificationSession,
  getActiveVerificationExperiment,
  getVerificationExperiments,
  getMachineVerificationRun,
  getRematch,
  getRematchChanges,
  getVerificationSession,
  getVerificationVersions,
  downloadFullMachineVerificationExcel,
  pauseMachineVerificationRun,
  resetInvalidVerificationV1,
  resumeMachineVerificationRun,
  terminateMachineVerificationRun,
  startMachineVerificationRun,
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
  MachineVerificationRun,
  VerificationExperiment,
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
const { locale, t } = useI18n()

/** 第三页暂不展示关系匹配与质量基线，后续需要时改回 true 即可。 */
const showMatchingPanel = false
const showQualityEvaluationPanel = false

function createDefaultRules(): VerificationRule[] {
  return [
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
      enabled: false,
    },
    {
      id: 3,
      title: 'Figure Caption Check',
      description: 'The figure caption number must match the figure order on the corresponding PDF page.',
      enabled: false,
    },
    {
      id: 4,
      title: 'Size Precision',
      description: 'Dimensions may include length, width, height or any measurement with an accepted unit.',
      enabled: false,
    },
    {
      id: 5,
      title: 'Material / Vessel Type Alignment',
      description: 'Synonymous terms are allowed, but missing material or vessel attributes are prohibited.',
      enabled: false,
    },
  ]
}

const rules = ref<VerificationRule[]>(createDefaultRules())

const running = ref(false)
const terminatingMachineVerification = ref(false)
const loadingVersions = ref(false)
const versionSnapshots = ref<VerificationVersionSnapshot[]>([])
const selectedVersionNumber = ref<number | null>(null)
const rematchRun = ref<RematchRun | null>(null)
const rematchCreating = ref(false)
const rematchApplying = ref(false)
const rematchReportOpen = ref(false)
const rematchChangesLoading = ref(false)
const rematchChanges = ref<RematchRelationChange[]>([])
const machineRun = ref<MachineVerificationRun | null>(null)
const verificationExperiment = ref<VerificationExperiment | null>(null)
const verificationExperiments = ref<VerificationExperiment[]>([])
const viewingExperimentId = ref('')
const newExperimentConfirmOpen = ref(false)
const experimentCreating = ref(false)
const invalidV1Resetting = ref(false)
const exportingMachineDetails = ref(false)
const configuredRulesForTargetVersion = ref<number | null>(null)
const openingHumanReview = ref(false)
const openedHumanReviewSessionId = ref('')
let rematchPollTimer: number | undefined
let machineRunPollTimer: number | undefined
let loadVersionsRequestId = 0

const rematchBusy = computed(() =>
  ['queued', 'running', 'applying', 'cancelling'].includes(rematchRun.value?.status ?? ''),
)
const machineProgressText = computed(() => {
  const run = machineRun.value
  if (!run || !running.value) return ''
  const phase = run.mode === 'initial'
    ? t('summary.initialProgress')
    : t('summary.recheckProgress')
  const { current, total, percent } = run.progress
  return total > 0
    ? t('summary.progress', { phase, current, total, percent })
    : t('summary.preparing', { phase })
})
const machinePaused = computed(() => machineRun.value?.status === 'paused')
function displayExperimentName(experiment: VerificationExperiment) {
  if (locale.value !== 'en-US') return experiment.name
  if (experiment.name === '历史校验') return t('verification.experiment.legacyName')
  const match = experiment.name.match(/^实验\s*E?(\d+)$/i)
  return match ? `Experiment E${match[1]}` : experiment.name
}

function formatExperimentSummary(experiment: VerificationExperiment) {
  const activeRunCount = machineRun.value?.experiment_id === experiment.id
    ? machineRun.value.total_artifacts
    : 0
  const artifactCount = activeRunCount > 0 ? activeRunCount : experiment.artifact_count
  return `${displayExperimentName(experiment)} · ${t('summary.artifacts', { count: artifactCount })} · ${experiment.matching_version_id}`
}

const experimentSummary = computed(() => {
  const experiment = verificationExperiment.value
  if (!experiment) return ''
  return formatExperimentSummary(experiment)
})
const viewingExperiment = computed(() =>
  verificationExperiments.value.find((item) => item.id === viewingExperimentId.value)
  ?? verificationExperiment.value,
)
const viewingArchivedExperiment = computed(() => Boolean(
  viewingExperiment.value
  && verificationExperiment.value
  && viewingExperiment.value.id !== verificationExperiment.value.id,
))
const viewingExperimentSummary = computed(() => {
  const experiment = viewingExperiment.value
  if (!experiment) return ''
  return formatExperimentSummary(experiment)
})
const activeExperimentHasCompletedV1 = computed(() =>
  verificationExperiment.value?.status === 'legacy' ||
  versionSnapshots.value.some((version) =>
    version.version === 1 && !(version.report.model_unavailable_count && version.report.model_unavailable_count > 0),
  ),
)
const invalidV1Snapshot = computed(() =>
  !viewingArchivedExperiment.value
    ? versionSnapshots.value.find((version) =>
      version.version === 1
        && (version.report.model_unavailable_count ?? 0) > 0,
    )
    : undefined,
)
const awaitingInitialHumanReview = computed(() =>
  !viewingArchivedExperiment.value &&
  machineRun.value?.mode === 'initial' &&
  machineRun.value.status === 'completed' &&
  Boolean(machineRun.value.session_id) &&
  !activeExperimentHasCompletedV1.value,
)
const newExperimentBlockedReason = computed(() => {
  if (viewingArchivedExperiment.value) return t('verification.experiment.archivedHint')
  if (running.value) return t('verification.experiment.runningBlocked')
  if (!activeExperimentHasCompletedV1.value) {
    return t('verification.experiment.v1Required')
  }
  return ''
})
const canCreateExperiment = computed(() =>
  Boolean(props.jobId) &&
  !running.value &&
  !experimentCreating.value &&
  !viewingArchivedExperiment.value &&
  activeExperimentHasCompletedV1.value,
)

/** 校验版本与断言基准一一对应；V3 要等其基准配置后才会开放。 */
const targetVersionNumber = computed(() =>
  Math.max(0, ...versionSnapshots.value.map((item) => item.version)) + 1,
)
const targetBaselineId = computed<'v1' | 'v2' | null>(() => {
  if (targetVersionNumber.value === 1) return 'v1'
  if (targetVersionNumber.value === 2) return 'v2'
  return null
})

watch(targetVersionNumber, (targetVersion) => {
  if (configuredRulesForTargetVersion.value === targetVersion) return
  rules.value = createDefaultRules()
  configuredRulesForTargetVersion.value = targetVersion
}, { immediate: true })

function stopRematchPolling() {
  if (rematchPollTimer !== undefined) {
    globalThis.clearTimeout(rematchPollTimer)
    rematchPollTimer = undefined
  }
}

function stopMachineRunPolling() {
  if (machineRunPollTimer !== undefined) {
    globalThis.clearTimeout(machineRunPollTimer)
    machineRunPollTimer = undefined
  }
}

async function finishMachineRun(run: MachineVerificationRun) {
  if (!props.jobId) return
  if (run.mode === 'initial') {
    await openHumanReview(run)
    return
  }
  await loadVersions()
  const total = run.pass_count + run.fail_count + run.uncertain_count
  ElMessage.success(t('verification.recheckCompleted', { count: total }))
}

/**
 * 首次全量校验结束后，人工核验会话才是进入第二页的唯一依据。
 * 不只依赖轮询响应中的 session_id，避免完成瞬间、刷新或短暂断网导致
 * 已生成的 18 条样本留在后台而前端没有跳转。
 */
async function openHumanReview(run: MachineVerificationRun | null = machineRun.value) {
  if (!props.jobId || openingHumanReview.value) return
  openingHumanReview.value = true
  try {
    const session = run?.session_id
      ? await getVerificationSession(props.jobId, run.session_id)
      : await getActiveVerificationSession(props.jobId, { suppressErrorMessage: true })

    if (session.status !== 'in_progress') {
      throw new Error(t('verification.humanReviewUnavailable'))
    }
    openedHumanReviewSessionId.value = session.id
    emit('startVerification', session)
    ElMessage.success(t('verification.humanReviewOpened', { count: session.sample_count }))
  } catch (error: unknown) {
    // 首次完成后仍保留可恢复状态；不把它误显示成“后续复核完成”。
    await loadVersions()
    ElMessage.error(
      error instanceof Error
        ? `${t('verification.humanReviewOpenFailed')} ${error.message}`
        : t('verification.humanReviewOpenFailed'),
    )
  } finally {
    openingHumanReview.value = false
  }
}

async function pollMachineRun(runId: string) {
  if (!props.jobId) return
  try {
    const run = await getMachineVerificationRun(props.jobId, runId)
    if (machineRun.value && machineRun.value.id !== runId) return
    machineRun.value = run
    if (['queued', 'running'].includes(run.status)) {
      machineRunPollTimer = globalThis.setTimeout(() => void pollMachineRun(runId), 800)
      return
    }
    running.value = false
    if (run.status === 'paused') {
      ElMessage.info(t('verification.pausedCanResume'))
      return
    }
    if (run.status === 'failed') {
      const sampleCreationFailed = run.mode === 'initial'
        && run.progress.percent === 100
        && !run.session_id
      ElMessage.error(
        sampleCreationFailed
          ? t('verification.sampleCreationFailed', {
              count: 18,
              reason: run.error || t('verification.startFailed'),
            })
          : (run.error || t('verification.startFailed')),
      )
      return
    }
    if (run.status === 'terminated') {
      running.value = false
      ElMessage.info(t('verification.terminatedCanRestart'))
      return
    }
    await finishMachineRun(run)
  } catch (error: unknown) {
    running.value = false
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  }
}

async function pauseMachineVerification() {
  if (!props.jobId || !machineRun.value || !running.value) return
  stopMachineRunPolling()
  try {
    machineRun.value = await pauseMachineVerificationRun(props.jobId, machineRun.value.id)
    running.value = false
    ElMessage.info(t('verification.pausedSaved'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
    if (machineRun.value) void pollMachineRun(machineRun.value.id)
  }
}

async function resumeMachineVerification() {
  if (!props.jobId || !machineRun.value || !machinePaused.value) return
  try {
    machineRun.value = await resumeMachineVerificationRun(props.jobId, machineRun.value.id)
    running.value = true
    void pollMachineRun(machineRun.value.id)
    ElMessage.success(t('verification.resumed'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  }
}

async function terminateMachineVerification() {
  if (!props.jobId || !machineRun.value || terminatingMachineVerification.value) return
  terminatingMachineVerification.value = true
  stopMachineRunPolling()
  try {
    machineRun.value = await terminateMachineVerificationRun(props.jobId, machineRun.value.id)
    running.value = false
    ElMessage.success(t('verification.terminatedCanRestart'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
    if (machineRun.value?.status === 'running') void pollMachineRun(machineRun.value.id)
  } finally {
    terminatingMachineVerification.value = false
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
      uncertain: 0,
      stale: 0,
      relationChanged: 0,
      reviewRequired: 0,
      reviewLoad: null,
      fields: [],
    }
  }
  const failureLabels: Record<string, string> = {
    artifact_id_missing: t('catalog.failure.artifactIdMissing'),
    artifact_id_duplicate: t('catalog.failure.artifactIdDuplicate'),
    artifact_id_evidence_conflict: t('catalog.failure.artifactIdConflict'),
    sequence_crop_conflict: t('catalog.failure.sequenceCropConflict'),
    figure_caption_missing: t('catalog.failure.captionMissing'),
    figure_caption_evidence_conflict: t('catalog.failure.captionConflict'),
    color_plate_relation_conflict: t('catalog.failure.colorPlateConflict'),
    artifact_crop_missing: t('catalog.failure.artifactCropMissing'),
    structured_measurements_error: t('catalog.failure.measurements'),
    structured_classification_error: t('catalog.failure.classification'),
    structured_field_evidence_conflict: t('catalog.failure.structuredConflict'),
    text_evidence_conflict: t('catalog.failure.textEvidence'),
    unclassified_failure: t('catalog.failure.unclassified'),
    field_error: t('catalog.failure.field'),
    text_evidence_error: t('catalog.failure.textEvidence'),
    caption_match_error: t('catalog.failure.captionMatch'),
    number_match_error: t('catalog.failure.numberMatch'),
    artifact_crop_error: t('catalog.failure.artifactCrop'),
    color_plate_error: t('catalog.failure.colorPlate'),
    other: t('catalog.failure.other'),
  }
  const failureCounts = new Map<string, number>()
  if (version.report.field_error_distribution !== undefined) {
    version.report.field_error_distribution.forEach(({ key, count }) => {
      failureCounts.set(failureLabels[key] || key, count)
    })
  } else {
    version.items
      .filter((item) => item.verdict === 'failed')
      .forEach((item) => {
      const label = failureLabels[item.failure_code ?? '']
        || item.failure_reason
        || t('verification.unspecifiedFailure')
      failureCounts.set(label, (failureCounts.get(label) ?? 0) + 1)
      })
  }
  const passRate = Math.round(version.report.pass_rate * 100)
  const toPercent = (value: number | null | undefined, fallback: number) => Math.round(
    (value ?? fallback) * 100,
  )
  return {
    sampleCount: version.report.sample_count,
    errorCoverage: toPercent(version.report.error_coverage, 1 - version.report.pass_rate),
    precision: toPercent(version.report.error_precision, version.report.pass_rate),
    alignment: toPercent(version.report.human_machine_alignment, version.report.pass_rate),
    reviewLoad: version.report.review_load === null || version.report.review_load === undefined
      ? null
      : Math.round(version.report.review_load * 100),
    totalArtifacts: version.report.total_artifacts,
    passed: version.report.full_pass_count ?? version.report.pass_count,
    errors: version.report.full_fail_count ?? version.report.fail_count,
    uncertain: version.report.full_uncertain_count ?? 0,
    stale: version.report.stale_count ?? 0,
    relationChanged: version.report.relation_changed_count ?? 0,
    reviewRequired: version.report.review_required_count ?? 0,
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
    const beforeRate = previous
      ? Math.round((previous.report.human_machine_alignment ?? previous.report.pass_rate) * 100)
      : 0
    const afterRate = Math.round((version.report.human_machine_alignment ?? version.report.pass_rate) * 100)
    const enabledCount = version.rules.filter((rule) => rule.enabled).length
    const baselineTitle = version.version === 1
      ? t('verification.baselineV1')
      : version.version === 2
        ? t('verification.baselineV2')
        : version.assertion_baseline_name ?? t('verification.baselineExecution')
    return {
      version: version.version,
      createdAt: formatTimestamp(version.created_at),
      title: baselineTitle,
      summary: t('version.summary', {
        rules: enabledCount,
        records: version.report.total_artifacts,
        samples: version.report.sample_count,
        matching: version.matching_version_id ?? 'M0',
      }),
      matchingVersionId: version.matching_version_id ?? 'M0',
      staleCount: version.report.stale_count ?? 0,
      relationChangedCount: version.report.relation_changed_count ?? 0,
      exportable: (version.report.stale_count ?? 0) === 0
        && version.report.pass_count + version.report.fail_count === version.report.sample_count,
      before: previous
        ? `Alignment ${beforeRate}%, ${previous.report.full_fail_count ?? previous.report.fail_count} records require review.`
        : 'No previous verification baseline.',
      after: `Alignment ${afterRate}%, ${version.report.full_fail_count ?? version.report.fail_count} records require review.`,
      impact: {
        alignmentBefore: beforeRate,
        alignmentAfter: afterRate,
        errorsBefore: previous?.report.full_fail_count ?? previous?.report.fail_count ?? 0,
        errorsAfter: version.report.full_fail_count ?? version.report.fail_count,
        passedBefore: previous?.report.full_pass_count ?? previous?.report.pass_count ?? 0,
        passedAfter: version.report.full_pass_count ?? version.report.pass_count,
      },
    }
  }),
)

async function loadVersions() {
  if (!props.jobId) {
    versionSnapshots.value = []
    loadingVersions.value = false
    return
  }
  const requestId = ++loadVersionsRequestId
  loadingVersions.value = true
  try {
    const [activeExperiment, experiments] = await Promise.all([
      getActiveVerificationExperiment(props.jobId),
      getVerificationExperiments(props.jobId),
    ])
    if (requestId !== loadVersionsRequestId) return
    verificationExperiment.value = activeExperiment
    verificationExperiments.value = experiments
    if (!viewingExperimentId.value || !experiments.some((item) => item.id === viewingExperimentId.value)) {
      viewingExperimentId.value = activeExperiment.id
    }
    versionSnapshots.value = await getVerificationVersions(props.jobId, {
      experimentId: viewingExperimentId.value,
    })
    if (requestId !== loadVersionsRequestId) return
    if (!viewingArchivedExperiment.value) {
      const [activeRun, activeSession] = await Promise.all([
        getActiveMachineVerificationRun(props.jobId),
        getActiveVerificationSession(props.jobId, { suppressErrorMessage: true }).catch(() => null),
      ])
      if (activeRun) {
        machineRun.value = activeRun
        if (['queued', 'running'].includes(activeRun.status) && !running.value) {
          running.value = true
          void pollMachineRun(activeRun.id)
        }
      }
      // 任务完成后刷新页面或切回第三页时，重新发现后台已创建的固定样本，
      // 直接恢复到人工核验，不要求用户重新跑一次全量校验。
      if (
        activeSession?.status === 'in_progress'
        && activeSession.id !== openedHumanReviewSessionId.value
      ) {
        openedHumanReviewSessionId.value = activeSession.id
        emit('startVerification', activeSession)
      }
    } else {
      machineRun.value = null
      running.value = false
    }
    const latest = sortedSnapshots.value[0]
    if (!sortedSnapshots.value.some((version) => version.version === selectedVersionNumber.value)) {
      selectedVersionNumber.value = latest?.version ?? null
    }
  } catch (error: unknown) {
    if (requestId === loadVersionsRequestId) {
      ElMessage.error(error instanceof Error ? error.message : t('verification.loadFailed'))
    }
  } finally {
    if (requestId === loadVersionsRequestId) loadingVersions.value = false
  }
}

/** 首次全量机器校验后抽取固定 18 条人工样本；后续断言仅复用这组样本。 */
async function executeVerification() {
  if (running.value || viewingArchivedExperiment.value) return
  if (!props.jobId) {
    ElMessage.warning(t('verification.needJob'))
    return
  }
  if (invalidV1Snapshot.value) {
    ElMessage.warning(t('verification.experiment.invalidV1Notice'))
    return
  }
  const enabledRules = rules.value.filter((rule) => rule.enabled)
  const baselineId = targetBaselineId.value
  if (!baselineId) {
    ElMessage.warning(t('verification.baselineUnavailable', {
      version: targetVersionNumber.value,
    }))
    return
  }
  if (!enabledRules.length) {
    ElMessage.warning(t('verification.needRule'))
    return
  }

  running.value = true
  stopMachineRunPolling()
  try {
    machineRun.value = await startMachineVerificationRun(
      props.jobId,
      rules.value,
      18,
      baselineId,
    )
    await pollMachineRun(machineRun.value.id)
  } catch (error: unknown) {
    running.value = false
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  }
}

async function resetInvalidV1() {
  if (!props.jobId || !invalidV1Snapshot.value || invalidV1Resetting.value) return
  invalidV1Resetting.value = true
  try {
    await resetInvalidVerificationV1(props.jobId)
    machineRun.value = null
    selectedVersionNumber.value = null
    versionSnapshots.value = []
    configuredRulesForTargetVersion.value = null
    rules.value = createDefaultRules()
    await loadVersions()
    if (versionSnapshots.value.some((version) => version.version === 1)) {
      throw new Error(t('verification.invalidV1RefreshIncomplete'))
    }
    ElMessage.success(t('verification.invalidV1Cleared'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  } finally {
    invalidV1Resetting.value = false
  }
}

function beginNewExperiment() {
  if (!props.jobId) {
    ElMessage.warning(t('verification.needJob'))
    return
  }
  if (!canCreateExperiment.value) {
    ElMessage.warning(newExperimentBlockedReason.value || t('verification.newExperimentBlocked'))
    return
  }
  newExperimentConfirmOpen.value = true
}

function cancelNewExperiment() {
  if (!experimentCreating.value) newExperimentConfirmOpen.value = false
}

async function confirmNewExperiment() {
  if (!props.jobId || experimentCreating.value || !canCreateExperiment.value) return
  experimentCreating.value = true
  try {
    verificationExperiment.value = await createVerificationExperiment(props.jobId)
    viewingExperimentId.value = verificationExperiment.value.id
    machineRun.value = null
    selectedVersionNumber.value = null
    versionSnapshots.value = []
    configuredRulesForTargetVersion.value = null
    rules.value = createDefaultRules()
    await loadVersions()
    newExperimentConfirmOpen.value = false
    ElMessage.success(t('verification.experimentReady', { name: verificationExperiment.value.name }))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  } finally {
    experimentCreating.value = false
  }
}

async function viewExperiment(experimentId: string) {
  if (experimentId === viewingExperimentId.value || loadingVersions.value) return
  viewingExperimentId.value = experimentId
  selectedVersionNumber.value = null
  configuredRulesForTargetVersion.value = null
  rules.value = createDefaultRules()
  await loadVersions()
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
      experimentId: version.experiment_id ?? 'legacy',
      experimentName: version.experiment_name ?? t('verification.experiment.legacyName'),
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

/** Export every persisted full-run item, including field-level decisions, as Excel. */
async function exportFullMachineDetails() {
  const version = selectedSnapshot.value
  if (!props.jobId || !version || exportingMachineDetails.value) {
    ElMessage.warning(t('verification.selectCompletedVersion'))
    return
  }
  exportingMachineDetails.value = true
  try {
    const blob = await downloadFullMachineVerificationExcel(props.jobId, version.id)
    const url = globalThis.URL.createObjectURL(blob)
    const link = globalThis.document.createElement('a')
    link.href = url
    link.download = t('verification.machineDetailsFileName', { version: version.version })
    link.click()
    globalThis.URL.revokeObjectURL(url)
    ElMessage.success(t('verification.machineDetailsExported', { version: version.version }))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.machineDetailsExportFailed'))
  } finally {
    exportingMachineDetails.value = false
  }
}

defineExpose({ exportResult, exportFullMachineDetails, refreshVersions: loadVersions })

watch(
  () => props.jobId,
  () => {
    stopRematchPolling()
    stopMachineRunPolling()
    rematchRun.value = null
    rematchReportOpen.value = false
    rematchChanges.value = []
    machineRun.value = null
    selectedVersionNumber.value = null
    viewingExperimentId.value = ''
    verificationExperiments.value = []
    newExperimentConfirmOpen.value = false
    experimentCreating.value = false
    openedHumanReviewSessionId.value = ''
    void loadVersions()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopRematchPolling()
  stopMachineRunPolling()
})
</script>

<template>
  <div
    class="machine-verification-workspace"
    :class="{ 'machine-verification-workspace--compact': !showMatchingPanel && !showQualityEvaluationPanel }"
  >
    <section
      v-if="showMatchingPanel"
      class="matching-panel panel"
    >
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
      v-if="showMatchingPanel && rematchReportOpen && rematchRun"
      :run="rematchRun"
      :changes="rematchChanges"
      :loading="rematchChangesLoading"
      :applying="rematchApplying"
      @close="rematchReportOpen = false"
      @discard="discardMatchingPreview"
      @apply="applyMatchingPreview"
    />

    <QualityEvaluationPanel
      v-if="showQualityEvaluationPanel"
      :job-id="jobId"
      :document-id="documentId"
    />

    <AssertionRules
      v-model:rules="rules"
      :baseline-id="targetBaselineId"
      :target-version="targetVersionNumber"
      :running="running"
      :paused="machinePaused"
      :terminating="terminatingMachineVerification"
      :view-only="viewingArchivedExperiment"
      @execute="executeVerification"
      @pause="pauseMachineVerification"
      @resume="resumeMachineVerification"
      @terminate="terminateMachineVerification"
    />

    <section class="experiment-bar panel">
      <div class="experiment-bar__header">
        <div>
        <span>{{ viewingArchivedExperiment ? t('verification.experiment.historyTitle') : t('verification.experiment.currentTitle') }}</span>
        <strong>{{ viewingExperimentSummary || experimentSummary || t('common.loading') }}</strong>
        <small v-if="viewingArchivedExperiment">{{ t('verification.experiment.archivedHint') }}</small>
        <small v-else-if="awaitingInitialHumanReview">{{ t('verification.experiment.humanReviewReady') }}</small>
        <small v-else-if="invalidV1Snapshot" class="invalid-v1-hint">{{ t('verification.experiment.invalidV1Hint') }}</small>
        <small v-else>{{ t('verification.experiment.newHint') }}</small>
        </div>
        <button
          v-if="awaitingInitialHumanReview"
          type="button"
          class="open-human-review-button"
          :disabled="openingHumanReview"
          @click="openHumanReview()"
        >
          {{ openingHumanReview ? t('verification.experiment.openingReview') : t('verification.experiment.openReview') }}
        </button>
        <label v-if="verificationExperiments.length > 1" class="experiment-switcher">
          <span>{{ t('verification.experiment.switch') }}</span>
          <el-select
            class="experiment-switcher__select"
            :model-value="viewingExperimentId"
            :disabled="running || loadingVersions || experimentCreating"
            popper-class="experiment-switcher-popper"
            @change="viewExperiment"
          >
            <el-option
              v-for="experiment in verificationExperiments"
              :key="experiment.id"
              :label="`${displayExperimentName(experiment)}${experiment.id === verificationExperiment?.id ? t('verification.experiment.currentSuffix') : t('verification.experiment.historySuffix')}`"
              :value="experiment.id"
            />
          </el-select>
        </label>
        <button
          v-if="invalidV1Snapshot"
          class="reset-invalid-v1-button"
          type="button"
          :disabled="invalidV1Resetting"
          @click="resetInvalidV1"
        >
          {{ invalidV1Resetting ? t('verification.experiment.clearingInvalidV1') : t('verification.experiment.resetInvalidV1') }}
        </button>
        <button
          class="new-experiment-button"
          type="button"
          :disabled="!canCreateExperiment"
          :title="newExperimentBlockedReason || t('verification.experiment.createHint')"
          @click="beginNewExperiment"
        >
          <span aria-hidden="true">＋</span>
          {{ experimentCreating ? t('verification.experiment.creating') : t('verification.experiment.create') }}
        </button>
      </div>
      <small v-if="newExperimentBlockedReason" class="new-experiment-blocked-reason">
        {{ newExperimentBlockedReason }}
      </small>
      <div v-if="newExperimentConfirmOpen" class="new-experiment-confirm" role="status">
        <div>
          <strong>{{ t('verification.experiment.confirmTitle') }}</strong>
          <p>{{ t('verification.experiment.confirmHint') }}</p>
        </div>
        <div class="new-experiment-confirm__actions">
          <button type="button" :disabled="experimentCreating" @click="cancelNewExperiment">{{ t('common.cancel') }}</button>
          <button type="button" :disabled="experimentCreating" @click="confirmNewExperiment">
            {{ experimentCreating ? t('verification.experiment.creating') : t('verification.experiment.confirmCreate') }}
          </button>
        </div>
      </div>
    </section>

    <section class="verification-result panel">
      <h2>{{ t('verification.result') }}</h2>
      <div class="verification-result__content">
        <VerificationSummary
          :report="report"
          :running="running || loadingVersions"
          :progress-text="machineProgressText"
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

.machine-verification-workspace--compact {
  grid-template-areas:
    'experiment experiment'
    'rules result';
  grid-template-rows: auto minmax(0, 1fr);
}

.machine-verification-workspace--compact :deep(.assertion-rules) {
  grid-area: rules;
}

.machine-verification-workspace--compact .verification-result {
  grid-area: result;
}

.experiment-bar {
  grid-area: experiment;
  display: grid;
  gap: 10px;
  padding: 12px 16px;

  &__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  &__header > div {
    display: grid;
    grid-template-columns: auto auto;
    gap: 3px 10px;
    align-items: baseline;
  }

  span { color: var(--af-text-secondary); font-size: 13px; }
  strong { color: var(--af-primary-dark); }
  small { grid-column: 1 / -1; color: var(--af-text-secondary); }

}

.new-experiment-button {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 38px;
  padding: 8px 15px;
  font-weight: 700;
  color: #fff;
  cursor: pointer;
  background: linear-gradient(135deg, #d9781d, #b95b14);
  border: 1px solid #ae5010;
  border-radius: 8px;
  box-shadow: 0 3px 8px rgb(177 81 16 / 22%);
  transition: transform .16s ease, box-shadow .16s ease, background .16s ease;

  span { font-size: 19px; font-weight: 400; line-height: 0; }

  &:hover:not(:disabled) {
    background: linear-gradient(135deg, #e38424, #c56316);
    box-shadow: 0 5px 12px rgb(177 81 16 / 28%);
    transform: translateY(-1px);
  }

  &:focus-visible { outline: 3px solid rgb(225 132 45 / 34%); outline-offset: 2px; }
  &:disabled { cursor: not-allowed; opacity: .58; box-shadow: none; }
}

.reset-invalid-v1-button {
  flex: 0 0 auto;
  min-height: 38px;
  padding: 8px 13px;
  color: #a94b20;
  font-weight: 650;
  cursor: pointer;
  background: #fff4ed;
  border: 1px solid #e6a47e;
  border-radius: 8px;

  &:hover:not(:disabled) { background: #ffe8da; border-color: #d77845; }
  &:disabled { cursor: wait; opacity: .62; }
}

.experiment-bar .invalid-v1-hint { color: #bb562d; font-weight: 600; }

.open-human-review-button {
  flex: 0 0 auto;
  padding: 8px 13px;
  color: #fff;
  font-weight: 650;
  background: var(--af-primary);
  border: 1px solid var(--af-primary-dark);
  border-radius: 7px;
  box-shadow: 0 2px 6px rgb(151 82 27 / 22%);
  cursor: pointer;

  &:disabled {
    cursor: wait;
    opacity: .65;
  }
}

.new-experiment-blocked-reason {
  padding: 7px 10px;
  color: #8a5d35;
  font-size: 12px;
  line-height: 1.45;
  background: #fff8f0;
  border: 1px solid #efd9c4;
  border-radius: 7px;
}

.new-experiment-confirm {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 12px 14px;
  background: #fff7ed;
  border: 1px solid #f0c493;
  border-radius: 9px;

  strong { color: #8e4715; }
  p { max-width: 760px; margin: 4px 0 0; font-size: 12px; line-height: 1.55; color: #745946; }

  &__actions {
    display: flex;
    flex: 0 0 auto;
    gap: 8px;

    button {
      min-width: 76px;
      min-height: 32px;
      padding: 6px 11px;
      color: #87501f;
      cursor: pointer;
      background: #fff;
      border: 1px solid #dcb58d;
      border-radius: 7px;

      &:last-child { color: #fff; background: #c96518; border-color: #bd5911; }
      &:disabled { cursor: not-allowed; opacity: .6; }
    }
  }
}

.experiment-switcher {
  display: flex;
  flex: 0 1 240px;
  gap: 7px;
  align-items: center;
  min-width: 205px;
  font-size: 11px;
  color: var(--af-text-secondary);
}

.experiment-switcher span {
  flex: 0 0 auto;
  white-space: nowrap;
}

.experiment-switcher__select {
  flex: 1 1 auto;
  min-width: 0;
}

.experiment-switcher__select :deep(.el-select__wrapper) {
  min-height: 32px;
  padding: 0 9px;
  font-size: 12px;
  color: #7f4b21;
  background: #fffdf9;
  box-shadow: 0 0 0 1px #ddc3a8 inset;
  border-radius: 7px;
  transition: box-shadow .16s ease, background .16s ease;
}

.experiment-switcher__select :deep(.el-select__wrapper:hover) {
  background: #fff9f2;
  box-shadow: 0 0 0 1px #c8824c inset;
}

.experiment-switcher__select :deep(.is-focused .el-select__wrapper) {
  box-shadow: 0 0 0 1px #b96528 inset, 0 0 0 3px rgb(201 111 43 / 12%);
}

.experiment-switcher__select :deep(.el-select__caret) {
  color: #ae7650;
  font-size: 13px;
}

.experiment-switcher__select.is-disabled { opacity: .62; }

:global(.experiment-switcher-popper.el-popper) {
  padding: 5px;
  background: #fffdfa;
  border: 1px solid #e4cdb9;
  border-radius: 9px;
  box-shadow: 0 10px 24px rgb(94 57 28 / 16%);
}

:global(.experiment-switcher-popper .el-select-dropdown__item) {
  height: 34px;
  padding: 0 10px;
  font-size: 12px;
  line-height: 34px;
  color: #754b30;
  border-radius: 6px;
}

:global(.experiment-switcher-popper .el-select-dropdown__item.is-hovering) {
  color: #9a4e18;
  background: #fff1e3;
}

:global(.experiment-switcher-popper .el-select-dropdown__item.is-selected) {
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #d0782d, #b95d1c);
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

  .machine-verification-workspace--compact {
    grid-template-areas:
      'experiment'
      'rules'
      'result';
    grid-template-rows: auto 620px 780px;
  }
}

@media (max-width: 650px) {
  .experiment-bar__header,
  .new-experiment-confirm {
    align-items: stretch;
    flex-direction: column;
  }

  .new-experiment-button { width: 100%; }
  .new-experiment-confirm__actions button { flex: 1; }

  .verification-result__content {
    grid-template-columns: 1fr;
    grid-template-rows: 620px 620px;
  }

  .machine-verification-workspace {
    grid-template-rows: auto auto 620px 1280px;
  }

  .machine-verification-workspace--compact {
    grid-template-rows: auto 620px 1280px;
  }
}
</style>
