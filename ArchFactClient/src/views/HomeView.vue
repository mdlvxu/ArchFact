<script setup lang="ts">
import { Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import {
  cancelExtractionJob,
  completeVerificationSession,
  createExtractionJob,
  getDocumentImageContentUrl,
  getDocumentImages,
  getExtractionJob,
  getLatestCompletedExtractionJob,
  getAiVerificationRun,
  getRegionCropContentUrl,
  getPageAnnotations,
  getExtractionRecords,
  getRecordEvidenceContext,
  getVerificationSessionRecords,
  getVerificationSession,
  getUploadedDocument,
  rebindRegionRelation,
  retryFailedExtractionPages,
  updateRegionRelationReview,
  updateVerificationSessionItem,
  uploadPdfDocument,
} from '@/api/modules/extraction'
import ArchaeologicalCatalogs from '@/components/business/ArchaeologicalCatalogs.vue'
import ContentPreview from '@/components/business/ContentPreview.vue'
import ExtractionSettings from '@/components/business/ExtractionSettings.vue'
import LanguageToggle from '@/components/business/LanguageToggle.vue'
import MachineVerificationWorkspace from '@/components/business/MachineVerificationWorkspace.vue'
import PageNavigator from '@/components/business/PageNavigator.vue'
import PdfOverview from '@/components/business/PdfOverview.vue'
import ProcessingPanel, {
  type ProcessLog,
} from '@/components/business/ProcessingPanel.vue'
import RelatedPages from '@/components/business/RelatedPages.vue'
import { buildPreviewAnnotations } from '@/domain/preview-annotations'
import { filterExtractedPdfPages } from '@/domain/preview-pages'
import { getDefaultExtractionPages } from '@/domain/page-selection'
import { useI18n } from '@/i18n'
import type {
  ExtractionConfigPayload,
  ExtractionJob,
  ExtractionRecord,
  PageAnnotations,
  PreviewAnnotation,
  RecordEvidenceContext,
  RegionRelation,
} from '@/types/extraction'
import type { PdfPageItem } from '@/types/pdf'
import type {
  AiVerificationRun,
  VerificationFailureCode,
  VerificationItem,
  VerificationSession,
} from '@/types/verification'

// PDF.js 使用独立 Worker 解析文件，避免大文档阻塞页面交互
GlobalWorkerOptions.workerSrc = pdfWorkerUrl

/** 页面顶部的业务步骤导航 */
type WorkspaceTab = 'Data Extraction' | 'Data Preview' | 'Machine Verification'
type PreviewMode = 'browse' | 'verify'

const tabs: WorkspaceTab[] = ['Data Extraction', 'Data Preview', 'Machine Verification']
const tabLabels: Record<WorkspaceTab, string> = {
  'Data Extraction': 'nav.extraction',
  'Data Preview': 'nav.preview',
  'Machine Verification': 'nav.verification',
}
const { t } = useI18n()
const activeTab = ref<WorkspaceTab>('Data Extraction')
const activePage = ref(1)
const previewSelectedPage = ref<number | null>(null)
const activeAnnotationId = ref('')
const selectedRecordId = ref('')
const catalogDetailsOpen = ref(false)
const reviewSavingId = ref('')
const relationSavingId = ref('')
const stopped = ref(false)
const progress = ref(0)
const fileInputRef = ref<HTMLInputElement>()
const machineVerificationRef = ref<InstanceType<typeof MachineVerificationWorkspace>>()
const pdfDocument = shallowRef<PDFDocumentProxy>()
const pdfPages = ref<PdfPageItem[]>([])
const selectedExtractionPages = ref<number[]>([])
const extractionTaskPages = ref<number[]>([])
const pdfFileName = ref('')
const previewUrl = ref('')
const previewLoading = ref(false)
const uploadLoading = ref(false)
const serverDocumentId = ref('')
const currentJobId = ref('')
const jobRunning = ref(false)
const processingStartedAt = ref<string | null>(null)
const processingEndedAt = ref<string | null>(null)
const processedPages = ref(0)
const totalProcessingPages = ref(0)
const failedJobPages = ref(0)
const extractionRecords = ref<ExtractionRecord[]>([])
const activeMatchingVersionId = ref('M0')
const previewMode = ref<PreviewMode>('browse')
const verificationSession = ref<VerificationSession | null>(null)
const verificationRecords = ref<ExtractionRecord[]>([])
const verificationCompleting = ref(false)
const verificationAiRun = ref<AiVerificationRun | null>(null)
const pageAnnotationData = ref<PageAnnotations | null>(null)
const recordEvidenceContext = ref<RecordEvidenceContext | null>(null)
const evidencePagePreviewUrls = ref<Record<number, string>>({})
const renderingThumbnails = new Set<string>()
const renderingEvidencePages = new Set<string>()
const evidencePreviewOrder: number[] = []
let previewRequestId = 0
let pdfDocumentVersion = 0
let jobPollTimer: number | undefined
let jobPollFailureCount = 0
let annotationRequestId = 0
let evidenceContextRequestId = 0
const lastExtractionJobStorageKey = 'archfact:last-extraction-job-id'

const logs = ref<ProcessLog[]>([])

/** 第二页只使用本次任务提交的页码，不受第一页后续选择状态影响。 */
const extractedPdfPages = computed(() =>
  filterExtractedPdfPages(pdfPages.value, extractionTaskPages.value),
)
const pagePreviewUrls = computed<Record<number, string>>(() => ({
  ...Object.fromEntries(
    pdfPages.value
      .filter((item) => Boolean(item.thumbnailUrl))
      .map((item) => [item.page, item.thumbnailUrl]),
  ),
  ...evidencePagePreviewUrls.value,
}))

const previewCatalogRecords = computed(() =>
  previewMode.value === 'verify' ? verificationRecords.value : extractionRecords.value,
)

const verificationReviewStatuses = computed<
  Record<string, ExtractionRecord['review_status']>
>(() =>
  Object.fromEntries(
    (verificationSession.value?.items ?? []).map((item) => [item.record_id, item.verdict]),
  ),
)

const verificationReviewItems = computed<Record<string, VerificationItem>>(() =>
  Object.fromEntries(
    (verificationSession.value?.items ?? []).map((item) => [item.record_id, item]),
  ),
)

const verificationStaleItems = computed(() =>
  (verificationSession.value?.items ?? []).filter((item) => item.stale),
)

const verificationRemaining = computed(() =>
  verificationSession.value
    ? verificationSession.value.sample_count - verificationSession.value.reviewed_count
    : 0,
)

const currentPreviewRecords = computed(() => {
  if (previewSelectedPage.value === null) return []
  return previewCatalogRecords.value.filter((record) =>
    record.source_pages.includes(previewSelectedPage.value as number),
  )
})

const previewAnnotations = computed<PreviewAnnotation[]>(() => {
  const page = previewSelectedPage.value
  if (page === null) return []
  const context = recordEvidenceContext.value
  const annotationData = context
    ? {
        page,
        regions: context.regions,
        relations: context.relations,
        records: [context.record],
      }
    : pageAnnotationData.value?.page === page
      ? pageAnnotationData.value
      : null
  const cropUrl = currentJobId.value
    ? (regionId: string) => getRegionCropContentUrl(currentJobId.value, regionId)
    : undefined
  return buildPreviewAnnotations(
    context ? [context.record] : currentPreviewRecords.value,
    annotationData,
    page,
    cropUrl,
  )
})

const evidenceContextAnnotations = computed<PreviewAnnotation[]>(() => {
  const context = recordEvidenceContext.value
  if (!context || !currentJobId.value) return previewAnnotations.value
  const cropUrl = (regionId: string) => getRegionCropContentUrl(currentJobId.value, regionId)
  return context.page_numbers.flatMap((page) =>
    buildPreviewAnnotations(
      [context.record],
      {
        page,
        regions: context.regions,
        relations: context.relations,
        records: [context.record],
      },
      page,
      cropUrl,
    ),
  )
})

const previewRegions = computed(
  () => recordEvidenceContext.value?.regions ?? pageAnnotationData.value?.regions ?? [],
)
const previewRelations = computed(
  () => recordEvidenceContext.value?.relations ?? pageAnnotationData.value?.relations ?? [],
)

watch(
  previewAnnotations,
  (annotations) => {
    if (!annotations.some((annotation) => annotation.id === activeAnnotationId.value)) {
      activeAnnotationId.value = annotations[0]?.id ?? ''
    }
  },
  { immediate: true },
)

watch(
  currentPreviewRecords,
  (records) => {
    if (!records.some((record) => record.id === selectedRecordId.value)) {
      selectedRecordId.value = records[0]?.id ?? ''
    }
  },
  { immediate: true },
)

/** 切换业务步骤，暂未开发的页面给出明确反馈 */
function changeTab(tab: WorkspaceTab) {
  activeTab.value = tab
}

/** 调用第三页工作区导出当前机器校验结果。 */
function exportVerificationResult() {
  machineVerificationRef.value?.exportResult()
}

/** 清空当前任务日志 */
function clearLogs() {
  logs.value = []
  ElMessage.success(t('home.logCleared'))
}

/** 将当前日志导出为本地文本文件 */
function exportLogs() {
  const content = logs.value.map((item) => `[${item.status}] ${item.text}`).join('\n')
  const blob = new globalThis.Blob([content || 'No processing logs'], {
    type: 'text/plain;charset=utf-8',
  })
  const downloadUrl = globalThis.URL.createObjectURL(blob)
  const link = globalThis.document.createElement('a')
  link.href = downloadUrl
  link.download = 'archfact-processing-log.txt'
  link.click()
  globalThis.URL.revokeObjectURL(downloadUrl)
}

function stopJobPolling() {
  if (jobPollTimer !== undefined) {
    globalThis.clearTimeout(jobPollTimer)
    jobPollTimer = undefined
  }
  jobPollFailureCount = 0
}

function applyJobState(job: ExtractionJob) {
  const terminal = ['completed', 'completed_with_warnings', 'failed', 'cancelled'].includes(
    job.status,
  )
  progress.value = job.progress.percent
  processingStartedAt.value = job.attempt_started_at ?? job.created_at
  processingEndedAt.value = terminal ? job.updated_at : null
  jobRunning.value = !terminal
  processedPages.value = job.progress.current
  totalProcessingPages.value = job.progress.total || totalProcessingPages.value
  failedJobPages.value = job.failed_pages
  stopped.value = job.status === 'cancelled' || job.status === 'cancelling'
  activeMatchingVersionId.value = job.active_matching_version_id ?? 'M0'
  logs.value = job.events.map((event, index) => ({
    id: index + 1,
    status: event.level,
    text: event.message,
  }))
}

async function pollExtractionJob(jobId: string) {
  try {
    const job = await getExtractionJob(jobId)
    if (currentJobId.value !== jobId) return
    jobPollFailureCount = 0
    applyJobState(job)

    if (job.status === 'completed' || job.status === 'completed_with_warnings') {
      extractionRecords.value = await getExtractionRecords(jobId)
      previewMode.value = 'browse'
      verificationSession.value = null
      verificationRecords.value = []
      jobRunning.value = false
      previewSelectedPage.value = null
      activeAnnotationId.value = ''
      selectedRecordId.value = ''
      pageAnnotationData.value = null
      recordEvidenceContext.value = null
      ++evidenceContextRequestId
      activeTab.value = 'Data Preview'
      if (job.status === 'completed_with_warnings') {
        ElMessage.warning(t('home.completedWithWarnings', {
          count: extractionRecords.value.length,
          failed: job.failed_pages,
        }))
      } else {
        ElMessage.success(t('home.completed', { count: extractionRecords.value.length }))
      }
      return
    }
    if (job.status === 'failed') {
      jobRunning.value = false
      ElMessage.error(job.error || t('home.extractionFailed'))
      return
    }
    if (job.status === 'cancelled') {
      jobRunning.value = false
      ElMessage.info(t('home.cancelled'))
      return
    }
    jobPollTimer = globalThis.setTimeout(() => void pollExtractionJob(jobId), 1200)
  } catch (error: unknown) {
    if (currentJobId.value !== jobId) return
    jobPollFailureCount += 1
    jobRunning.value = true
    processingEndedAt.value = null
    if (jobPollFailureCount === 1) {
      logs.value = [
        ...logs.value,
        {
          id: logs.value.length + 1,
          status: 'WARNING',
          text: error instanceof Error ? error.message : t('home.progressFailed'),
        },
      ]
      ElMessage.warning(t('home.progressFailed'))
    }
    const retryDelay = Math.min(15_000, 1_200 * 2 ** Math.min(jobPollFailureCount, 3))
    jobPollTimer = globalThis.setTimeout(() => void pollExtractionJob(jobId), retryDelay)
  }
}

async function retryFailedJobPages() {
  if (!currentJobId.value || jobRunning.value || failedJobPages.value <= 0) return
  const jobId = currentJobId.value
  stopJobPolling()
  try {
    const job = await retryFailedExtractionPages(jobId)
    applyJobState(job)
    jobRunning.value = true
    stopped.value = false
    processingEndedAt.value = null
    activeTab.value = 'Data Extraction'
    ElMessage.success(t('home.retryStarted', { count: job.retry_pages?.length ?? 0 }))
    await pollExtractionJob(jobId)
  } catch (error: unknown) {
    jobRunning.value = false
    ElMessage.error(error instanceof Error ? error.message : t('home.retryFailed'))
  }
}

/** 取消当前抽取任务。已完成和已取消任务不可恢复，需要重新创建任务。 */
async function toggleTask() {
  if (!currentJobId.value || !jobRunning.value) {
    ElMessage.info(t('home.noRunningTask'))
    return
  }
  const cancelledJobId = currentJobId.value
  stopJobPolling()
  currentJobId.value = ''
  jobRunning.value = false
  stopped.value = true
  processingEndedAt.value = new Date().toISOString()
  logs.value = [
    ...logs.value,
    { id: logs.value.length + 1, status: 'INFO', text: t('home.stoppingNow') },
  ]
  try {
    const job = await cancelExtractionJob(cancelledJobId)
    if (!jobRunning.value && !currentJobId.value) {
      applyJobState(job)
      ElMessage.info(t('home.cancelled'))
    }
  } catch (error: unknown) {
    if (!jobRunning.value && !currentJobId.value) {
      currentJobId.value = cancelledJobId
      jobRunning.value = true
      stopped.value = false
      processingEndedAt.value = null
      void pollExtractionJob(cancelledJobId)
    }
    ElMessage.error(error instanceof Error ? error.message : t('home.cancelFailed'))
  }
}

/** 创建后端异步任务，前端只依赖业务任务契约，不感知 Coze Workflow ID。 */
async function startExtraction(config: ExtractionConfigPayload) {
  if (!serverDocumentId.value) {
    ElMessage.warning(t('home.noDocument'))
    return
  }
  if (!config.pages.length) {
    ElMessage.warning(t('home.noPages'))
    return
  }
  if (jobRunning.value) {
    ElMessage.warning(t('home.alreadyRunning'))
    return
  }

  stopJobPolling()
  jobPollFailureCount = 0
  progress.value = 0
  stopped.value = false
  processingStartedAt.value = new Date().toISOString()
  processingEndedAt.value = null
  processedPages.value = 0
  totalProcessingPages.value = config.pages.length
  failedJobPages.value = 0
  extractionTaskPages.value = []
  extractionRecords.value = []
  previewMode.value = 'browse'
  verificationSession.value = null
  verificationRecords.value = []
  verificationCompleting.value = false
  pageAnnotationData.value = null
  recordEvidenceContext.value = null
  ++evidenceContextRequestId
  ++annotationRequestId
  previewSelectedPage.value = null
  activeAnnotationId.value = ''
  selectedRecordId.value = ''
  logs.value = [{ id: 1, status: 'INFO', text: t('home.creating') }]
  jobRunning.value = true
  try {
    const created = await createExtractionJob(serverDocumentId.value, config)
    currentJobId.value = created.job_id
    globalThis.localStorage.setItem(lastExtractionJobStorageKey, created.job_id)
    extractionTaskPages.value = [...config.pages]
    ElMessage.success(t('home.created'))
    await pollExtractionJob(created.job_id)
  } catch (error: unknown) {
    jobRunning.value = false
    processingStartedAt.value = null
    processingEndedAt.value = null
    processedPages.value = 0
    totalProcessingPages.value = 0
    ElMessage.error(error instanceof Error ? error.message : t('home.createFailed'))
  }
}

/** 打开操作系统的 PDF 文件选择窗口 */
function openPdfPicker() {
  fileInputRef.value?.click()
}

/** 将 PDF 指定页渲染为图片，供缩略图和主预览共同使用 */
async function renderPdfPage(
  pageNumber: number,
  scale: number,
  quality = 0.92,
  document = pdfDocument.value,
) {
  if (!document) {
    throw new Error('PDF 文档尚未加载')
  }

  const page = await document.getPage(pageNumber)
  const viewport = page.getViewport({ scale })
  const canvas = globalThis.document.createElement('canvas')
  const context = canvas.getContext('2d')

  if (!context) {
    throw new Error('当前浏览器不支持 PDF 画布渲染')
  }

  canvas.width = Math.ceil(viewport.width)
  canvas.height = Math.ceil(viewport.height)
  await page.render({
    canvas,
    canvasContext: context,
    viewport,
  }).promise

  return canvas.toDataURL('image/jpeg', quality)
}

/** 渲染用户当前选中的 PDF 页面，并避免快速点击时旧页面覆盖新页面 */
async function renderSelectedPage(pageNumber: number) {
  const requestId = ++previewRequestId
  const documentVersion = pdfDocumentVersion
  const document = pdfDocument.value
  previewLoading.value = true

  try {
    if (!document) {
      const restoredUrl = pagePreviewUrls.value[pageNumber]
      if (!restoredUrl) throw new Error('当前页面预览图尚未生成')
      if (requestId === previewRequestId) previewUrl.value = restoredUrl
      return
    }
    const imageUrl = await renderPdfPage(pageNumber, 2.2, 0.94, document)
    if (requestId === previewRequestId && documentVersion === pdfDocumentVersion) {
      previewUrl.value = imageUrl
    }
  } catch (error: unknown) {
    if (requestId === previewRequestId && documentVersion === pdfDocumentVersion) {
      ElMessage.error(error instanceof Error ? error.message : t('home.previewFailed'))
    }
  } finally {
    if (requestId === previewRequestId && documentVersion === pdfDocumentVersion) {
      previewLoading.value = false
    }
  }
}

/** Render related evidence pages at preview resolution so focused artifacts stay sharp. */
async function renderEvidencePage(pageNumber: number) {
  const documentVersion = pdfDocumentVersion
  const renderKey = `${documentVersion}:${pageNumber}`
  const document = pdfDocument.value
  if (
    !document ||
    evidencePagePreviewUrls.value[pageNumber] ||
    renderingEvidencePages.has(renderKey)
  ) return

  renderingEvidencePages.add(renderKey)
  try {
    const imageUrl = await renderPdfPage(pageNumber, 1.45, 0.92, document)
    if (documentVersion !== pdfDocumentVersion) return
    const nextUrls = { ...evidencePagePreviewUrls.value, [pageNumber]: imageUrl }
    const existingIndex = evidencePreviewOrder.indexOf(pageNumber)
    if (existingIndex >= 0) evidencePreviewOrder.splice(existingIndex, 1)
    evidencePreviewOrder.push(pageNumber)
    while (evidencePreviewOrder.length > 8) {
      const expiredPage = evidencePreviewOrder.shift()
      if (expiredPage !== undefined) delete nextUrls[expiredPage]
    }
    evidencePagePreviewUrls.value = nextUrls
  } catch {
    // The low-resolution thumbnail remains available if a focused render fails.
  } finally {
    renderingEvidencePages.delete(renderKey)
  }
}

/** 点击左侧缩略图时切换主预览页 */
function selectPdfPage(pageNumber: number) {
  activePage.value = pageNumber
  void renderSelectedPage(pageNumber)
}

/** Data Preview 的页面选择独立于抽取页，首次进入时允许保持未选择状态。 */
function selectPreviewPage(pageNumber: number, preferredRecordId = '') {
  recordEvidenceContext.value = null
  ++evidenceContextRequestId
  previewSelectedPage.value = pageNumber
  activePage.value = pageNumber
  const records = previewCatalogRecords.value.filter((record) =>
    record.source_pages.includes(pageNumber),
  )
  selectedRecordId.value =
    records.find((record) => record.id === preferredRecordId)?.id ?? records[0]?.id ?? ''
  activeAnnotationId.value = ''
  pageAnnotationData.value = null
  void renderThumbnail(pageNumber)
  void renderSelectedPage(pageNumber)
  void loadPageAnnotations(pageNumber)
}

async function loadPageAnnotations(pageNumber: number) {
  if (!currentJobId.value) return
  const requestId = ++annotationRequestId
  try {
    const data = await getPageAnnotations(currentJobId.value, pageNumber)
    if (requestId === annotationRequestId && previewSelectedPage.value === pageNumber) {
      pageAnnotationData.value = data
    }
  } catch (error: unknown) {
    if (requestId === annotationRequestId && previewSelectedPage.value === pageNumber) {
      pageAnnotationData.value = null
      ElMessage.error(error instanceof Error ? error.message : t('home.previewFailed'))
    }
  }
}

function clearPreviewSelection() {
  ++annotationRequestId
  ++evidenceContextRequestId
  previewSelectedPage.value = null
  pageAnnotationData.value = null
  recordEvidenceContext.value = null
  activeAnnotationId.value = ''
  selectedRecordId.value = ''
  catalogDetailsOpen.value = false
}

async function selectCatalogRecord(record: ExtractionRecord, preferredAnnotationId = '') {
  if (!currentJobId.value) return
  const requestId = ++evidenceContextRequestId
  selectedRecordId.value = record.id
  catalogDetailsOpen.value = true
  activeAnnotationId.value = preferredAnnotationId
  pageAnnotationData.value = null
  try {
    const context = await getRecordEvidenceContext(currentJobId.value, record.id)
    if (requestId !== evidenceContextRequestId || selectedRecordId.value !== record.id) return
    recordEvidenceContext.value = context
    context.page_numbers.forEach((page) => {
      void renderThumbnail(page)
      void renderEvidencePage(page)
    })
    const sourcePage =
      record.source_pages.find((page) => context.page_numbers.includes(page)) ??
      context.page_numbers[0]
    if (!sourcePage) return
    previewSelectedPage.value = sourcePage
    activePage.value = sourcePage
    void renderSelectedPage(sourcePage)
  } catch (error: unknown) {
    if (requestId !== evidenceContextRequestId) return
    recordEvidenceContext.value = null
    ElMessage.error(error instanceof Error ? error.message : t('home.previewFailed'))
  }
}

/**
 * 中间画布与右侧目录共享同一个 record selection。点击任意证据框时，
 * 除了切换蓝色关系线，也会定位到该证据所属的器物卡片及内联详情。
 */
async function selectPreviewAnnotation(annotationId: string) {
  activeAnnotationId.value = annotationId
  const annotation = [...previewAnnotations.value, ...evidenceContextAnnotations.value]
    .find((item) => item.id === annotationId)
  if (!annotation?.recordId || annotation.recordId === selectedRecordId.value) return
  const record = previewCatalogRecords.value.find((item) => item.id === annotation.recordId)
  if (record) await selectCatalogRecord(record, annotationId)
}

async function reviewRecord(
  record: ExtractionRecord,
  status: 'passed' | 'failed',
  failureCode?: VerificationFailureCode,
  failureReason = '',
) {
  if (
    previewMode.value !== 'verify' ||
    !currentJobId.value ||
    !verificationSession.value ||
    reviewSavingId.value
  ) return
  reviewSavingId.value = record.id
  try {
    verificationSession.value = await updateVerificationSessionItem(
      currentJobId.value,
      verificationSession.value.id,
      record.id,
      status,
      failureCode,
      failureReason,
    )
    catalogDetailsOpen.value = false
    ElMessage.success(status === 'passed' ? t('home.reviewPassed') : t('home.reviewFailed'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('home.reviewUpdateFailed'))
  } finally {
    reviewSavingId.value = ''
  }
}

async function startVerification(session: VerificationSession) {
  if (!currentJobId.value) return
  try {
    const records = await getVerificationSessionRecords(currentJobId.value, session.id)
    verificationSession.value = session
    verificationAiRun.value = null
    verificationRecords.value = records
    previewMode.value = 'verify'
    clearPreviewSelection()
    activeTab.value = 'Data Preview'
    const first = records[0]
    if (first) await selectCatalogRecord(first)
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('verification.startFailed'))
  }
}

async function completeCurrentVerification() {
  if (!currentJobId.value || !verificationSession.value || verificationCompleting.value) return
  if (verificationRemaining.value > 0) {
    ElMessage.warning(t('home.verificationIncomplete', { count: verificationRemaining.value }))
    return
  }
  verificationCompleting.value = true
  try {
    const result = await completeVerificationSession(
      currentJobId.value,
      verificationSession.value.id,
    )
    verificationSession.value = result.session
    if (!result.version && result.ai_run) {
      verificationAiRun.value = result.ai_run
      ElMessage.info(t('verification.aiStarted'))
      let run = result.ai_run
      while (run.status === 'queued' || run.status === 'running') {
        await new Promise((resolve) => window.setTimeout(resolve, 800))
        run = await getAiVerificationRun(currentJobId.value, run.id)
        verificationAiRun.value = run
      }
      if (run.status === 'failed') {
        throw new Error(run.error || t('verification.aiFailed'))
      }
      verificationSession.value = await getVerificationSession(
        currentJobId.value,
        verificationSession.value.id,
      )
      if (!run.version_id) {
        const conflicts = verificationSession.value.items.filter(
          (item) => item.consensus_status === 'conflict' && !item.conflict_resolved,
        )
        ElMessage.warning(t('verification.aiConflicts', { count: conflicts.length }))
        const firstConflict = conflicts[0]
        if (firstConflict) {
          const record = verificationRecords.value.find(
            (item) => item.id === firstConflict.record_id,
          )
          if (record) await selectCatalogRecord(record)
        }
        return
      }
      ElMessage.success(
        t('verification.completed', { version: verificationSession.value.target_version }),
      )
    } else if (result.version) {
      ElMessage.success(t('verification.completed', { version: result.version.version }))
    }
    previewMode.value = 'browse'
    verificationSession.value = null
    verificationAiRun.value = null
    verificationRecords.value = []
    clearPreviewSelection()
    activeTab.value = 'Machine Verification'
    await nextTick()
    await machineVerificationRef.value?.refreshVersions()
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('home.reviewUpdateFailed'))
  } finally {
    verificationCompleting.value = false
  }
}

