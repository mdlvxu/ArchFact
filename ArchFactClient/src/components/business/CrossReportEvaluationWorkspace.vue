<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Filter, Search } from '@element-plus/icons-vue'
import {
  createQualityEvaluation,
  controlQualityEvaluation,
  deleteGoldDataset,
  downloadQualityEvaluation,
  getCrossReportEvaluationHistory,
  getDatasetQualityEvaluations,
  getGoldDatasets,
  getQualityEvaluation,
  getQualityEvaluationItems,
  importGoldWorkbook,
  listUploadedDocuments,
  listRecentCompletedExtractionJobs,
  previewGoldWorkbook,
} from '@/api/modules/extraction'
import { localizeTemplateName, useI18n } from '@/i18n'
import { formatArtifactId } from '@/domain/artifact-display'
import { displayMatchingVersion, joinVisibleMetadata } from '@/domain/matching-version'
import { filterEvaluationHistory, isCurrentEvaluationJob, reportKey } from '@/domain/quality-evaluation-scope'
import { matchesQualityEvaluationItem } from '@/domain/quality-evaluation-search'
import type {
  EvaluationJobOption,
  CrossReportEvaluationItem,
  GoldDataset,
  GoldWorkbookPreview,
  QualityEvaluationHistoryItem,
  QualityEvaluationRun,
  QualityEvaluationItem,
} from '@/types/quality-evaluation'
import type { UploadedDocument } from '@/api/modules/extraction'

const props = defineProps<{
  active: boolean
  jobId: string
  documentId: string
  documentName: string
}>()

const { locale } = useI18n()
// Hidden workspaces stay mounted to preserve their state. Avoid subscribing this
// large view to locale changes until it is actually visible.
const renderLocale = computed(() => props.active ? locale.value : 'zh-CN')
const zh = computed(() => renderLocale.value === 'zh-CN')
const VERDICT_LABELS: Record<string, [string, string]> = {
  matched: ['一致', 'Matched'], mismatched: ['不一致', 'Mismatched'],
  missing: ['缺失', 'Missing'], extra: ['多提', 'Extra'],
  not_evaluated: ['未评测', 'Not evaluated'], not_applicable: ['不适用', 'Not applicable'],
}
const MATCH_STATUS_LABELS: Record<QualityEvaluationItem['match_status'], [string, string]> = {
  matched: ['已匹配', 'Matched'], not_found: ['未匹配', 'Not found'], ambiguous: ['编号歧义', 'Ambiguous ID'],
}
const FIELD_LABELS_EN: Record<string, string> = {
  artifact_id: 'Artifact ID', measurements: 'Measurements',
  morphological_description: 'Description', surface_color: 'Color',
  artifact_group: 'Artifact type 1', category: 'Category', type: 'Type',
  subtype: 'Subtype', texture: 'Material / texture', completeness: 'Completeness',
  figure_caption: 'Figure caption', stratigraphy: 'Stratigraphy', notes: 'Notes',
}
const FIELD_LABELS_ZH: Record<string, string> = {
  artifact_group: '器型1',
}
const jobs = ref<EvaluationJobOption[]>([])
const datasets = ref<GoldDataset[]>([])
const uploadedDocuments = ref<UploadedDocument[]>([])
const history = ref<QualityEvaluationHistoryItem[]>([])
const crossHistory = ref<CrossReportEvaluationItem[]>([])
const selectedJobId = ref('')
const selectedReportDocumentId = ref('')
const selectedDatasetId = ref('')
const selectedEvaluationId = ref('')
const selectedEvaluationJobId = ref('')
const run = ref<QualityEvaluationRun | null>(null)
const detailItems = shallowRef<QualityEvaluationItem[]>([])
const detailRows = computed(() => detailItems.value.map((item) => ({
  item,
  fields: item.field_results.filter((field) => field.verdict !== 'not_applicable'),
  sourcePages: item.source_pages.join(', '),
})))
const detailTotal = ref(0)
const detailSearchQuery = ref('')
const detailSearchField = ref('all')
const detailSearchLoading = ref(false)
let detailSearchTimer: ReturnType<typeof setTimeout> | undefined
let detailRequestToken = 0
let detailCache: { evaluationId: string; items: QualityEvaluationItem[] } | null = null
const resultTab = ref<'summary' | 'items' | 'logs'>('summary')
const exporting = ref(false)
const loading = ref(false)
const running = ref(false)
const controlling = ref(false)
const importOpen = ref(false)
const importing = ref(false)
const previewing = ref(false)
const importStage = ref<'uploading' | 'processing' | null>(null)
const uploadPercent = ref<number | null>(null)
const managerOpen = ref(false)
const managedDatasetId = ref('')
const managedEvaluations = ref<QualityEvaluationHistoryItem[] | null>(null)
const managementLoading = ref(false)
const deletingDataset = ref(false)
const preview = ref<GoldWorkbookPreview | null>(null)
const selectedFile = ref<File | null>(null)
const inputRef = ref<HTMLInputElement>()
const datasetName = ref('')
const datasetVersion = ref('1.0')
const keyMode = ref<'artifact_id' | 'context_sequence'>('context_sequence')
const fieldMapping = ref<Record<string, string>>({})
const showAllHistory = ref(false)
const historyQuery = ref('')
const historyStatus = ref('all')
const historyPage = ref(1)
const baselineDetailsOpen = ref(false)
const historyPageSize = 4
let pollTimer: number | undefined
let loadingToken = 0
let evaluationToken = 0

const strings = computed(() => zh.value ? {
  title: 'Ground Truth 评测', subtitle: '导入人工标注数据作为基准，评估对应报告的不同提取任务。',
  report: '考古报告', reportImportHint: '人工基准绑定到所选报告；无需先提取。开始评测时再选择对应的提取任务。', noReport: '请先上传要导入基准的 PDF 报告；无需先完成提取。', job: '提取任务', dataset: '人工标注基准',
  selectJob: '选择已完成的提取任务', selectDataset: '选择此报告的评测基准', upload: '导入 Excel 基准',
  noDatasets: '尚未导入此报告的人工标注数据。', noJobs: '当前报告还没有已完成的提取任务。可先导入基准，提取完成后再评测。',
  run: '开始评测', running: '评测中…', loading: '正在加载评测数据…', compare: '同一基准下的历史评测',
  result: '本次结果', empty: '选择提取任务和人工标注基准后即可开始。',
  idPrecision: '编号精确率', idRecall: '编号召回率', fieldScore: '字段一致度', ocrScore: 'OCR 锚点命中',
  detection: '图像检测 F1', relation: '图文关系', predicted: '最终器物卡片', matched: '成功匹配', ambiguous: '主键歧义', unmatchedHuman: '未关联人工行',
  lowFields: '字段对照', missing: '漏提', mismatch: '不一致', exact: '严格一致', notAvailable: '未提供对应标注，暂不可评测',
  partial: '当前提取仅覆盖部分页面；整本报告召回率不计算。', unsupported: '文本表不含图像框和图文关联真值，因此检测与关联指标显示为不可评测。',
  uploadTitle: '导入人工标注 Excel', name: '基准名称', version: '基准版本', keyMode: '器物主键方式',
  composite: '遗迹号 + 序号', single: '完整器物编号', columns: 'Excel 字段映射', key: '器物编号', context: '遗迹号', sequence: '序号',
  confirmImport: '确认导入', cancel: '取消', chooseFile: '选择 XLSX 文件', sheet: '工作表',
  mapHint: '请先选择主键列；其余未标注字段留空即可，不会被当成错误。', pleaseMapKey: '请完成主键字段映射。',
  importSuccess: '评测基准已导入', importFailed: '基准导入失败', evaluationFailed: '评测启动失败',
  template: '模板', matching: '关系版本', date: '评测时间', status: '状态',
  statusCompleted: '已完成', statusRunning: '进行中', statusFailed: '失败', statusQueued: '排队中', statusPaused: '已暂停', statusTerminated: '已终止',
  pause: '暂停', resume: '继续', terminate: '终止', pausedHint: '评测已暂停；已发出的模型请求可能先完成。', terminatedHint: '评测已终止，可以重新开始。', controlFailed: '操作失败',
  reportLabel: '报告', pageScope: '页码范围', missingRecords: '未匹配机器记录',
  invalidFile: '请选择 XLSX 格式的 Excel 文件。',
  alreadyImported: '该报告已导入人工标注基准，可直接复用。',
  selectReportFirst: '请先选择要导入基准的考古报告。',
  historicalReadOnly: '历史报告只可查看和导出已完成的评测；请切回当前报告运行新评测。',
  noActiveJob: '当前报告尚无已完成的提取任务。',
  exportDetail: '导出左外关联报告', exportImages: '导出文本与图片表',
  items: '逐条对照', logs: '评测日志', overview: '总览', machine: '机器', human: '人工',
  searchRecords: '搜索记录…', searchField: '选择搜索字段', allFields: '全部字段', sourcePages: '来源页码',
  searchNoResults: '没有找到符合条件的记录，请调整字段或关键词。', searchingRecords: '正在搜索记录…',
  noResults: '此任务尚无已完成的评测结果。',
} : {
  title: 'Ground Truth Evaluation', subtitle: 'Use human annotations as ground truth to evaluate extraction runs for the corresponding report.',
  report: 'Archaeological report', reportImportHint: 'The baseline is linked to this report; extraction is not required until you are ready to evaluate.', noReport: 'Upload the PDF to register the report; extraction is not required before importing a baseline.', job: 'Extraction run', dataset: 'Human-annotation baseline',
  selectJob: 'Select a completed extraction run', selectDataset: 'Select a baseline for this report', upload: 'Import Excel baseline',
  noDatasets: 'No human-annotation baseline has been imported for this report.', noJobs: 'No completed extraction runs for this report. You can import its baseline now and evaluate after extraction.',
  run: 'Run evaluation', running: 'Evaluating…', loading: 'Loading evaluation data…', compare: 'Evaluation history on this baseline',
  result: 'Current result', empty: 'Select an extraction run and a baseline to begin.',
  idPrecision: 'ID precision', idRecall: 'ID recall', fieldScore: 'Field agreement', ocrScore: 'OCR anchor hit rate',
  detection: 'Detection F1', relation: 'Text-image links', predicted: 'Final artifact cards', matched: 'Matched', ambiguous: 'Ambiguous keys', unmatchedHuman: 'Unmatched human rows',
  lowFields: 'Field comparison', missing: 'Missing', mismatch: 'Mismatched', exact: 'Exact', notAvailable: 'No corresponding annotations; not evaluated',
  partial: 'Only part of the document was extracted; whole-report recall is unavailable.',
  unsupported: 'This spreadsheet has no image boxes or relation labels, so detection and relation metrics are unavailable.',
  uploadTitle: 'Import human annotations', name: 'Baseline name', version: 'Baseline version', keyMode: 'Artifact key',
  composite: 'Context ID + sequence', single: 'Complete artifact ID', columns: 'Excel column mapping', key: 'Artifact ID', context: 'Context ID', sequence: 'Sequence',
  confirmImport: 'Import baseline', cancel: 'Cancel', chooseFile: 'Choose XLSX file', sheet: 'Worksheet',
  mapHint: 'Map the key columns first. Leave unannotated fields empty; they will not be scored as errors.', pleaseMapKey: 'Map the artifact key columns first.',
  importSuccess: 'Baseline imported', importFailed: 'Could not import baseline', evaluationFailed: 'Could not start evaluation',
  template: 'Template', matching: 'Matching version', date: 'Evaluated', status: 'Status',
  statusCompleted: 'Completed', statusRunning: 'Running', statusFailed: 'Failed', statusQueued: 'Queued', statusPaused: 'Paused', statusTerminated: 'Terminated',
  pause: 'Pause', resume: 'Resume', terminate: 'Terminate', pausedHint: 'Evaluation paused. Requests already sent may finish.', terminatedHint: 'Evaluation terminated. You can start a new run.', controlFailed: 'Action failed',
  reportLabel: 'Report', pageScope: 'Pages', missingRecords: 'Unmatched machine records',
  invalidFile: 'Please select an .xlsx workbook.',
  alreadyImported: 'A human baseline already exists for this report and can be reused.',
  selectReportFirst: 'Select the archaeological report for this baseline first.',
  historicalReadOnly: 'Historical reports are read-only. Switch to the current report to run a new evaluation.',
  noActiveJob: 'No completed extraction run exists for the current report.',
  exportDetail: 'Export left-join report', exportImages: 'Export text-image table',
  items: 'Record comparison', logs: 'Evaluation log', overview: 'Overview', machine: 'Machine', human: 'Human',
  searchRecords: 'Search records…', searchField: 'Search field', allFields: 'All fields', sourcePages: 'Source pages',
  searchNoResults: 'No records match. Try another field or keyword.', searchingRecords: 'Searching records…',
  noResults: 'No completed evaluation is available for this run.',
})