async function handleMatchingVersionApplied(matchingVersionId: string) {
  activeMatchingVersionId.value = matchingVersionId
  if (!currentJobId.value) return
  extractionRecords.value = await getExtractionRecords(currentJobId.value)
  clearPreviewSelection()
}

async function reviewRelation(
  relationId: string,
  status: 'accepted' | 'rejected',
) {
  if (!currentJobId.value || relationSavingId.value) return
  relationSavingId.value = relationId
  try {
    await updateRegionRelationReview(currentJobId.value, relationId, status)
    const selectedRecord = previewCatalogRecords.value.find(
      (record) => record.id === selectedRecordId.value,
    )
    if (selectedRecord && recordEvidenceContext.value) {
      await selectCatalogRecord(selectedRecord)
    } else if (previewSelectedPage.value !== null) {
      await loadPageAnnotations(previewSelectedPage.value)
    }
    ElMessage.success(
      status === 'accepted' ? t('home.relationAccepted') : t('home.relationRejected'),
    )
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('home.relationUpdateFailed'))
  } finally {
    relationSavingId.value = ''
  }
}

async function rebindRelation(payload: {
  relationId: string
  sourceRegionId: string
  targetRegionId: string
  relationType: RegionRelation['relation_type']
}) {
  if (!currentJobId.value || relationSavingId.value) return
  relationSavingId.value = payload.relationId
  try {
    await rebindRegionRelation(currentJobId.value, payload.relationId, {
      source_region_id: payload.sourceRegionId,
      target_region_id: payload.targetRegionId,
      relation_type: payload.relationType,
    })
    const selectedRecord = previewCatalogRecords.value.find(
      (record) => record.id === selectedRecordId.value,
    )
    if (selectedRecord && recordEvidenceContext.value) {
      await selectCatalogRecord(selectedRecord)
    } else if (previewSelectedPage.value !== null) {
      await loadPageAnnotations(previewSelectedPage.value)
    }
    ElMessage.success(t('home.relationRebound'))
  } catch (error: unknown) {
    ElMessage.error(error instanceof Error ? error.message : t('home.relationUpdateFailed'))
  } finally {
    relationSavingId.value = ''
  }
}