const fields = computed(() => [
  { key: 'artifact_id', label: strings.value.key },
  ...(keyMode.value === 'context_sequence' ? [
    { key: 'context_id', label: strings.value.context },
    { key: 'sequence_no', label: strings.value.sequence },
  ] : []),
  { key: 'measurements', label: zh.value ? '尺寸' : 'Measurements' },
  { key: 'morphological_description', label: zh.value ? '外形描述' : 'Description' },
  { key: 'surface_color', label: zh.value ? '颜色' : 'Color' },
  { key: 'artifact_group', label: zh.value ? '器型1' : 'Artifact type 1' },
  { key: 'category', label: zh.value ? '器型 2 / 类别' : 'Category' },
  { key: 'type', label: zh.value ? '型别' : 'Type' },
  { key: 'subtype', label: zh.value ? '式别' : 'Subtype' },
  { key: 'texture', label: zh.value ? '材质 / 质地' : 'Material / texture' },
  { key: 'completeness', label: zh.value ? '完整程度' : 'Completeness' },
  { key: 'figure_caption', label: zh.value ? '图注' : 'Figure caption' },
  { key: 'stratigraphy', label: zh.value ? '地层' : 'Stratigraphy' },
  { key: 'notes', label: zh.value ? '备注' : 'Notes' },
])
const detailSearchFields = computed(() => {
  const available = new Set((run.value?.field_metrics || []).filter((metric) => metric.evaluated > 0).map((metric) => metric.key))
  return [
    { key: 'all', label: strings.value.allFields },
    ...fields.value.filter((field) => available.has(field.key)),
    { key: 'source_pages', label: strings.value.sourcePages },
  ]
})

const currentDataset = computed(() => datasets.value.find((item) => item.id === selectedDatasetId.value))
const managedDataset = computed(() => datasets.value.find((item) => item.id === managedDatasetId.value))
const selectedJob = computed(() => jobs.value.find((item) => item.job_id === selectedJobId.value))
const selectedReportDocument = computed(() => {
  const uploaded = uploadedDocuments.value.find((item) => item.document_id === selectedReportDocumentId.value)
  if (uploaded) return uploaded
  const dataset = datasets.value.find((item) => item.document_id === selectedReportDocumentId.value)
  if (dataset) return { document_id: dataset.document_id, filename: dataset.document_filename, size: 0, status: 'uploaded' }
  return props.documentId && (!selectedReportDocumentId.value || selectedReportDocumentId.value === props.documentId)
    ? { document_id: props.documentId, filename: props.documentName, size: 0, status: 'uploaded' }
    : undefined
})
const visibleDatasets = computed(() => datasets.value.filter((item) =>
  reportKey(item.document_filename || '') === reportKey(selectedReportDocument.value?.filename || '') &&
  Boolean(selectedReportDocument.value?.filename),
))
const isCurrentJob = computed(() => isCurrentEvaluationJob(selectedJob.value?.document_id, props.documentId))
const canImport = computed(() => Boolean(selectedReportDocument.value?.document_id && !visibleDatasets.value.length))
const canEvaluate = computed(() => isCurrentJob.value && Boolean(selectedDatasetId.value) && !runningState.value)
const reportOptions = computed(() => {
  const documents = [...uploadedDocuments.value]
  if (props.documentId && !documents.some((item) => item.document_id === props.documentId)) {
    documents.unshift({ document_id: props.documentId, filename: props.documentName, size: 0, status: 'uploaded' })
  }
  return documents.filter((item) => item.filename).map((item) => ({ id: item.document_id, key: reportKey(item.filename), name: item.filename }))
})
const selectedReport = computed(() => reportKey(selectedReportDocument.value?.filename || selectedJob.value?.document_filename || ''))
const reportJobs = computed(() => jobs.value.filter((job) => reportKey(job.document_filename) === selectedReport.value))
const filteredJobs = computed(() => filterEvaluationHistory(jobs.value, {
  report: selectedReport.value,
  allReports: showAllHistory.value,
  query: historyQuery.value,
  status: historyStatus.value,
  statusOf: (job) => jobEvaluation(job.job_id)?.status || 'pending',
  searchText: (job) => `${jobOptionLabel(job)} ${job.template_name} ${job.job_id}`,
}))
const historyPages = computed(() => Math.max(1, Math.ceil(filteredJobs.value.length / historyPageSize)))
const visibleJobs = computed(() => filteredJobs.value.slice(
  (historyPage.value - 1) * historyPageSize, historyPage.value * historyPageSize,
))

function jobEvaluation(jobId: string) {
  const current = history.value.find((item) => item.job_id === jobId)
  if (run.value?.job_id === jobId && (!current || run.value.id === current.id)) return run.value
  return current ? { ...current, dataset_id: selectedDatasetId.value } : crossHistory.value.find((item) => item.job_id === jobId)
}

function selectJob(job: EvaluationJobOption) {
  selectedReportDocumentId.value = job.document_id
  const evaluation = jobEvaluation(job.job_id)
  if (evaluation) openHistorical(evaluation)
  else selectedJobId.value = job.job_id
}

function stopPolling() {
  if (pollTimer !== undefined) globalThis.clearTimeout(pollTimer)
  pollTimer = undefined
  evaluationToken += 1
}

function formatScore(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(renderLocale.value)
}

function templateName(value: string, fallback: string) {
  if (!value) return fallback
  // Do not let this helper subscribe a hidden workspace to the global locale.
  return props.active ? localizeTemplateName(value) : value
}

function jobOptionLabel(job: EvaluationJobOption) {
  return joinVisibleMetadata(
    templateName(job.template_name, job.job_id.slice(-10)),
    job.document_filename,
    displayMatchingVersion(job.matching_version_id),
    formatDate(job.created_at),
  )
}

function jobSelectionLabel(job: EvaluationJobOption) {
  return joinVisibleMetadata(
    templateName(job.template_name, job.job_id.slice(-10)),
    displayMatchingVersion(job.matching_version_id),
    formatDate(job.created_at),
  )
}

function jobHistoryLabel(job: EvaluationJobOption) {
  return joinVisibleMetadata(
    job.document_filename,
    displayMatchingVersion(job.matching_version_id),
    formatDate(job.created_at),
  )
}

function evaluationRunLabel(run: QualityEvaluationRun) {
  return joinVisibleMetadata(
    templateName(run.template_name, run.job_id),
    displayMatchingVersion(run.matching_version_id),
    run.semantic_model,
  )
}

function verdictLabel(verdict: string) {
  return VERDICT_LABELS[verdict]?.[zh.value ? 0 : 1] ?? verdict
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    completed: strings.value.statusCompleted,
    running: strings.value.statusRunning,
    failed: strings.value.statusFailed,
    queued: strings.value.statusQueued,
    paused: strings.value.statusPaused,
    terminated: strings.value.statusTerminated,
  }
  return labels[status] ?? status
}

function matchStatusLabel(status: QualityEvaluationItem['match_status']) {
  return MATCH_STATUS_LABELS[status][zh.value ? 0 : 1]
}

function fieldLabel(key: string, fallback: string) {
  if (zh.value) return FIELD_LABELS_ZH[key] || fallback
  return FIELD_LABELS_EN[key] || fallback
}

function displayFieldValue(key: string, value: unknown) {
  if (value == null) return '—'
  return key === 'artifact_id' && typeof value === 'string'
    ? formatArtifactId(value)
    : value
}

function baselineFieldLabel(key: string) {
  if (key === 'context_id') return strings.value.context
  if (key === 'sequence_no') return strings.value.sequence
  const labels: Record<string, string> = {
    artifact_id: '器物编号', measurements: '尺寸', morphological_description: '外形描述',
    surface_color: '颜色', artifact_group: '器型1', category: '器型 2 / 类别', type: '型别',
    subtype: '式别', texture: '材质 / 质地', completeness: '完整程度', figure_caption: '图注',
    stratigraphy: '地层', notes: '备注',
  }
  return fieldLabel(key, labels[key] || key)
}

function logMessage(entry: { stage: string; message: string }) {
  if (zh.value) return entry.message
  const counts = entry.message.match(/\d+/g)
  if (entry.stage === 'matching_records' && counts?.length === 2) {
    return `Loaded ${counts[0]} human rows and ${counts[1]} final fused cards`
  }
  if (entry.stage === 'semantic_completed' && counts?.length === 1) {
    return `Compared ${counts[0]} free-text field pairs with the semantic model`
  }
  const labels: Record<string, string> = {
    queued: 'Evaluation queued', running: 'Loading baseline and fused cards',
    matching_records: 'Matching human IDs to final fused cards',
    semantic_comparison: 'Comparing free-text semantics',
    saving: 'Saving evaluation details', completed: 'Results saved',
    paused: 'Evaluation paused; requests already sent may finish',
    terminated: 'Evaluation terminated; a new run can be started',
  }
  return labels[entry.stage] || entry.message
}

function warningMessage(message: string) {
  if (zh.value) return message
  if (message.startsWith('当前任务只覆盖部分页面')) return 'This run covers only part of the PDF; whole-report recall is not calculated.'
  if (message.startsWith('当前文档没有可用的 OCR 文本')) return 'No OCR text is available for anchor evaluation.'
  if (message.startsWith('人工标注数据没有逐字整页转写')) return 'OCR scores measure anchor hits, not character error rate.'
  return message
}

async function loadWorkspace() {
  const token = ++loadingToken
  stopPolling()
  run.value = null
  history.value = []
  loading.value = true
  try {
    const [jobItems, datasetItems, evaluationItems, documentItems] = await Promise.all([
      listRecentCompletedExtractionJobs(),
      getGoldDatasets(),
      getCrossReportEvaluationHistory(),
      listUploadedDocuments().catch(() => []),
    ])
    if (token !== loadingToken) return
    jobs.value = jobItems.filter((job) => ['completed', 'completed_with_warnings'].includes(job.status))
    datasets.value = datasetItems
    uploadedDocuments.value = documentItems
    crossHistory.value = evaluationItems
    selectedReportDocumentId.value = documentItems.some((document) => document.document_id === props.documentId)
      ? props.documentId
      : documentItems.find((document) => reportKey(document.filename) === reportKey(props.documentName))?.document_id
        ?? props.documentId
    selectedJobId.value = jobs.value.some((job) => job.job_id === props.jobId && job.document_id === props.documentId)
      ? props.jobId : jobs.value.find((job) => job.document_id === props.documentId)?.job_id ?? ''
    selectedDatasetId.value = visibleDatasets.value[0]?.id ?? ''
    await loadHistory()
  } catch (error) {
    if (token === loadingToken) ElMessage.error(error instanceof Error ? error.message : strings.value.loading)
  } finally {
    if (token === loadingToken) loading.value = false
  }
}