/** 页面进入左侧可视区域后生成低分辨率缩略图 */
async function renderThumbnail(pageNumber: number) {
  const item = pdfPages.value.find((page) => page.page === pageNumber)
  const documentVersion = pdfDocumentVersion
  const renderKey = `${documentVersion}:${pageNumber}`
  const document = pdfDocument.value
  if (!item || item.thumbnailUrl || renderingThumbnails.has(renderKey)) return

  renderingThumbnails.add(renderKey)
  item.loading = true

  try {
    const imageUrl = await renderPdfPage(pageNumber, 0.24, 0.72, document)
    if (documentVersion === pdfDocumentVersion && pdfPages.value.includes(item)) {
      item.thumbnailUrl = imageUrl
    }
  } catch {
    // 单页缩略图失败不影响文档其他页面的浏览
  } finally {
    item.loading = false
    renderingThumbnails.delete(renderKey)
  }
}

/** 读取本地 PDF，初始化页码列表并优先渲染第一页 */
async function handlePdfSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''

  if (!file) return

  const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
  if (!isPdf) {
    ElMessage.warning(t('home.pdfOnly'))
    return
  }

  const documentVersion = ++pdfDocumentVersion
  globalThis.localStorage.setItem(lastExtractionJobStorageKey, 'disabled')
  ++previewRequestId
  renderingThumbnails.clear()
  renderingEvidencePages.clear()
  evidencePreviewOrder.splice(0)
  evidencePagePreviewUrls.value = {}
  uploadLoading.value = true
  previewLoading.value = false
  previewUrl.value = ''
  pdfPages.value = []
  selectedExtractionPages.value = []
  extractionTaskPages.value = []
  pdfFileName.value = file.name
  serverDocumentId.value = ''
  currentJobId.value = ''
  extractionRecords.value = []
  activeMatchingVersionId.value = 'M0'
  previewMode.value = 'browse'
  verificationSession.value = null
  verificationRecords.value = []
  verificationCompleting.value = false
  pageAnnotationData.value = null
  recordEvidenceContext.value = null
  ++evidenceContextRequestId
  ++annotationRequestId
  previewSelectedPage.value = null
  activeAnnotationId.value = ''
  selectedRecordId.value = ''
  reviewSavingId.value = ''
  relationSavingId.value = ''
  progress.value = 0
  logs.value = []
  jobRunning.value = false
  stopped.value = false
  processingStartedAt.value = null
  processingEndedAt.value = null
  processedPages.value = 0
  totalProcessingPages.value = 0
  stopJobPolling()

  try {
    await pdfDocument.value?.destroy()
    const [fileData, uploadedDocument] = await Promise.all([
      file.arrayBuffer().then((buffer) => new Uint8Array(buffer)),
      uploadPdfDocument(file),
    ])
    const loadedDocument = await getDocument({ data: fileData }).promise
    if (documentVersion !== pdfDocumentVersion) {
      await loadedDocument.destroy()
      return
    }

    pdfDocument.value = loadedDocument
    serverDocumentId.value = uploadedDocument.document_id
    pdfPages.value = Array.from({ length: pdfDocument.value.numPages }, (_, index) => ({
      page: index + 1,
      thumbnailUrl: '',
      loading: false,
    }))
    selectedExtractionPages.value = getDefaultExtractionPages(pdfPages.value)
    activePage.value = 1
    await renderSelectedPage(1)
    ElMessage.success(t('home.loaded', { name: file.name, count: pdfDocument.value.numPages }))
  } catch (error: unknown) {
    if (documentVersion === pdfDocumentVersion) {
      pdfDocument.value = undefined
      pdfFileName.value = ''
      selectedExtractionPages.value = []
      ElMessage.error(error instanceof Error ? error.message : t('home.loadFailed'))
    }
  } finally {
    if (documentVersion === pdfDocumentVersion) {
      uploadLoading.value = false
    }
  }
}