async function loadHistory() {
  stopPolling()
  history.value = []
  run.value = null
  detailItems.value = []
  detailCache = null
  detailRequestToken += 1
  detailSearchLoading.value = false
  detailSearchQuery.value = ''
  detailSearchField.value = 'all'
  detailTotal.value = 0
  if (!selectedDatasetId.value) return
  try {
    history.value = await getDatasetQualityEvaluations(selectedDatasetId.value)
    const latestForJob = history.value.find((item) => item.id === selectedEvaluationId.value && item.job_id === selectedJobId.value)
      ?? history.value.find((item) => item.job_id === selectedJobId.value)
    selectedEvaluationId.value = latestForJob?.id ?? ''
    selectedEvaluationJobId.value = latestForJob?.job_id ?? ''
    if (latestForJob) await loadEvaluation(latestForJob.job_id, latestForJob.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : strings.value.loading)
  }
}

async function loadEvaluation(jobId: string, evaluationId: string) {
  const token = ++evaluationToken
  try {
    const result = await getQualityEvaluation(jobId, evaluationId)
    if (token !== evaluationToken) return
    if (run.value?.id !== result.id) {
      detailCache = null
      detailSearchQuery.value = ''
      detailSearchField.value = 'all'
      detailTotal.value = 0
      detailItems.value = []
      detailSearchLoading.value = false
    }
    run.value = result
    selectedEvaluationJobId.value = jobId
    if (['queued', 'running', 'paused'].includes(result.status)) {
      pollTimer = globalThis.setTimeout(() => void loadEvaluation(jobId, evaluationId), 1000)
    } else if (result.status === 'completed') {
      detailTotal.value = result.summary.gold_records
      if (resultTab.value === 'items') await loadDetailItems()
    }
  } catch (error) {
    if (token === evaluationToken) ElMessage.error(error instanceof Error ? error.message : strings.value.loading)
  }
}

async function startEvaluation() {
  if (!selectedJobId.value || !canEvaluate.value || running.value) return
  running.value = true
  try {
    const created = await createQualityEvaluation(selectedJobId.value, selectedDatasetId.value)
    selectedEvaluationId.value = created.id
    selectedEvaluationJobId.value = created.job_id
    run.value = created
    await loadHistory()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : strings.value.evaluationFailed)
  } finally {
    running.value = false
  }
}

async function controlEvaluation(action: 'pause' | 'resume' | 'terminate') {
  if (!run.value || controlling.value) return
  const jobId = run.value.job_id
  const evaluationId = run.value.id
  controlling.value = true
  stopPolling()
  evaluationToken += 1
  try {
    run.value = await controlQualityEvaluation(jobId, evaluationId, action)
    ElMessage.info(action === 'pause' ? strings.value.pausedHint : action === 'terminate' ? strings.value.terminatedHint : strings.value.statusRunning)
    await loadHistory()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : strings.value.controlFailed)
    await loadEvaluation(jobId, evaluationId)
  } finally {
    controlling.value = false
  }
}

async function loadDetailItems() {
  const currentRun = run.value
  if (!currentRun || currentRun.status !== 'completed') return
  const token = ++detailRequestToken
  detailSearchLoading.value = true
  try {
    let allItems = detailCache?.evaluationId === currentRun.id ? detailCache.items : null
    if (!allItems) {
      const pageSize = 200
      const firstPage = await getQualityEvaluationItems(currentRun.job_id, currentRun.id, 1, pageSize)
      const pageCount = Math.ceil(firstPage.total / pageSize)
      const remainingItems: QualityEvaluationItem[] = []
      for (let firstPageNo = 2; firstPageNo <= pageCount; firstPageNo += 4) {
        if (token !== detailRequestToken) return
        const pages = await Promise.all(
          Array.from({ length: Math.min(4, pageCount - firstPageNo + 1) }, (_, index) =>
            getQualityEvaluationItems(currentRun.job_id, currentRun.id, firstPageNo + index, pageSize),
          ),
        )
        remainingItems.push(...pages.flatMap((result) => result.items))
      }
      allItems = [...firstPage.items, ...remainingItems]
      detailCache = { evaluationId: currentRun.id, items: allItems }
    }
    if (token !== detailRequestToken) return
    const query = detailSearchQuery.value.trim()
    if (query) {
      detailItems.value = allItems.filter((item) =>
        matchesQualityEvaluationItem(item, query, detailSearchField.value),
      )
      detailTotal.value = detailItems.value.length
    } else {
      detailItems.value = allItems
      detailTotal.value = allItems.length
    }
  } catch (error) {
    if (token === detailRequestToken) {
      ElMessage.error(error instanceof Error ? error.message : strings.value.loading)
    }
  } finally {
    if (token === detailRequestToken) detailSearchLoading.value = false
  }
}

function scheduleDetailSearch() {
  if (detailSearchTimer !== undefined) globalThis.clearTimeout(detailSearchTimer)
  detailSearchTimer = globalThis.setTimeout(() => {
    detailSearchTimer = undefined
    void loadDetailItems()
  }, 250)
}

function changeDetailSearchField() {
  if (detailSearchTimer !== undefined) {
    globalThis.clearTimeout(detailSearchTimer)
    detailSearchTimer = undefined
  }
  void loadDetailItems()
}

function openDetailTab() {
  resultTab.value = 'items'
  if (run.value && detailCache?.evaluationId !== run.value.id && !detailSearchLoading.value) void loadDetailItems()
}

function openHistorical(item: CrossReportEvaluationItem) {
  let job = jobs.value.find((candidate) => candidate.job_id === item.job_id)
  if (!job) {
    const dataset = datasets.value.find((candidate) => candidate.id === item.dataset_id)
    job = {
      job_id: item.job_id,
      document_id: item.document_id,
      document_filename: dataset?.document_filename || '',
      status: 'completed',
      created_at: item.created_at,
      completed_at: item.created_at,
      template_name: item.template_name,
      matching_version_id: item.matching_version_id,
    }
    jobs.value.push(job)
  }
  selectedReportDocumentId.value = job.document_id
  selectedJobId.value = job.job_id
  selectedDatasetId.value = item.dataset_id
  selectedEvaluationId.value = item.id
}

async function exportRun(kind: 'detail' | 'text_image') {
  if (!displayRun.value || displayRun.value.status !== 'completed' || exporting.value) return
  exporting.value = true
  try {
    const blob = await downloadQualityEvaluation(displayRun.value.job_id, displayRun.value.id, kind)
    const url = globalThis.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `ArchFact_${displayRun.value.id}_${kind}.xlsx`
    link.click()
    globalThis.setTimeout(() => globalThis.URL.revokeObjectURL(url), 1000)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : strings.value.evaluationFailed)
  } finally {
    exporting.value = false
  }
}

function chooseWorkbook() {
  if (!canImport.value) return
  if (visibleDatasets.value.length) {
    ElMessage.info(strings.value.alreadyImported)
    return
  }
  inputRef.value?.click()
}

async function handleWorkbookSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  ;(event.target as HTMLInputElement).value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    ElMessage.warning(strings.value.invalidFile)
    return
  }
  previewing.value = true
  try {
    const workbookPreview = await previewGoldWorkbook(file)
    selectedFile.value = file
    preview.value = workbookPreview
    datasetName.value = (selectedReportDocument.value?.filename || props.documentName).replace(/\.pdf$/i, '')
    datasetVersion.value = '1.0'
    keyMode.value = workbookPreview.headers.some((header) => /^(器物编号|器物号|artifact\s*id|sf\s*no\.?|cat\s*no\.?)$/i.test(header.trim()))
      ? 'artifact_id' : 'context_sequence'
    fieldMapping.value = {}
    const aliases: Record<string, RegExp> = {
      artifact_id: /^(器物编号|器物号|编号|artifact\s*id|sf\s*no\.?|cat\s*no\.?)$/i,
      context_id: /^(遗迹号|墓号|context\s*id|feature\s*no\.?)$/i,
      sequence_no: /^(序号|器物序号|sequence|item\s*no\.?)$/i,
      measurements: /^(尺寸|measurements?)$/i,
      morphological_description: /^(外形描述|形态描述|description|morphology)$/i,
      surface_color: /^(颜色|表面颜色|colour|color)$/i,
      artifact_group: /^(器型1|器型\s*1|artifact\s*(?:type\s*1|group))$/i,
      category: /^(器型2|器型\s*2|类别|category|type)$/i,
      type: /^(型别|form)$/i,
      subtype: /^(式别|style)$/i,
      texture: /^(材质|质地|material|texture)$/i,
      completeness: /^(完整程度|完整度|completeness|condition)$/i,
      figure_caption: /^(图注|图版|figure\s*caption|caption)$/i,
      stratigraphy: /^(地层|stratigraphy)$/i,
      notes: /^(备注|notes?)$/i,
    }
    for (const [key, pattern] of Object.entries(aliases)) {
      const header = workbookPreview.headers.find((item) => pattern.test(item.trim()))
      if (header) fieldMapping.value[key] = header
    }
    importOpen.value = true
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : strings.value.importFailed)
  } finally {
    previewing.value = false
  }
}

async function saveDataset() {
  const targetDocument = selectedReportDocument.value
  if (!selectedFile.value || !targetDocument?.document_id || !canImport.value) return
  const hasKey = keyMode.value === 'artifact_id'
    ? Boolean(fieldMapping.value.artifact_id)
    : Boolean(fieldMapping.value.context_id && fieldMapping.value.sequence_no)
  if (!hasKey) {
    ElMessage.warning(strings.value.pleaseMapKey)
    return
  }
  importing.value = true
  importStage.value = 'uploading'
  uploadPercent.value = null
  try {
    const imported = await importGoldWorkbook({
      documentId: targetDocument.document_id,
      name: datasetName.value.trim() || targetDocument.filename.replace(/\.pdf$/i, ''),
      version: datasetVersion.value.trim() || '1.0',
      keyMode: keyMode.value,
      fieldMapping: fieldMapping.value,
      file: selectedFile.value,
      onUploadProgress: (loaded, total) => {
        if (total && total > 0) {
          uploadPercent.value = Math.min(100, Math.round(loaded * 100 / total))
          if (loaded >= total) importStage.value = 'processing'
        }
      },
    })
    datasets.value.unshift(imported)
    selectedDatasetId.value = imported.id
    importOpen.value = false
    ElMessage.success(`${strings.value.importSuccess} · ${imported.record_count} ${zh.value ? '条记录' : 'records'}`)
    await loadHistory()
    try {
      crossHistory.value = await getCrossReportEvaluationHistory()
    } catch {
      ElMessage.warning(zh.value ? '基准已导入，但历史列表刷新失败，请稍后刷新页面。' : 'Baseline imported, but history could not be refreshed.')
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : strings.value.importFailed)
  } finally {
    importing.value = false
    importStage.value = null
  }
}

let managementToken = 0
async function inspectDataset(datasetId: string) {
  const token = ++managementToken
  managedDatasetId.value = datasetId
  managedEvaluations.value = null
  managementLoading.value = true
  try {
    const evaluations = await getDatasetQualityEvaluations(datasetId)
    if (token === managementToken) managedEvaluations.value = evaluations
  } catch (error) {
    if (token === managementToken) ElMessage.error(error instanceof Error ? error.message : strings.value.loading)
  } finally {
    if (token === managementToken) managementLoading.value = false
  }
}

async function openManager() {
  managerOpen.value = true
  managedEvaluations.value = null
  managementLoading.value = true
  try {
    datasets.value = await getGoldDatasets()
    const id = datasets.value.some((item) => item.id === selectedDatasetId.value)
      ? selectedDatasetId.value : datasets.value[0]?.id
    if (id) await inspectDataset(id)
    else { managedDatasetId.value = ''; managedEvaluations.value = null; managementLoading.value = false }
  } catch (error) {
    managementLoading.value = false
    ElMessage.error(error instanceof Error ? error.message : strings.value.loading)
  }
}

async function removeManagedDataset() {
  const dataset = managedDataset.value
  if (!dataset || managedEvaluations.value === null || managedEvaluations.value.length || deletingDataset.value) return
  try {
    await ElMessageBox.confirm(
      zh.value ? `确定删除“${dataset.name}”？该基准的人工记录将被删除，原始 Excel、PDF 和提取结果不受影响。` : `Delete “${dataset.name}” and its annotation records? The original Excel, PDF and extraction data will not be affected.`,
      zh.value ? '删除人工基准' : 'Delete baseline',
      { confirmButtonText: zh.value ? '确认删除' : 'Delete', cancelButtonText: strings.value.cancel, type: 'warning' },
    )
  } catch { return }
  deletingDataset.value = true
  try {
    await deleteGoldDataset(dataset.id)
    datasets.value = datasets.value.filter((item) => item.id !== dataset.id)
    if (selectedDatasetId.value === dataset.id) selectedDatasetId.value = ''
    ElMessage.success(zh.value ? '人工基准已删除。' : 'Baseline deleted.')
    const next = datasets.value[0]?.id
    if (next) await inspectDataset(next)
    else { managedDatasetId.value = ''; managedEvaluations.value = null }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : (zh.value ? '删除失败' : 'Deletion failed'))
  } finally {
    deletingDataset.value = false
  }
}

const runningState = computed(() => Boolean(run.value && ['queued', 'running', 'paused'].includes(run.value.status)))
const displayRun = computed(() => {
  if (run.value?.id === selectedEvaluationId.value) return run.value
  return null
})

watch(() => [props.jobId, props.documentId, props.documentName], () => void loadWorkspace(), { immediate: true })
watch([historyQuery, historyStatus, showAllHistory, selectedReport], () => { historyPage.value = 1 })
watch(historyPages, (count) => { if (historyPage.value > count) historyPage.value = count })
watch(selectedDatasetId, () => void loadHistory())
watch(selectedReportDocumentId, (documentId) => {
  const report = reportOptions.value.find((item) => item.id === documentId)
  if (!report) return
  const nextJob = jobs.value.find((item) => item.document_id === documentId)
  selectedJobId.value = nextJob?.job_id ?? ''
  selectedDatasetId.value = datasets.value.find((item) => reportKey(item.document_filename || '') === report.key)?.id ?? ''
}, { flush: 'sync' })
watch(selectedJobId, () => {
  if (!visibleDatasets.value.some((dataset) => dataset.id === selectedDatasetId.value)) {
    selectedDatasetId.value = visibleDatasets.value[0]?.id ?? ''
  }
})
watch(selectedJobId, () => {
  const latest = history.value.find((item) => item.id === selectedEvaluationId.value && item.job_id === selectedJobId.value)
    ?? history.value.find((item) => item.job_id === selectedJobId.value)
  selectedEvaluationId.value = latest?.id ?? ''
  if (latest) void loadEvaluation(latest.job_id, latest.id)
  else run.value = null
})
watch(selectedEvaluationId, (evaluationId) => {
  if (!evaluationId) {
    run.value = null
    return
  }
  const item = history.value.find((entry) => entry.id === evaluationId)
  if (item) void loadEvaluation(item.job_id, item.id)
})
watch(() => props.active, (active) => {
  if (!active) stopPolling()
  else if (run.value && ['queued', 'running', 'paused'].includes(run.value.status)) {
    void loadEvaluation(run.value.job_id, run.value.id)
  }
})
onBeforeUnmount(() => {
  stopPolling()
  if (detailSearchTimer !== undefined) globalThis.clearTimeout(detailSearchTimer)
  detailRequestToken += 1
  loadingToken += 1
})
</script>