// 离开页面时释放 PDF.js 占用的 Worker 和文档资源
async function restoreLatestExtractionResult() {
  if (currentJobId.value || extractionRecords.value.length) return

  try {
    const savedJobId = globalThis.localStorage.getItem(lastExtractionJobStorageKey)
    if (savedJobId === 'disabled') return
    const job = savedJobId
      ? await getExtractionJob(savedJobId)
      : await getLatestCompletedExtractionJob()
    if (!job) return

    currentJobId.value = job.id
    serverDocumentId.value = job.document_id
    extractionTaskPages.value = [
      ...(job.effective_pages?.length ? job.effective_pages : job.requested_pages ?? []),
    ]
    selectedExtractionPages.value = [...extractionTaskPages.value]
    globalThis.localStorage.setItem(lastExtractionJobStorageKey, job.id)
    applyJobState(job)

    if (job.status !== 'completed' && job.status !== 'completed_with_warnings') {
      if (jobRunning.value) void pollExtractionJob(job.id)
      return
    }

    const [records, images, documentInfo] = await Promise.all([
      getExtractionRecords(job.id),
      getDocumentImages(job.document_id),
      getUploadedDocument(job.document_id),
    ])
    const pageImages = new Map(
      images
        .filter((image) => image.image_type === 'page_render')
        .map((image) => [image.page_no, image]),
    )
    const pageNumbers = extractionTaskPages.value.length
      ? extractionTaskPages.value
      : [...new Set(records.flatMap((record) => record.source_pages))].sort((a, b) => a - b)

    pdfFileName.value = documentInfo.filename
    pdfPages.value = pageNumbers.map((page) => {
      const image = pageImages.get(page)
      return {
        page,
        thumbnailUrl: image
          ? getDocumentImageContentUrl(job.document_id, image.image_id)
          : '',
        loading: false,
      }
    })
    extractionRecords.value = records
    activePage.value = pageNumbers[0] ?? 1
    previewMode.value = 'browse'
    previewSelectedPage.value = null
    activeTab.value = 'Data Preview'
  } catch (error: unknown) {
    globalThis.localStorage.removeItem(lastExtractionJobStorageKey)
    ElMessage.warning(error instanceof Error ? error.message : t('home.progressFailed'))
  }
}

onMounted(() => {
  void restoreLatestExtractionResult()
})

onBeforeUnmount(() => {
  ++pdfDocumentVersion
  ++previewRequestId
  renderingThumbnails.clear()
  renderingEvidencePages.clear()
  evidencePreviewOrder.splice(0)
  evidencePagePreviewUrls.value = {}
  stopJobPolling()
  void pdfDocument.value?.destroy()
})
</script>

<template>
  <!-- 第一个工作台页面：复刻 Figma 中的数据抽取操作界面 -->
  <div class="workspace">
    <header class="app-header">
      <h1>ArchFact</h1>
    </header>

    <nav
      class="top-nav"
      :aria-label="t('nav.workflow')"
    >
      <div class="top-nav__tabs">
        <button
          v-for="tab in tabs"
          :key="tab"
          type="button"
          :class="{ 'top-nav__tab--active': activeTab === tab }"
          @click="changeTab(tab)"
        >
          {{ t(tabLabels[tab]) }}
        </button>
      </div>
      <div class="top-nav__actions">
        <LanguageToggle />
        <el-button
          v-if="activeTab === 'Data Extraction'"
          class="input-button"
          :icon="Upload"
          :loading="uploadLoading"
          plain
          @click="openPdfPicker"
        >
          {{ t('nav.inputPdf') }}
        </el-button>
        <el-button
          v-else-if="activeTab === 'Data Preview' && previewMode === 'verify' && verificationSession"
          class="done-button"
          plain
          :loading="verificationCompleting"
          :disabled="verificationRemaining > 0"
          @click="completeCurrentVerification"
        >
          <template v-if="verificationCompleting && verificationAiRun">
            {{ t('nav.aiReviewProgress', { percent: verificationAiRun.progress.percent }) }}
          </template>
          <template v-else>
            {{ t('nav.completeVerification') }}
          </template>
          <template v-if="verificationRemaining > 0">
            · {{ t('nav.verificationRemaining', { count: verificationRemaining }) }}
          </template>
        </el-button>
        <el-button
          v-else-if="activeTab === 'Machine Verification'"
          class="output-button"
          plain
          @click="exportVerificationResult"
        >
          {{ t('nav.output') }}
        </el-button>
      </div>
      <input
        ref="fileInputRef"
        class="pdf-input"
        type="file"
        accept=".pdf,application/pdf"
        @change="handlePdfSelected"
      >
    </nav>

    <main
      v-if="activeTab === 'Data Extraction'"
      class="workspace__content"
    >
      <PageNavigator
        :pages="pdfPages"
        :active-page="activePage"
        :total="pdfPages.length"
        :file-name="pdfFileName"
        @select="selectPdfPage"
        @thumbnail-needed="renderThumbnail"
      />

      <div class="workspace__center">
        <ProcessingPanel
          :progress="progress"
          :logs="logs"
          :stopped="stopped"
          :running="jobRunning"
          :started-at="processingStartedAt"
          :ended-at="processingEndedAt"
          :processed-pages="processedPages"
          :total-pages="totalProcessingPages"
          :failed-pages="failedJobPages"
          @clear="clearLogs"
          @export="exportLogs"
          @stop="toggleTask"
          @retry="retryFailedJobPages"
        />
        <ContentPreview
          :page="activePage"
          :preview-url="previewUrl"
          :file-name="pdfFileName"
          :loading="previewLoading"
        />
      </div>

      <ExtractionSettings
        v-model:selected-pages="selectedExtractionPages"
        :pages="pdfPages"
        @extract="startExtraction"
        @thumbnail-needed="renderThumbnail"
      />
    </main>

    <main
      v-else-if="activeTab === 'Data Preview'"
      class="preview-workspace"
      :class="{ 'preview-workspace--selected': previewSelectedPage !== null }"
    >
      <PageNavigator
        :pages="extractedPdfPages"
        :active-page="previewSelectedPage ?? 0"
        :total="pdfPages.length"
        :file-name="pdfFileName"
        @select="selectPreviewPage"
        @thumbnail-needed="renderThumbnail"
      />

      <PdfOverview
        v-if="previewSelectedPage !== null"
        :pages="extractedPdfPages"
        :active-page="previewSelectedPage"
        :total="pdfPages.length"
        @select="selectPreviewPage"
        @thumbnail-needed="renderThumbnail"
      />

      <div class="preview-workspace__center">
        <ContentPreview
          :page="previewSelectedPage ?? 0"
          :preview-url="previewSelectedPage === null ? '' : previewUrl"
          :file-name="pdfFileName"
          :loading="previewSelectedPage !== null && previewLoading"
          interactive
          :annotations="previewAnnotations"
          :related-annotations="evidenceContextAnnotations"
          :regions="previewRegions"
          :relations="previewRelations"
          :page-preview-urls="pagePreviewUrls"
          :active-annotation-id="activeAnnotationId"
          :relation-saving="Boolean(relationSavingId)"
          @select-annotation="selectPreviewAnnotation"
          @review-relation="reviewRelation"
          @rebind-relation="rebindRelation"
        />
        <RelatedPages
          :pages="extractedPdfPages"
          :active-page="previewSelectedPage"
          :annotations="evidenceContextAnnotations"
          :active-annotation-id="activeAnnotationId"
          @select="selectPreviewPage"
          @select-annotation="selectPreviewAnnotation"
          @thumbnail-needed="renderThumbnail"
        />
      </div>

      <ArchaeologicalCatalogs
        :records="previewCatalogRecords"
        :pages="extractedPdfPages"
        :selected-page="previewSelectedPage"
        :selected-record-id="selectedRecordId"
        :active-annotation-id="activeAnnotationId"
        :saving-review-id="reviewSavingId"
        :mode="previewMode"
        :job-id="currentJobId"
        :review-statuses="verificationReviewStatuses"
        :review-items="verificationReviewItems"
        :stale-items="verificationStaleItems"
        :matching-version-id="verificationSession?.matching_version_id ?? activeMatchingVersionId"
        :details-open="catalogDetailsOpen"
        :review-locked="verificationSession?.status === 'ai_review'"
        @select-record="selectCatalogRecord"
        @review="reviewRecord"
      />
    </main>

    <main
      v-else
      class="machine-workspace-page"
    >
      <MachineVerificationWorkspace
        ref="machineVerificationRef"
        :job-id="currentJobId"
        :document-id="serverDocumentId"
        :active-matching-version-id="activeMatchingVersionId"
        @start-verification="startVerification"
        @matching-version-applied="handleMatchingVersionApplied"
      />
    </main>
  </div>