<template>
  <main class="cross-evaluation">
    <input
      ref="inputRef"
      class="sr-only"
      type="file"
      accept=".xlsx"
      @change="handleWorkbookSelected"
    >
    <header class="cross-evaluation__header">
      <div>
        <div class="workspace-heading">
          <strong>{{ zh ? 'Ground Truth 评测' : 'Ground Truth Evaluation' }}</strong>
        </div>
        <p>{{ zh ? '选择报告、提取任务与人工基准后开始评测，每次评测及日志都会独立保存。' : 'Select a report, extraction run and human baseline. Each evaluation and its logs are saved separately.' }}</p>
      </div>
      <div class="baseline-actions">
        <button
          type="button"
          class="primary-button import-button"
          :disabled="!canImport || previewing || importing"
          :title="visibleDatasets.length ? strings.alreadyImported : selectedReportDocument ? strings.upload : strings.selectReportFirst"
          @click="chooseWorkbook"
        >
          ＋ {{ previewing ? (zh ? '正在读取 Excel…' : 'Reading Excel…') : strings.upload }}
        </button>
        <button
          type="button"
          :disabled="importing || previewing"
          @click="openManager"
        >
          {{ zh ? '管理基准' : 'Manage baselines' }}
        </button>
      </div>
    </header>
    <div class="workspace-columns">
      <div class="workspace-sidebar">
        <section class="cross-evaluation__scope">
          <div class="scope-heading">
            <h3>{{ zh ? '评测配置' : 'Evaluation setup' }}</h3>
            <span
              v-if="currentDataset"
              class="baseline-badge"
            >✓ {{ zh ? '已关联人工基准' : 'Baseline linked' }}</span>
            <div class="scope-actions">
              <template v-if="runningState && isCurrentJob">
                <button
                  type="button"
                  :disabled="controlling"
                  @click="controlEvaluation(run?.status === 'paused' ? 'resume' : 'pause')"
                >
                  {{ run?.status === 'paused' ? strings.resume : strings.pause }}
                </button>
                <button
                  type="button"
                  class="terminate-button"
                  :disabled="controlling"
                  @click="controlEvaluation('terminate')"
                >
                  {{ strings.terminate }}
                </button>
              </template>
              <button
                type="button"
                class="primary-button"
                :disabled="!canEvaluate || running || loading || controlling"
                @click="startEvaluation"
              >
                {{ runningState || running ? (run?.status === 'paused' ? strings.statusPaused : strings.running) : strings.run }}
              </button>
            </div>
          </div>
          <p class="scope-description">
            {{ zh ? '基准按报告保存；同一报告的不同提取任务可重复评测，无需重新导入。' : 'Baselines are saved per report and can be reused across extraction runs.' }}
          </p>
          <label>
            <span>{{ zh ? '考古报告' : 'Archaeological report' }}</span>
            <el-select
              v-model="selectedReportDocumentId"
              class="evaluation-select"
              popper-class="evaluation-select-popper"
              :aria-label="zh ? '考古报告' : 'Archaeological report'"
              :disabled="loading || !reportOptions.length"
              filterable
            >
              <el-option
                v-for="report in reportOptions"
                :key="report.id"
                :label="report.name"
                :value="report.id"
              />
            </el-select>
          </label>
          <p class="report-import-hint">
            {{ strings.reportImportHint }}
          </p>
          <label>
            <span>{{ strings.job }}</span>
            <el-select
              v-model="selectedJobId"
              :disabled="loading || !reportJobs.length"
              :placeholder="strings.selectJob"
              :aria-label="strings.job"
              :no-match-text="zh ? '没有匹配的提取任务' : 'No matching extraction runs'"
              :no-data-text="strings.noJobs"
              class="evaluation-select"
              popper-class="evaluation-select-popper"
              filterable
            >
              <template #label>
                <span
                  v-if="selectedJob"
                  :title="jobOptionLabel(selectedJob)"
                >{{ jobSelectionLabel(selectedJob) }}</span>
              </template>
              <el-option
                v-for="job in reportJobs"
                :key="job.job_id"
                :label="jobOptionLabel(job)"
                :value="job.job_id"
              >
                <div
                  class="evaluation-option"
                  :title="jobOptionLabel(job)"
                >
                  <strong>{{ templateName(job.template_name, job.job_id.slice(-10)) }}</strong>
                  <span>{{ jobHistoryLabel(job) }}</span>
                </div>
              </el-option>
            </el-select>
          </label>
          <p
            v-if="selectedJob"
            class="job-ready"
          >
            {{ zh ? '提取完成' : 'Extraction completed' }} · {{ templateName(selectedJob.template_name, selectedJob.job_id.slice(-10)) }} · {{ formatDate(selectedJob.created_at) }}
          </p>
          <label>
            <span>{{ strings.dataset }}</span>
            <el-select
              v-model="selectedDatasetId"
              :disabled="loading || !visibleDatasets.length"
              :placeholder="strings.selectDataset"
              :aria-label="strings.dataset"
              :no-match-text="zh ? '没有匹配的人工基准' : 'No matching baselines'"
              :no-data-text="strings.noDatasets"
              class="evaluation-select"
              popper-class="evaluation-select-popper"
              filterable
            >
              <el-option
                v-for="dataset in visibleDatasets"
                :key="dataset.id"
                :label="`${dataset.name} · ${dataset.version} · ${dataset.record_count}`"
                :value="dataset.id"
              />
            </el-select>
          </label>
          <div class="matching-method">
            <span>{{ zh ? '器物匹配方式' : 'Artifact matching' }}</span>
            <strong>{{ currentDataset?.key_mode === 'artifact_id' ? (zh ? '完整器物编号 · 规范化精确匹配' : 'Artifact ID · normalized exact match') : (zh ? '遗迹号 + 序号 · 规范化精确匹配' : 'Context ID + sequence · normalized exact match') }}</strong>
          </div>
          <button
            v-if="currentDataset"
            type="button"
            class="baseline-details-button"
            @click="baselineDetailsOpen = true"
          >
            {{ zh ? '查看人工基准信息' : 'View baseline details' }} →
          </button>
          <div class="start-card">
            <div><span class="step-number">1</span><div><strong>{{ zh ? '先精确匹配器物，再评字段语义' : 'Match artifacts first, then compare fields' }}</strong><p>{{ zh ? '编号、尺寸与图注按规则核对；描述等文本进行语义比较。' : 'IDs, measurements and captions use structured checks; descriptions use semantic comparison.' }}</p></div></div>
          </div>
          <p
            v-if="run?.status === 'paused'"
            class="scope-note"
          >
            {{ strings.pausedHint }}
          </p>
          <p
            v-if="run?.status === 'terminated'"
            class="scope-note"
          >
            {{ strings.terminatedHint }}
          </p>
          <p
            v-if="selectedJob && !isCurrentJob"
            class="scope-note"
          >
            {{ strings.historicalReadOnly }}
          </p>
          <p
            v-if="!selectedJob"
            class="scope-note"
          >
            {{ strings.noActiveJob }}
          </p>
        </section>

        <div
          v-if="loading"
          class="cross-evaluation__empty"
        >
          {{ strings.loading }}
        </div>
        <div
          v-else-if="!selectedReportDocument"
          class="cross-evaluation__empty"
        >
          {{ strings.noReport }}
        </div>
        <div
          v-else-if="!reportJobs.length"
          class="cross-evaluation__empty"
        >
          {{ strings.noJobs }}
        </div>
        <div
          v-else-if="!visibleDatasets.length"
          class="cross-evaluation__empty"
        >
          {{ strings.noDatasets }}
          <button
            type="button"
            :disabled="!canImport"
            @click="chooseWorkbook"
          >
            {{ strings.upload }}
          </button>
        </div>
        <section class="history-panel job-history">
          <header><div><h3>{{ zh ? '提取任务与评测历史' : 'Extraction and evaluation history' }}</h3><p>{{ zh ? '每次提取独立评测，历史结果和日志长期保留。' : 'Each extraction can be evaluated separately; history and logs are retained.' }}</p></div><span>{{ showAllHistory ? (zh ? '全部报告' : 'All reports') : (zh ? '本报告' : 'This report') }} · {{ showAllHistory ? jobs.length : reportJobs.length }} {{ zh ? '次提取' : 'runs' }}</span></header>
          <div class="history-filters">
            <input
              v-model="historyQuery"
              type="search"
              :placeholder="zh ? '搜索模板、报告或任务编号…' : 'Search templates, reports or run IDs…'"
              :aria-label="zh ? '搜索提取任务' : 'Search extraction runs'"
            >
            <el-select
              v-model="historyStatus"
              class="evaluation-select history-status-select"
              popper-class="evaluation-select-popper"
              :aria-label="zh ? '按评测状态筛选' : 'Filter by evaluation status'"
            >
              <el-option
                :label="zh ? '全部评测状态' : 'All evaluation statuses'"
                value="all"
              />
              <el-option
                :label="strings.statusCompleted"
                value="completed"
              />
              <el-option
                :label="zh ? '尚未评测' : 'Not evaluated'"
                value="pending"
              />
              <el-option
                :label="strings.statusRunning"
                value="running"
              />
              <el-option
                :label="strings.statusPaused"
                value="paused"
              />
              <el-option
                :label="strings.statusFailed"
                value="failed"
              />
              <el-option
                :label="strings.statusTerminated"
                value="terminated"
              />
              <el-option
                :label="strings.statusQueued"
                value="queued"
              />
            </el-select>
            <button
              type="button"
              @click="showAllHistory = !showAllHistory"
            >
              {{ showAllHistory ? (zh ? '本报告' : 'This report') : (zh ? '全部历史' : 'All reports') }}
            </button>
          </div>
          <div class="job-history__list">
            <template
              v-for="job in visibleJobs"
              :key="job.job_id"
            >
              <button
                type="button"
                class="job-history__item"
                :class="{ selected: job.job_id === selectedJobId && (!jobEvaluation(job.job_id) || selectedEvaluationId === jobEvaluation(job.job_id)?.id) }"
                @click="selectJob(job)"
              >
                <span class="job-history__icon">{{ templateName(job.template_name, 'T').slice(0, 1) }}</span>
                <span class="job-history__info"><strong>{{ templateName(job.template_name, job.job_id.slice(-10)) }}</strong><small :title="jobHistoryLabel(job)">{{ jobHistoryLabel(job) }}</small></span>
                <span class="job-history__status"><b :class="jobEvaluation(job.job_id)?.status || 'pending'">{{ jobEvaluation(job.job_id) ? statusLabel(jobEvaluation(job.job_id)!.status) : (zh ? '尚未评测' : 'Not evaluated') }}</b><small>{{ jobEvaluation(job.job_id) ? (zh ? '查看结果' : 'View result') : (zh ? '选择任务' : 'Select run') }} →</small></span>
              </button>
              <button
                v-for="item in job.job_id === selectedJobId ? history.filter(entry => entry.job_id === job.job_id && entry.id !== jobEvaluation(job.job_id)?.id) : []"
                :key="item.id"
                type="button"
                class="job-history__item job-history__evaluation"
                :class="{ selected: item.id === selectedEvaluationId }"
                @click="openHistorical(item)"
              >
                <span class="job-history__info"><strong>{{ zh ? '历史 GT 评测' : 'Previous GT evaluation' }}</strong><small>{{ formatDate(item.created_at) }} · {{ displayMatchingVersion(item.matching_version_id) }}</small></span>
                <span class="job-history__status"><b :class="item.status">{{ statusLabel(item.status) }}</b><small>{{ zh ? '查看结果' : 'View result' }} →</small></span>
              </button>
            </template>
            <p
              v-if="!filteredJobs.length"
              class="history-empty"
            >
              {{ zh ? '没有符合条件的提取任务。' : 'No extraction runs match these filters.' }}
            </p>
          </div>
          <footer>
            <span>{{ zh ? '显示' : 'Showing' }} {{ filteredJobs.length ? (historyPage - 1) * historyPageSize + 1 : 0 }}–{{ Math.min(historyPage * historyPageSize, filteredJobs.length) }} / {{ filteredJobs.length }}</span>
            <div class="history-pagination">
              <button
                type="button"
                :disabled="historyPage <= 1"
                :aria-label="zh ? '上一页' : 'Previous page'"
                @click="historyPage--"
              >
                ‹
              </button>
              <span>{{ historyPage }} / {{ historyPages }}</span>
              <button
                type="button"
                :disabled="historyPage >= historyPages"
                :aria-label="zh ? '下一页' : 'Next page'"
                @click="historyPage++"
              >
                ›
              </button>
            </div>
          </footer>
        </section>
      </div>
      <div class="workspace-results">
        <section
          class="result-panel"
        >
          <header>
            <h3>{{ zh ? '评测结果' : 'Evaluation results' }}</h3>
            <span v-if="displayRun">{{ evaluationRunLabel(displayRun) }}</span>
            <div class="result-exports">
              <button
                type="button"
                :disabled="!displayRun || displayRun.status !== 'completed' || exporting"
                @click="exportRun('detail')"
              >
                {{ strings.exportDetail }}
              </button>
              <button
                type="button"
                :disabled="!displayRun || displayRun.status !== 'completed' || exporting"
                @click="exportRun('text_image')"
              >
                {{ strings.exportImages }}
              </button>
            </div>
          </header>
          <template v-if="displayRun">
            <div
              v-if="runningState"
              class="evaluation-progress"
            >
              <i :style="{ width: `${displayRun.progress.percent}%` }" /><span>{{ displayRun.progress.percent }}%</span>
            </div>
            <p
              v-if="displayRun.status === 'failed'"
              class="evaluation-error"
            >
              {{ displayRun.error || strings.evaluationFailed }}
            </p>
            <nav
              class="result-tabs"
              aria-label="Evaluation result sections"
            >
              <button
                type="button"
                :class="{ active: resultTab === 'summary' }"
                @click="resultTab = 'summary'"
              >
                {{ strings.overview }}
              </button>
              <button
                type="button"
                :class="{ active: resultTab === 'items' }"
                @click="openDetailTab"
              >
                {{ strings.items }} {{ detailTotal }}
              </button>
              <button
                type="button"
                :class="{ active: resultTab === 'logs' }"
                @click="resultTab = 'logs'"
              >
                {{ strings.logs }} {{ displayRun.logs?.length || 0 }}
              </button>
              <div
                v-if="resultTab === 'items'"
                class="detail-search-toolbar"
              >
                <el-input
                  v-model="detailSearchQuery"
                  class="detail-search-input"
                  type="search"
                  clearable
                  :placeholder="strings.searchRecords"
                  :aria-label="strings.searchRecords"
                  @input="scheduleDetailSearch"
                >
                  <template #prefix>
                    <el-icon aria-hidden="true">
                      <Search />
                    </el-icon>
                  </template>
                </el-input>
                <el-select
                  v-model="detailSearchField"
                  class="evaluation-select detail-search-field"
                  popper-class="evaluation-select-popper detail-search-popper"
                  :aria-label="strings.searchField"
                  @change="changeDetailSearchField"
                >
                  <template #prefix>
                    <el-icon aria-hidden="true">
                      <Filter />
                    </el-icon>
                  </template>
                  <el-option
                    v-for="field in detailSearchFields"
                    :key="field.key"
                    :label="field.label"
                    :value="field.key"
                  />
                </el-select>
              </div>
            </nav>
            <div
              v-if="displayRun.summary && resultTab === 'summary'"
              class="result-scroll-area"
            >
              <div class="result-overview">
                <div class="overview-main">
                  <div class="metric-grid">
                    <article><span>{{ zh ? '编号成功配对' : 'Matched identifiers' }}</span><strong>{{ displayRun.summary.matched_records }} <small>/ {{ displayRun.summary.gold_records }}</small></strong><small>{{ zh ? '以人工标注记录为基准' : 'Out of human baseline rows' }}</small></article>
                    <article><span>{{ strings.fieldScore }}</span><strong>{{ formatScore(displayRun.summary.field_macro_score) }}</strong><small>{{ zh ? '已评测字段的平均一致度' : 'Average over evaluated fields' }}</small></article>
                    <article><span>{{ strings.idRecall }}</span><strong>{{ formatScore(displayRun.summary.artifact_id_recall) }}</strong><small>{{ zh ? '人工编号被匹配的比例' : 'Human IDs matched' }}</small></article>
                    <article><span>{{ strings.ambiguous }}</span><strong>{{ displayRun.summary.ambiguous_records }} <small>{{ zh ? '条' : 'rows' }}</small></strong><small>{{ zh ? '重复编号需人工确认' : 'Duplicate IDs need review' }}</small></article>
                  </div>
                  <h4 class="section-title">
                    {{ zh ? '字段一致度' : 'Field agreement' }}
                  </h4>
                  <div class="field-metrics">
                    <div
                      v-for="metric in displayRun.field_metrics.filter(item => item.evaluated > 0)"
                      :key="metric.key"
                    >
                      <span>{{ fieldLabel(metric.key, metric.label) }}</span><b>{{ formatScore(metric.score) }}</b>
                      <div class="field-progress">
                        <i :style="{ width: `${Math.max(0, Math.min(100, (metric.score ?? 0) * 100))}%` }" />
                      </div>
                      <small>{{ strings.exact }} {{ metric.exact }} · {{ strings.missing }} {{ metric.missing }} · {{ strings.mismatch }} {{ metric.mismatched }}</small>
                    </div>
                    <p
                      v-if="!displayRun.field_metrics.some(item => item.evaluated > 0)"
                      class="history-empty"
                    >
                      {{ strings.notAvailable }}
                    </p>
                  </div>
                </div>
                <aside class="overview-aside">
                  <section class="overview-card">
                    <h4>{{ zh ? '器物配对概况' : 'Matching overview' }}</h4><div class="matching-overview">
                      <div
                        class="matching-ring"
                        :style="{ '--match-percent': `${displayRun.summary.gold_records ? Math.min(100, displayRun.summary.matched_records / displayRun.summary.gold_records * 100) : 0}%` }"
                      >
                        <strong>{{ displayRun.summary.gold_records ? formatScore(displayRun.summary.matched_records / displayRun.summary.gold_records) : '—' }}</strong>
                      </div><div class="matching-legend">
                        <span><i class="legend-matched" />{{ strings.matched }} <b>{{ displayRun.summary.matched_records }}</b></span><span><i class="legend-ambiguous" />{{ strings.ambiguous }} <b>{{ displayRun.summary.ambiguous_records }}</b></span><span><i class="legend-unmatched" />{{ strings.unmatchedHuman }} <b>{{ displayRun.summary.unmatched_gold_records ?? '—' }}</b></span>
                      </div>
                    </div>
                  </section>
                  <section class="overview-card baseline-card">
                    <h4>{{ currentDataset?.name || (zh ? '人工标注基准' : 'Human baseline') }}</h4><p>{{ currentDataset?.version }} · {{ displayRun.summary.gold_records }} {{ zh ? '条人工记录' : 'human rows' }}</p><small>{{ displayRun.scoring_version || '—' }} <template v-if="displayRun.semantic_model">· {{ displayRun.semantic_model }}</template></small>
                  </section>
                  <section class="overview-card">
                    <div class="aside-heading">
                      <h4>{{ zh ? '本次评测记录' : 'Evaluation record' }}</h4><button
                        type="button"
                        @click="resultTab = 'logs'"
                      >
                        {{ zh ? '查看日志' : 'View logs' }} →
                      </button>
                    </div><p>{{ zh ? '评测 ID' : 'Evaluation ID' }}: {{ displayRun.id }}</p><p>{{ statusLabel(displayRun.status) }} · {{ formatDate(displayRun.created_at) }}</p>
                  </section>
                </aside>
              </div>
              <p
                v-if="!displayRun.summary.full_document_scope"
                class="scope-note"
              >
                {{ strings.partial }}
              </p>
              <p
                v-if="displayRun.detection_metrics.length === 0 && Object.keys(displayRun.relation_metrics).length === 0"
                class="scope-note"
              >
                {{ strings.unsupported }}
              </p>
              <div class="result-counts">
                {{ strings.predicted }} {{ displayRun.summary.predicted_records }} · {{ strings.matched }} {{ displayRun.summary.matched_records }} · {{ strings.unmatchedHuman }} {{ displayRun.summary.unmatched_gold_records ?? '—' }} · {{ strings.ambiguous }} {{ displayRun.summary.ambiguous_records }} · {{ strings.missingRecords }} {{ displayRun.summary.unmatched_predicted_records }}
              </div>
              <p
                v-for="warning in displayRun.warnings"
                :key="warning"
                class="scope-note"
              >
                {{ warningMessage(warning) }}
              </p>
            </div>
            <div
              v-if="resultTab === 'items'"
              class="detail-list-shell"
            >
              <div
                class="detail-list"
                :aria-busy="detailSearchLoading"
                :aria-label="strings.items"
                tabindex="0"
              >
                <p
                  v-if="detailSearchLoading && !detailItems.length"
                  class="detail-search-state"
                  aria-live="polite"
                >
                  {{ strings.searchingRecords }}
                </p>
                <p
                  v-else-if="!detailSearchLoading && !detailRows.length"
                  class="detail-search-state"
                  aria-live="polite"
                >
                  {{ strings.searchNoResults }}
                </p>
                <article
                  v-for="detailRow in detailRows"
                  :key="detailRow.item.id"
                  class="detail-row"
                >
                  <div class="detail-row__title">
                    <strong>{{ formatArtifactId(detailRow.item.artifact_id) }}</strong><span>{{ matchStatusLabel(detailRow.item.match_status) }}</span><small>{{ detailRow.sourcePages }}</small>
                  </div>
                  <div
                    v-for="field in detailRow.fields"
                    :key="field.key"
                    class="detail-field"
                  >
                    <b>{{ fieldLabel(field.key, field.label) }}</b><span>{{ strings.human }}: {{ displayFieldValue(field.key, field.expected) }}</span><span>{{ strings.machine }}: {{ displayFieldValue(field.key, field.actual) }}</span>
                    <em>{{ formatScore(field.score) }} · {{ verdictLabel(field.verdict) }}</em><small v-if="field.reason">{{ field.reason }}</small>
                  </div>
                </article>
              </div>
            </div>
            <div
              v-if="resultTab === 'logs'"
              class="run-logs"
            >
              <p
                v-for="(entry, index) in displayRun.logs || []"
                :key="index"
              >
                <time>{{ formatDate(entry.at) }}</time>{{ logMessage(entry) }}
              </p>
              <p
                v-if="displayRun.error"
                class="run-error"
              >
                {{ displayRun.error }}
              </p>
            </div>
          </template>
          <div
            v-else
            class="result-empty"
          >
            <strong>{{ zh ? '暂无评测结果' : 'No evaluation results' }}</strong><span>{{ strings.empty }}</span>
          </div>
        </section>
      </div>
    </div>

    <el-dialog
      v-model="managerOpen"
      class="archfact-baseline-dialog"
      modal-class="archfact-baseline-dialog-overlay"
      :title="zh ? '管理人工基准' : 'Manage human baselines'"
      width="min(900px, 94vw)"
    >
      <div class="baseline-manager">
        <div
          class="baseline-manager__list"
          :aria-label="zh ? '人工基准列表' : 'Human baselines'"
        >
          <button
            v-for="dataset in datasets"
            :key="dataset.id"
            type="button"
            class="baseline-manager__item"
            :class="{ selected: dataset.id === managedDatasetId }"
            :disabled="deletingDataset"
            @click="inspectDataset(dataset.id)"
          >
            <strong>{{ dataset.name }}</strong><small>{{ dataset.document_filename || '—' }}</small><small>{{ dataset.version }} · {{ dataset.record_count }} {{ zh ? '条记录' : 'records' }}</small>
          </button>
          <p
            v-if="!datasets.length"
            class="history-empty"
          >
            {{ zh ? '尚无人工基准。' : 'No human baselines available.' }}
          </p>
        </div>
        <section
          v-if="managedDataset"
          class="baseline-details baseline-manager__details"
        >
          <p><b>{{ strings.name }}：</b>{{ managedDataset.name }}</p>
          <p><b>{{ strings.reportLabel }}：</b>{{ managedDataset.document_filename || '—' }}</p>
          <p><b>{{ zh ? '来源文件' : 'Workbook' }}：</b>{{ managedDataset.workbook_name || '—' }}</p>
          <p><b>{{ strings.version }}：</b>{{ managedDataset.version }} · {{ managedDataset.record_count }} {{ zh ? '条记录' : 'records' }}</p>
          <p><b>{{ zh ? '关联 GT 评测' : 'Related GT evaluations' }}：</b>{{ managementLoading ? strings.loading : managedEvaluations === null ? '—' : `${managedEvaluations.length}${managedEvaluations.length >= 100 ? '+' : ''}` }}</p>
          <p
            v-if="managedEvaluations?.length"
            class="management-notice"
          >
            {{ zh ? '该基准已有评测记录，为保留历史结果暂不能删除。' : 'This baseline has evaluation history and cannot be deleted.' }}
          </p>
          <p
            v-else
            class="management-notice"
          >
            {{ zh ? '删除前系统也会检查机器校验是否引用该基准。' : 'Machine verification references are also checked before deletion.' }}
          </p>
          <h4>{{ strings.columns }}</h4>
          <dl v-if="Object.keys(managedDataset.field_mapping || {}).length">
            <template
              v-for="(column, field) in managedDataset.field_mapping"
              :key="field"
            >
              <dt>{{ baselineFieldLabel(String(field)) }}</dt><dd>{{ column }}</dd>
            </template>
          </dl>
          <p v-else>
            {{ zh ? '该基准未保存字段映射信息。' : 'No field mapping was saved for this baseline.' }}
          </p>
          <button
            type="button"
            class="terminate-button"
            :disabled="managementLoading || managedEvaluations === null || managedEvaluations.length > 0 || deletingDataset"
            @click="removeManagedDataset"
          >
            {{ deletingDataset ? (zh ? '正在删除…' : 'Deleting…') : (zh ? '删除基准' : 'Delete baseline') }}
          </button>
        </section>
      </div>
    </el-dialog>

    <el-dialog
      v-model="baselineDetailsOpen"
      class="archfact-baseline-dialog"
      modal-class="archfact-baseline-dialog-overlay"
      :title="zh ? '人工基准信息' : 'Human baseline details'"
      width="min(620px, 94vw)"
    >
      <div
        v-if="currentDataset"
        class="baseline-details"
      >
        <p><b>{{ strings.name }}：</b>{{ currentDataset.name }}</p>
        <p><b>{{ strings.reportLabel }}：</b>{{ currentDataset.document_filename }}</p>
        <p><b>{{ strings.version }}：</b>{{ currentDataset.version }} · {{ currentDataset.record_count }} {{ zh ? '条记录' : 'records' }}</p>
        <h4>{{ strings.columns }}</h4>
        <dl v-if="Object.keys(currentDataset.field_mapping || {}).length">
          <template
            v-for="(column, field) in currentDataset.field_mapping"
            :key="field"
          >
            <dt>{{ baselineFieldLabel(String(field)) }}</dt><dd>{{ column }}</dd>
          </template>
        </dl>
        <p v-else>
          {{ zh ? '这份历史基准未保存字段映射信息。' : 'Field mappings were not saved for this historical baseline.' }}
        </p>
      </div>
    </el-dialog>

    <el-dialog
      v-model="importOpen"
      class="archfact-baseline-dialog"
      modal-class="archfact-baseline-dialog-overlay"
      :title="strings.uploadTitle"
      width="min(760px, 94vw)"
      destroy-on-close
      :close-on-click-modal="!importing"
      :close-on-press-escape="!importing"
      :show-close="!importing"
    >
      <div
        v-if="preview"
        class="import-form"
      >
        <div class="import-form__meta">
          <span>{{ strings.report }}: {{ selectedReportDocument?.filename || '—' }}</span>
          <span>{{ zh ? 'Excel 文件' : 'Excel file' }}: {{ preview.filename }}</span>
          <span>{{ strings.sheet }}: {{ preview.sheet_name }}</span>
        </div>
        <div
          v-if="importing"
          class="import-progress"
          role="status"
          aria-live="polite"
        >
          <strong>{{ importStage === 'processing' ? (zh ? '文件已上传，正在解析并保存基准…' : 'Upload complete; processing and saving baseline…') : (zh ? '正在上传 Excel…' : 'Uploading Excel…') }}</strong>
          <template v-if="importStage === 'uploading' && uploadPercent !== null">
            <progress
              :value="uploadPercent"
              max="100"
            /> <span>{{ uploadPercent }}%</span>
          </template>
          <progress v-else />
          <small>{{ zh ? '请保持窗口打开，服务器处理完成后会提示导入成功。' : 'Keep this window open. Success is shown after server processing finishes.' }}</small>
        </div>
        <label>{{ strings.name }}<input
          v-model="datasetName"
          maxlength="160"
        ></label>
        <label>{{ strings.version }}<input
          v-model="datasetVersion"
          maxlength="40"
        ></label>
        <label>{{ strings.keyMode }}
          <select v-model="keyMode">
            <option value="context_sequence">{{ strings.composite }}</option>
            <option value="artifact_id">{{ strings.single }}</option>
          </select>
        </label>
        <p class="mapping-hint">
          {{ strings.mapHint }}
        </p>
        <div class="mapping-grid">
          <label
            v-for="field in fields"
            :key="field.key"
          >{{ field.label }}
            <select v-model="fieldMapping[field.key]">
              <option value="">—</option>
              <option
                v-for="header in preview.headers"
                :key="header"
                :value="header"
              >{{ header }}</option>
            </select>
          </label>
        </div>
      </div>
      <template #footer>
        <button
          type="button"
          class="secondary-button"
          :disabled="importing"
          @click="importOpen = false"
        >
          {{ strings.cancel }}
        </button>
        <button
          type="button"
          class="primary-button"
          :disabled="importing"
          @click="saveDataset"
        >
          {{ importing ? (zh ? '正在导入…' : 'Importing…') : strings.confirmImport }}
        </button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped lang="scss">