</template>

<style scoped lang="scss">
.workspace {
  --af-heading: #37322e;
  --af-muted: #928b83;
  --af-border: #e0d8cf;
  --af-shadow: 0 2px 7px rgb(74 54 36 / 14%);

  min-width: 320px;
  height: 100vh;
  min-height: 100vh;
  overflow: hidden;
  font-size: var(--af-font-body);
  color: var(--af-heading);
  background:
    radial-gradient(circle at 50% 15%, rgb(255 255 255 / 78%), transparent 38%),
    #f8f1ea;
}

.app-header {
  display: grid;
  place-items: center;
  height: 47px;
  background: rgb(255 255 255 / 85%);
  border-bottom: 1px solid #eee8e2;
}

.app-header h1 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
  letter-spacing: 0.01em;
}

.top-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 55px;
  padding: 0 19px;
}

.top-nav__tabs {
  display: flex;
  gap: 27px;
  align-items: center;
}

.top-nav__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  min-width: max-content;
  min-height: 32px;
}

.top-nav__tabs button {
  min-width: 108px;
  height: 31px;
  padding: 0 14px;
  font-size: var(--af-font-body);
  color: #4f4a45;
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
}

.top-nav__tabs button:hover,
.top-nav__tab--active {
  background: rgb(255 255 255 / 72%) !important;
  border-color: #e5ded6 !important;
  box-shadow: 0 1px 4px rgb(70 51 36 / 8%);
}

.input-button {
  width: 126px;
  font-size: var(--af-font-body);
  color: #8c8379;
  background: rgb(255 255 255 / 75%);
  border-color: #e8e0d8;
}

.output-button,
.done-button {
  font-size: var(--af-font-body);
  color: #8d6d50;
  background: rgb(255 255 255 / 78%);
  border-color: #e4d9ce;
  box-shadow: 0 2px 5px rgb(86 59 36 / 8%);
}

.output-button { width: 126px; }

.done-button {
  width: auto;
  min-width: 126px;
  max-width: none;
  padding-inline: 14px;
  white-space: nowrap;
}

.done-button :deep(span) {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}

.pdf-input {
  position: fixed;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.workspace__content {
  display: grid;
  grid-template-columns: 150px minmax(420px, 1fr) minmax(335px, 360px);
  gap: 12px;
  height: calc(100vh - 113px);
  min-height: 0;
  padding: 0 19px 20px;
  overflow: hidden;
}

.workspace__center {
  display: grid;
  grid-template-rows: clamp(180px, 27vh, 245px) minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.preview-workspace {
  display: grid;
  grid-template-columns: 150px minmax(480px, 1fr) minmax(350px, 420px);
  gap: 12px;
  height: calc(100vh - 113px);
  min-height: 0;
  padding: 0 19px 20px;
  overflow: hidden;
}

.preview-workspace--selected {
  grid-template-columns: 150px 92px minmax(480px, 1fr) minmax(350px, 420px);
}

.preview-workspace__center {
  display: grid;
  grid-template-rows: minmax(350px, 1fr) minmax(175px, 210px);
  gap: 12px;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.machine-workspace-page {
  height: calc(100vh - 113px);
  min-height: 0;
  padding: 0 19px 20px;
  overflow: hidden;
}

@media (max-width: 1180px) {
  .preview-workspace {
    grid-template-columns: 130px minmax(420px, 1fr);
    grid-template-rows: minmax(620px, 1fr) 680px;
    height: calc(100vh - 113px);
    overflow: auto;
  }

  .preview-workspace--selected {
    grid-template-columns: 130px 82px minmax(420px, 1fr);
  }

  .preview-workspace > :last-child {
    grid-column: 1 / -1;
  }
}

@media (max-width: 980px) {
  .workspace__content {
    grid-template-columns: 112px minmax(0, 1fr);
    height: calc(100vh - 113px);
    overflow: auto;
  }

  .workspace__content > :last-child {
    grid-column: 1 / -1;
  }

  .machine-workspace-page {
    overflow: auto;
  }
}

@media (max-width: 700px) {
  .top-nav {
    align-items: flex-start;
    height: auto;
    padding-block: 10px;
  }

  .top-nav__tabs {
    flex-wrap: wrap;
    gap: 5px;
  }

  .top-nav__tabs button {
    min-width: auto;
  }

  .workspace__content {
    grid-template-columns: 1fr;
    height: calc(100vh - 120px);
    padding: 0 10px 14px;
    overflow: auto;
  }

  .workspace__content > :last-child {
    grid-column: auto;
  }

  .workspace__center {
    grid-template-rows: minmax(220px, auto) minmax(420px, auto);
    overflow: visible;
  }

  .top-nav__actions {
    flex: 0 0 auto;
  }

  .preview-workspace {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: 620px minmax(620px, auto);
    height: calc(100vh - 120px);
    padding: 0 10px 14px;
    overflow: auto;
  }

  .preview-workspace > :first-child {
    display: none;
  }

  .preview-workspace--selected {
    grid-template-columns: 90px minmax(0, 1fr);
  }

  .preview-workspace__center {
    grid-template-rows: minmax(420px, 1fr) 190px;
  }

  .preview-workspace > :last-child {
    grid-column: 1 / -1;
  }

  .machine-workspace-page {
    height: calc(100vh - 120px);
    padding: 0 10px 14px;
  }
}
</style>