.cross-evaluation { display: flex; flex-direction: column; box-sizing: border-box; height: 100%; min-height: 0; padding: 14px 16px 24px; color: #403831; }
.workspace-columns { display: grid; flex: 1; grid-template-columns: minmax(340px, 34%) minmax(0, 1fr); align-items: stretch; gap: 16px; width: 100%; min-height: 0; }
.workspace-sidebar, .workspace-results { display: flex; flex-direction: column; gap: 14px; min-width: 0; min-height: 0; overflow: hidden; }
.cross-evaluation__header, .workspace-sidebar > .cross-evaluation__scope, .workspace-sidebar > .cross-evaluation__empty { flex: none; }
.workspace-results .result-panel { height: 100%; min-height: 0; }
.cross-evaluation__header, .cross-evaluation__scope, .history-panel, .result-panel { background: #fffdfa; border: 1px solid #e8ded3; border-radius: 12px; box-shadow: 0 2px 10px rgb(61 43 28 / 6%); }
.cross-evaluation__header { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 18px 20px; }
.cross-evaluation__header span { color: #a16b3a; font-size: 11px; letter-spacing: .08em; }
.cross-evaluation__header h2 { margin: 5px 0; font-size: 23px; }
.cross-evaluation__header p { margin: 0; color: #81766a; }
.cross-evaluation__scope { display: grid; grid-template-columns: minmax(0, 1fr); align-items: stretch; gap: 14px; padding: 16px; }
.scope-report, .cross-evaluation__scope label { display: grid; gap: 7px; min-width: 0; }
.scope-report span, .cross-evaluation__scope label span { color: #86796c; font-size: 12px; }
.scope-report strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cross-evaluation select, .import-form input, .import-form select { min-width: 0; height: 38px; padding: 0 10px; color: #51483f; background: #fff; border: 1px solid #ded3c8; border-radius: 7px; }
.evaluation-select { width: 100%; min-width: 0; }
.evaluation-select :deep(.el-select__wrapper) { min-height: 38px; padding: 0 10px; background: #fffdfa; border-radius: 7px; box-shadow: 0 0 0 1px #ded3c8 inset; }
.evaluation-select :deep(.el-select__wrapper:hover) { background: #fff9f2; box-shadow: 0 0 0 1px #c8824c inset; }
.evaluation-select :deep(.el-select__wrapper.is-focused) { box-shadow: 0 0 0 1px #b96528 inset, 0 0 0 3px rgb(201 111 43 / 12%); }
.evaluation-select :deep(.el-select__selected-item) { min-width: 0; color: #51483f; }
.evaluation-select :deep(.el-select__caret) { color: #ae7650; }
:global(.evaluation-select-popper.el-popper) { padding: 5px; background: #fffdfa; border: 1px solid #e4cdb9; border-radius: 9px; box-shadow: 0 10px 24px rgb(94 57 28 / 16%); }
:global(.evaluation-select-popper .el-select-dropdown__wrap) { max-height: 280px; }
:global(.evaluation-select-popper .el-select-dropdown__item) { height: auto; min-height: 36px; padding: 8px 10px; color: #754b30; border-radius: 6px; line-height: 1.4; }
:global(.evaluation-select-popper .el-select-dropdown__item.is-hovering) { color: #9a4e18; background: #fff1e3; }
:global(.evaluation-select-popper .el-select-dropdown__item.is-selected) { color: #fff; background: linear-gradient(135deg, #d0782d, #b95d1c); }
:global(.evaluation-option) { display: grid; gap: 3px; min-width: 0; }
:global(.evaluation-option strong), :global(.evaluation-option span) { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
:global(.evaluation-option strong) { font-size: 13px; }
:global(.evaluation-option span) { color: #8d7968; font-size: 11px; font-weight: 400; }
:global(.evaluation-select-popper .el-select-dropdown__item.is-selected .evaluation-option span) { color: #fff4e8; }
button { min-height: 38px; padding: 0 16px; color: #895322; background: #fffaf5; border: 1px solid #dbb48d; border-radius: 8px; cursor: pointer; }
button:hover { background: #fff0e1; }
button:disabled { opacity: .5; cursor: not-allowed; }
.primary-button { color: white; font-weight: 600; background: linear-gradient(110deg,#a85d1b,#d7761e); border: 0; }
.primary-button:hover { background: linear-gradient(110deg,#914c12,#c96812); }
.secondary-button { margin-right: 8px; }
.cross-evaluation__empty { padding: 30px; color: #897d71; text-align: center; background: #fffdfa; border: 1px dashed #ddcfc0; border-radius: 12px; }
.cross-evaluation__empty button { margin-left: 12px; }
.cross-evaluation__scope .scope-note { margin: 0; }
.history-panel, .result-panel { min-width: 0; padding: 16px; }
.result-panel { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.result-panel > header, .evaluation-progress, .evaluation-error, .result-tabs { flex: none; }
.history-panel header, .result-panel header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
h3 { margin: 0; font-size: 17px; }
.history-panel header span, .result-panel header span { color: #8a7c6e; font-size: 12px; }
.history-table-wrap { overflow: auto; }
.report-history-list { display: grid; gap: 8px; }
.report-history-card { display: grid; width: 100%; gap: 5px; padding: 12px; text-align: left; border: 1px solid #ebded0; background: #fffdfa; }
.report-history-card.selected { border-color: #b76d2c; background: #fff4e9; }
.report-history-card strong { overflow-wrap: anywhere; color: #503d2a; }
.report-history-card small { color: #8a7c6e; }
.report-history-card span { font-size: 12px; color: #8b5529; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 10px 9px; text-align: left; border-bottom: 1px solid #eee7df; white-space: nowrap; }
th { color: #84796e; font-weight: 500; }
tbody tr { cursor: pointer; }
tbody tr:hover, tbody tr.selected { background: #fff3e7; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.metric-grid article { display: grid; gap: 7px; padding: 14px; background: #faf6f1; border-radius: 9px; }
.metric-grid span { color: #82766a; font-size: 12px; }
.metric-grid strong { color: #9b5e26; font-size: 22px; }
.result-counts { margin: 14px 0; color: #76695d; font-size: 13px; }
.result-exports { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; margin-left: auto; }
.result-exports button { min-height: 32px; padding: 0 10px; font-size: 12px; }
.result-tabs { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin: 14px -16px; padding: 0 16px; border-bottom: 1px solid #eee6dd; }
.result-tabs button { min-height: 34px; padding: 0 12px; border: 0; border-radius: 0; background: transparent; color: #76695d; }
.result-tabs button.active { color: #a65a1c; border-bottom: 2px solid #ba641d; font-weight: 600; }
.detail-search-toolbar { display: flex; flex: 0 1 360px; align-items: center; gap: 8px; min-width: 240px; margin-left: auto; padding: 0 0 7px; }
.detail-search-input { flex: 1; min-width: 120px; }
.detail-search-input :deep(.el-input__wrapper) { min-height: 34px; background: #fff; border-radius: 7px; box-shadow: 0 0 0 1px #ded3c8 inset; }
.detail-search-input :deep(.el-input__wrapper:hover) { box-shadow: 0 0 0 1px #c8824c inset; }
.detail-search-input :deep(.el-input__wrapper.is-focus) { box-shadow: 0 0 0 1px #b96528 inset, 0 0 0 3px rgb(201 111 43 / 12%); }
.detail-search-input :deep(.el-input__prefix-inner), .detail-search-field :deep(.el-select__prefix) { color: #ad7651; }
.detail-search-field { flex: none; width: 148px; }
.detail-search-field :deep(.el-select__wrapper) { min-height: 34px; padding: 0 9px; background: #fff; border-radius: 7px; box-shadow: 0 0 0 1px #ded3c8 inset; }
.detail-search-field :deep(.el-select__wrapper:hover) { box-shadow: 0 0 0 1px #c8824c inset; }
.detail-search-field :deep(.el-select__selected-item) { min-width: 0; color: #6c513b; font-size: 12px; }
.detail-search-state { padding: 18px; color: #897d71; font-size: 13px; text-align: center; }
.result-scroll-area, .detail-list, .run-logs { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.detail-list-shell { display: flex; flex: 1; flex-direction: column; min-height: 0; }
.detail-list { display: grid; align-content: start; gap: 10px; scrollbar-width: thin; scrollbar-color: #bd7843 #f3ebe3; }
.detail-list::-webkit-scrollbar { display: block; width: 10px; }
.detail-list::-webkit-scrollbar-track { background: #f3ebe3; border-radius: 8px; }
.detail-list::-webkit-scrollbar-thumb { background: #bd7843; border: 2px solid #f3ebe3; border-radius: 8px; }
.detail-list::-webkit-scrollbar-thumb:hover { background: #a95d27; }
.detail-row { padding: 12px; border: 1px solid #eee6dd; border-radius: 9px; content-visibility: auto; contain-intrinsic-size: auto 480px; }
.detail-row__title { display: flex; align-items: center; gap: 12px; padding-bottom: 8px; }
.detail-row__title span { color: #a65a1c; }
.detail-row__title small { margin-left: auto; color: #8a7c6e; }
.detail-field { display: grid; grid-template-columns: 90px minmax(0,1fr) minmax(0,1fr) 90px; gap: 7px; padding: 7px 0; border-top: 1px solid #f0eae3; font-size: 12px; }
.detail-field span { overflow-wrap: anywhere; }
.detail-field em { color: #96531d; font-style: normal; text-align: right; }
.detail-field small { grid-column: 2 / -1; color: #83715e; }
.run-logs p { display: flex; gap: 12px; padding: 9px 0; margin: 0; border-bottom: 1px solid #eee6dd; }
.run-logs time { color: #938170; white-space: nowrap; }
.run-logs .run-error { color: #b93131; }
.field-metrics { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; }
.field-metrics > div { display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; padding: 10px; border: 1px solid #eee6dd; border-radius: 8px; }
.field-metrics b { color: #915723; }
.field-metrics small { grid-column: 1 / -1; color: #887d72; }
.scope-note { margin: 10px 0 0; padding: 9px 11px; color: #85653f; font-size: 12px; background: #fff7ec; border-radius: 7px; }
.evaluation-progress { position: relative; height: 8px; margin: 12px 0; overflow: hidden; background: #eee6dd; border-radius: 99px; }
.evaluation-progress i { display: block; height: 100%; background: #bd6a20; transition: width .2s; }
.evaluation-progress span { position: absolute; right: 0; top: 10px; color: #82766a; font-size: 11px; }
.evaluation-error { padding: 11px 12px; color: #a92e2e; background: #fff0ed; border: 1px solid #f0c9c3; border-radius: 7px; }
.import-form { display: grid; gap: 12px; max-height: 65vh; overflow: auto; }
.import-form__meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 12px; color: #86796c; font-size: 12px; }
.import-form > label { display: grid; grid-template-columns: 130px minmax(0,1fr); align-items: center; gap: 12px; color: #6f6459; font-size: 13px; }
.mapping-hint { margin: 0; color: #8e7558; font-size: 12px; }
.mapping-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 9px 14px; }
.mapping-grid label { display: grid; grid-template-columns: 118px minmax(0,1fr); align-items: center; gap: 8px; color: #6f6459; font-size: 12px; }
.sr-only { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
:global(.el-overlay.archfact-baseline-dialog-overlay) { background: rgb(38 31 25 / 30%); backdrop-filter: blur(3px); }
:global(.archfact-baseline-dialog.el-dialog) { overflow: hidden; color: #443930; background: radial-gradient(circle at 50% 0, rgb(255 255 255 / 92%), transparent 44%), #fffaf4; border: 1px solid #dfc8b3; border-radius: 14px; box-shadow: 0 18px 56px rgb(74 48 28 / 24%); }
:global(.archfact-baseline-dialog .el-dialog__header) { padding: 20px 24px 15px; margin-right: 0; border-bottom: 1px solid #eee0d3; }
:global(.archfact-baseline-dialog .el-dialog__title) { color: #211c18; font-size: var(--af-font-page-title); font-weight: 600; }
:global(.archfact-baseline-dialog .el-dialog__headerbtn) { top: 12px; right: 14px; width: 36px; height: 36px; border-radius: 8px; }
:global(.archfact-baseline-dialog .el-dialog__headerbtn:hover) { background: #f7eadc; }
:global(.archfact-baseline-dialog .el-dialog__headerbtn .el-dialog__close) { color: #887665; }
:global(.archfact-baseline-dialog .el-dialog__body) { padding: 18px 24px 24px; color: #5d4f42; }
:global(.archfact-baseline-dialog .el-dialog__footer) { padding: 0 24px 20px; }
/* The benchmark workspace shares the warm, compact card layout of machine verification. */
.cross-evaluation__header { min-height: 68px; margin-bottom: 14px; padding: 12px 16px; }
.workspace-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; }
.workspace-heading span { color: #82766a; letter-spacing: 0; }
.workspace-heading strong { color: #3e352d; font-size: 14px; }
.cross-evaluation__header p { margin-top: 5px; font-size: 12px; }
.baseline-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.import-button { flex: none; }
.cross-evaluation__scope { gap: 12px; }
.scope-heading { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.scope-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-left: auto; }
.scope-actions button { white-space: nowrap; }
.terminate-button { color: #b83b34; border-color: #e2a8a2; background: #fff; }
.terminate-button:hover { background: #fff1ef; }
.baseline-badge { padding: 4px 8px; color: #27845a; background: #e8f5ee; border-radius: 12px; font-size: 11px; }
.scope-description { margin: 0; padding-bottom: 12px; color: #81766a; border-bottom: 1px solid #eee6dd; font-size: 12px; }
.report-import-hint { margin: -8px 0 2px; color: #8a7764; font-size: 11px; line-height: 1.45; }
.scope-report strong { padding: 10px; font-size: 13px; font-weight: 500; background: #fff; border: 1px solid #ded3c8; border-radius: 7px; }
.matching-method { display: grid; gap: 7px; color: #86796c; font-size: 12px; }
.matching-method strong { justify-self: start; padding: 7px 9px; color: #705a47; background: #f7f2ed; border-radius: 5px; font-weight: 500; }
.start-card { padding: 12px; background: #faf6f1; border: 1px solid #eee1d3; border-radius: 8px; }
.start-card > div { display: flex; gap: 9px; align-items: flex-start; }
.start-card strong { font-size: 12px; }
.start-card p { margin: 4px 0 10px; color: #81766a; font-size: 12px; line-height: 1.5; }
.job-ready { margin: -6px 0 0; color: #44865f; font-size: 11px; overflow-wrap: anywhere; }
.baseline-details-button { justify-self: start; min-height: 30px; padding: 0; border: 0; background: transparent; font-size: 12px; }
.baseline-details p { color: #64584d; font-size: 13px; overflow-wrap: anywhere; }
.baseline-details h4 { margin: 18px 0 8px; }
.baseline-details dl { display: grid; grid-template-columns: minmax(110px, 1fr) minmax(0, 2fr); margin: 0; border-top: 1px solid #eee6dd; }
.baseline-details dt, .baseline-details dd { margin: 0; padding: 8px; border-bottom: 1px solid #eee6dd; overflow-wrap: anywhere; }
.baseline-details dt { color: #88796b; }
.baseline-manager { display: grid; grid-template-columns: minmax(200px, 0.85fr) minmax(0, 1.15fr); gap: 16px; max-height: 65vh; }
.baseline-manager__list, .baseline-manager__details { min-width: 0; overflow: auto; }
.baseline-manager__list { display: grid; align-content: start; gap: 8px; }
.baseline-manager__item { display: grid; width: 100%; gap: 5px; padding: 11px; text-align: left; border-color: #e7d9cc; background: #fffdfa; }
.baseline-manager__item strong, .baseline-manager__item small { overflow-wrap: anywhere; }
.baseline-manager__item small { color: #796c60; line-height: 1.4; }
.baseline-manager__item.selected { border-color: #bd712d; background: #fff3e7; }
.baseline-manager__details { padding: 0 6px; }
.baseline-manager__details > button { margin-top: 16px; }
.management-notice { padding: 9px; background: #fff7ec; border-radius: 7px; }
.import-progress { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 10px; padding: 12px; background: #fff7ec; border: 1px solid #edd5bc; border-radius: 8px; }
.import-progress strong, .import-progress small { grid-column: 1 / -1; }
.import-progress progress { width: 100%; accent-color: #b96528; }
.import-progress small { color: #796c60; }
.step-number { display: grid; place-items: center; flex: none; width: 24px; height: 24px; color: #aa5c1c; background: #f6e5d2; border-radius: 50%; font-size: 12px; }
.job-history { display: flex; flex: 1; flex-direction: column; min-height: 0; padding: 16px 0 0; overflow: hidden; }
.job-history header { padding: 0 16px; align-items: flex-start; }
.job-history header p { margin: 5px 0 0; color: #87796c; font-size: 12px; }
.job-history header > span { padding: 4px 7px; background: #f5f0ea; border-radius: 5px; white-space: nowrap; }
.history-filters { display: grid; grid-template-columns: minmax(0, 1fr) minmax(130px, 0.75fr) auto; gap: 8px; padding: 0 16px 12px; }
.history-filters input { min-width: 0; height: 38px; padding: 0 10px; color: #51483f; background: #fff; border: 1px solid #ded3c8; border-radius: 7px; }
.history-filters > button { padding: 0 10px; white-space: nowrap; font-size: 12px; }
.job-history__list { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; border-top: 1px solid #eee6dd; }
.job-history__item { display: flex; align-items: center; gap: 10px; width: 100%; min-width: 0; padding: 12px 15px; text-align: left; background: transparent; border: 0; border-bottom: 1px solid #eee6dd; border-left: 3px solid transparent; border-radius: 0; }
.job-history__item.selected { background: #fff4e8; border-left-color: #bb6c23; }
.job-history__icon { display: grid; place-items: center; flex: none; width: 30px; height: 30px; color: #8c5cba; background: #efe8fa; border-radius: 7px; font-weight: 700; }
.job-history__info { display: grid; flex: 1; min-width: 0; gap: 5px; }
.job-history__info strong, .job-history__info small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-history__info strong { color: #514238; font-size: 12px; }
.job-history__info small { color: #8c7e71; font-size: 11px; }
.job-history__status { display: grid; justify-items: end; gap: 6px; flex: none; font-size: 11px; }
.job-history__status b { padding: 3px 7px; color: #95601b; background: #fff2dd; border-radius: 10px; font-weight: 500; }
.job-history__status b.completed { color: #258556; background: #e5f4eb; }
.job-history__status b.failed { color: #b03b32; background: #fceae8; }
.job-history__status small { color: #a65a1c; }
.job-history__evaluation { padding-left: 54px; background: #fffbf7; }
.history-empty { padding: 12px; color: #897d71; font-size: 12px; }
.job-history footer { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; color: #8c7e71; font-size: 12px; }
.job-history footer button { min-height: 30px; padding: 0 8px; border: 0; background: transparent; }
.history-pagination { display: flex; align-items: center; gap: 5px; }
.history-pagination span { color: #8a7c6e; }
.result-panel > header { flex-wrap: wrap; }
.result-empty { display: grid; flex: 1; place-content: center; justify-items: center; gap: 10px; min-height: 0; color: #8c7e71; text-align: center; }
.result-empty strong { color: #66594d; font-size: 16px; }
.result-overview { display: grid; grid-template-columns: minmax(0, 2fr) minmax(230px, 1fr); gap: 14px; }
.overview-main, .overview-aside { min-width: 0; }
.overview-aside { display: grid; align-content: start; gap: 12px; }
.metric-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.metric-grid article { align-content: start; min-height: 104px; background: #fff; border: 1px solid #eee1d6; }
.metric-grid strong { color: #49382b; font-size: 22px; }
.metric-grid strong small { font-size: 12px; font-weight: 400; }
.metric-grid article > small { color: #8b7d70; font-size: 11px; line-height: 1.4; }
.section-title { margin: 20px 0 10px; font-size: 13px; }
.field-metrics > div { background: #fff; }
.field-metrics b { color: #278458; font-size: 12px; }
.field-progress { grid-column: 1 / -1; height: 5px; overflow: hidden; background: #f0e8df; border-radius: 8px; }
.field-progress i { display: block; height: 100%; background: linear-gradient(90deg, #ac611d, #e6a05c); border-radius: inherit; }
.overview-card { padding: 14px; background: #fff; border: 1px solid #eee1d6; border-radius: 9px; }
.overview-card h4 { margin: 0 0 10px; font-size: 13px; }
.overview-card p, .overview-card small { margin: 5px 0; color: #88796b; font-size: 12px; overflow-wrap: anywhere; }
.baseline-card { background: #fffaf5; }
.matching-overview { display: flex; align-items: center; gap: 16px; }
.matching-ring { display: grid; place-items: center; flex: none; width: 86px; height: 86px; background: conic-gradient(#2e9a68 var(--match-percent), #eee4da 0); border-radius: 50%; }
.matching-ring strong { display: grid; place-items: center; width: 68px; height: 68px; background: #fff; border-radius: 50%; font-size: 16px; }
.matching-legend { display: grid; gap: 7px; color: #796c60; font-size: 12px; }
.matching-legend i { display: inline-block; width: 7px; height: 7px; margin-right: 6px; background: #2e9a68; border-radius: 50%; }
.matching-legend .legend-ambiguous { background: #dbaa4c; }
.matching-legend .legend-unmatched { background: #d3c8bd; }
.aside-heading { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
.aside-heading button { min-height: 26px; padding: 0; border: 0; background: transparent; font-size: 12px; }
@media(max-width: 1450px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media(max-width: 1200px) { .result-overview { grid-template-columns: minmax(0, 1fr); } .overview-aside { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media(min-width: 1051px) and (max-height: 650px) {
  .cross-evaluation { height: auto; min-height: 100%; }
  .workspace-columns { flex: none; min-height: 420px; }
  .workspace-results .result-panel { min-height: 420px; }
}
@media(max-width: 1050px) {
  .cross-evaluation { height: auto; min-height: 100%; }
  .workspace-columns { flex: none; grid-template-columns: minmax(0,1fr); align-items: start; min-height: 0; }
  .workspace-sidebar, .workspace-results { display: grid; align-content: start; min-height: 0; overflow: visible; }
  .job-history { flex: none; }
  .job-history__list { flex: none; overflow: visible; }
  .workspace-results .result-panel { height: auto; min-height: min(790px, calc(100dvh - 95px)); overflow: visible; }
  .result-scroll-area, .detail-list, .run-logs { flex: none; min-height: auto; overflow: visible; }
  .detail-list-shell { display: block; }
  .detail-search-toolbar { flex: 1 0 100%; justify-content: flex-end; min-width: 0; padding-bottom: 8px; }
}
@media(max-width: 650px) { .cross-evaluation { padding: 10px; } .cross-evaluation__header { align-items: flex-start; flex-direction: column; } .baseline-manager { grid-template-columns: minmax(0,1fr); overflow: auto; } .baseline-manager__list { max-height: 190px; } .cross-evaluation__scope, .field-metrics, .mapping-grid, .overview-aside { grid-template-columns: minmax(0,1fr); } .history-filters { grid-template-columns: minmax(0,1fr) auto; } .history-filters input { grid-column: 1 / -1; } .metric-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } .detail-field { grid-template-columns: 80px minmax(0,1fr); } .detail-field em { text-align: left; } .detail-field small { grid-column: 1 / -1; } .import-form > label, .mapping-grid label { grid-template-columns: 100px minmax(0,1fr); } .history-panel header, .result-panel header { align-items: flex-start; flex-direction: column; } .result-exports { margin-left:0; } .detail-search-toolbar { width: 100%; } .detail-search-input { min-width: 0; } .detail-search-field { width: 132px; } }
</style>
