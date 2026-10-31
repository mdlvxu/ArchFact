<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  getActiveVerificationExperiment,
  getRegionCropContentUrl,
  getVerificationVersions,
} from '@/api/modules/extraction'
import { catalogMorphologyText, catalogTextureText } from '@/domain/catalog-records'
import { buildDataInsights, insightCategory, insightMaterial } from '@/domain/data-insights'
import { useI18n } from '@/i18n'
import type { ExtractionRecord } from '@/types/extraction'
import type { VerificationVersionSnapshot } from '@/types/verification'

const props = defineProps<{
  active: boolean
  jobId: string
  records: ExtractionRecord[]
  rawRecordCount: number
  requestedPages: number[]
  documentName: string
}>()

const emit = defineEmits<{
  'open-record': [record: ExtractionRecord]
}>()

const { isChinese, t } = useI18n()
const label = computed(() => isChinese.value ? {
  eyebrow: 'ARCHFACT · DATA INSIGHTS', title: '考古数据洞察',
  subtitle: '从器物卡片走向原始证据，让提取成果可统计、可追溯。',
  report: '当前报告', noReport: '尚未加载报告', refresh: '刷新校验数据',
  cards: '器物卡片', raw: '提取记录', pages: '涉及页码', linked: '跨页关联',
  overview: '器物画像', distribution: '器型分布', materialDistribution: '材质 / 质地分布',
  categoryDimension: '器型', materialDimension: '材质', distributionHint: '点击图表筛选下方器物卡片；材质按字段关键词归类',
  pageTrend: '页码提取趋势', pageTrendHint: '仅显示有器物卡片的页码；点击柱形可定位该页',
  evidenceCoverage: '证据覆盖', evidenceCoverageHint: '按器物卡片统计可用证据，不表示模型准确率',
  more: '展开其余类别', collapse: '收起类别', unknown: '未归类', pageTitle: '器物页码热力图', pageHint: '仅显示有器物卡片的页；颜色越深，该页卡片越多',
  cardsUnit: '件', typeUnit: '类', pageUnit: '页', noData: '当前任务暂无可分析的器物卡片。请先完成数据提取。',
  selectCategory: '全部器型', selectPage: '全部页码', clearFilter: '清除筛选',
  quality: '数据完整度', coverage: '结构化字段覆盖', coverageHint: '按当前器物卡片统计；文本推断值不计入，缺失值仍保留在分母中',
  field_artifact_id: '器物编号', field_category: '器型类别', field_material: '材质 / 质地',
  field_measurements: '尺寸', field_morphological_description: '形态描述',
  verification: '机器校验', version: '校验版本', noVersion: '尚无已保存的校验版本',
  sample: '抽样记录', passed: '通过', failed: '未通过', pending: '未完成 / 失效',
  verificationHint: '校验结果仅针对该版本的抽样记录，不代表全部器物的准确率。',
  failure: '问题类型', noFailure: '该版本暂无问题类型统计',
  evidence: '证据关联', evidenceHint: '选择器物，查看已保存的关联信息',
  cardsList: '关联器物', text: '文本证据', drawing: '器物线图', plate: '彩色版图',
  source: '来源页码', available: '已有证据', absent: '未记录', open: '在数据预览中查看完整关联 ›',
  emptyFilter: '当前筛选下没有器物卡片。', noId: '未标注编号',
  versionError: '暂时无法读取校验版本', pagePrefix: '第', pageSuffix: '页',
} : {
  eyebrow: 'ARCHFACT · DATA INSIGHTS', title: 'Archaeological Data Insights',
  subtitle: 'Explore extracted artifact cards and trace every result back to its evidence.',
  report: 'Current report', noReport: 'No report loaded', refresh: 'Refresh verification data',
  cards: 'Artifact cards', raw: 'Extraction records', pages: 'Source pages', linked: 'Cross-page links',
  overview: 'Artifact profile', distribution: 'Artifact types', materialDistribution: 'Material / texture',
  categoryDimension: 'Type', materialDimension: 'Material', distributionHint: 'Select a bar to filter cards; materials are grouped by stored field keywords',
  pageTrend: 'Extraction by page', pageTrendHint: 'Pages with cards only; select a bar to filter',
  evidenceCoverage: 'Evidence coverage', evidenceCoverageHint: 'Share of cards with saved evidence, not model accuracy',
  more: 'Show other types', collapse: 'Show fewer types', unknown: 'Unclassified', pageTitle: 'Artifact page heatmap', pageHint: 'Pages with cards only; darker tiles have more cards',
  cardsUnit: 'cards', typeUnit: 'types', pageUnit: 'pages', noData: 'No artifact cards to analyze yet. Finish an extraction first.',
  selectCategory: 'All types', selectPage: 'All pages', clearFilter: 'Clear filters',
  quality: 'Data coverage', coverage: 'Structured field coverage', coverageHint: 'Text-derived values are excluded; missing values remain in the denominator',
  field_artifact_id: 'Artifact ID', field_category: 'Category', field_material: 'Material / texture',
  field_measurements: 'Measurements', field_morphological_description: 'Description',
  verification: 'Machine verification', version: 'Version', noVersion: 'No saved verification version yet',
  sample: 'Sample records', passed: 'Passed', failed: 'Failed', pending: 'Pending / stale',
  verificationHint: 'Verification covers this version’s sample, not the accuracy of every artifact.',
  failure: 'Issue types', noFailure: 'No issue breakdown for this version',
  evidence: 'Evidence links', evidenceHint: 'Select an artifact to see its saved links',
  cardsList: 'Matching artifacts', text: 'Text evidence', drawing: 'Line drawing', plate: 'Color plate',
  source: 'Source pages', available: 'Recorded', absent: 'Not recorded', open: 'Open the full evidence view ›',
  emptyFilter: 'No cards match the current filter.', noId: 'Unlabeled artifact',
  versionError: 'Could not load verification versions', pagePrefix: 'p.', pageSuffix: '',
})

const insights = computed(() => buildDataInsights(props.records, props.requestedPages))
const selectedCategory = ref('')
const selectedMaterial = ref('')
const selectedPage = ref<number | null>(null)
const selectedRecordId = ref('')
const chartDimension = ref<'category' | 'material'>('category')
const showAllCategories = ref(false)
const versions = ref<VerificationVersionSnapshot[]>([])
const selectedVersionId = ref('')
const versionsLoading = ref(false)
const versionsError = ref(false)
let versionRequest = 0

const selectedVersion = computed(() => versions.value.find((item) => item.id === selectedVersionId.value))
const report = computed(() => selectedVersion.value?.report)
const distributionGroups = computed(() => chartDimension.value === 'category'
  ? insights.value.categoryGroups : insights.value.materialGroups)
const visibleCategories = computed(() => showAllCategories.value
  ? distributionGroups.value : distributionGroups.value.slice(0, 8))
const remainingCategoryCount = computed(() => distributionGroups.value.slice(8)
  .reduce((total, item) => total + item.count, 0))
const categoryMax = computed(() => Math.max(...visibleCategories.value.map((item) => item.count), 1))
const activePageGroups = computed(() => insights.value.pageGroups.filter((item) => item.count > 0))
const maxPageCount = computed(() => Math.max(...activePageGroups.value.map((item) => item.count), 1))
const filteredRecords = computed(() => props.records.filter((record) =>
  (!selectedCategory.value || insightCategory(record) === selectedCategory.value) &&
  (!selectedMaterial.value || insightMaterial(record) === selectedMaterial.value) &&
  (selectedPage.value === null || record.source_pages.includes(selectedPage.value)),
))
const pageChartWidth = computed(() => Math.max(100, activePageGroups.value.length * 9))
const evidenceRings = computed(() => [
  { key: 'text', count: insights.value.withText, color: '#a76c42' },
  { key: 'drawing', count: insights.value.withDrawing, color: '#bb8e59' },
  { key: 'linked', count: insights.value.crossPage, color: '#768e80' },
])
const selectedRecord = computed(() => filteredRecords.value.find((record) => record.id === selectedRecordId.value)
  ?? filteredRecords.value[0])
const displayedRecords = computed(() => filteredRecords.value.slice(0, 7))
const sourcePages = computed(() => [...new Set([
  ...(selectedRecord.value?.source_pages ?? []),
  ...(selectedRecord.value?.associated_pages ?? []),
])].sort((a, b) => a - b))
const textQuote = computed(() => selectedRecord.value?.text_evidence?.find((item) => item.quote?.trim())?.quote?.trim()
  ?? Object.values(selectedRecord.value?.fields ?? {}).flatMap((field) => field.evidence)
    .find((item) => item.quote?.trim())?.quote?.trim() ?? '')
const drawingRegionId = computed(() => selectedRecord.value?.primary_artifact_region_id
  ?? selectedRecord.value?.thumbnail_region_id ?? '')
const drawingUrl = computed(() => props.jobId && drawingRegionId.value
  ? getRegionCropContentUrl(props.jobId, drawingRegionId.value) : '')
const artifactId = computed(() => {
  const field = selectedRecord.value?.fields.artifact_id
  return String(field?.value ?? field?.raw_value ?? selectedRecord.value?.linkage?.identity.artifact_id_normalized ?? '').trim()
    || label.value.noId
})
const passCount = computed(() => report.value?.pass_count ?? 0)
const failCount = computed(() => report.value?.fail_count ?? 0)
const pendingCount = computed(() => Math.max(0, (report.value?.sample_count ?? 0) - passCount.value - failCount.value))
const sampleCount = computed(() => report.value?.sample_count ?? 0)
const failureGroups = computed(() => {
  const distribution = report.value?.field_error_distribution
  if (distribution !== undefined) {
    return [...distribution].sort((a, b) => b.count - a.count).slice(0, 5)
  }
  const counts = new Map<string, number>()
  for (const item of selectedVersion.value?.items ?? []) {
    if (item.verdict !== 'failed') continue
    const key = item.failure_code ?? 'unclassified_failure'
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return [...counts].map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count).slice(0, 5)
})
const maxFailureCount = computed(() => Math.max(...failureGroups.value.map((item) => item.count), 1))
const failureLabelKeys: Record<string, string> = {
  artifact_id_missing: 'catalog.failure.artifactIdMissing',
  artifact_id_duplicate: 'catalog.failure.artifactIdDuplicate',
  artifact_id_evidence_conflict: 'catalog.failure.artifactIdConflict',
  sequence_crop_conflict: 'catalog.failure.sequenceCropConflict',
  figure_caption_missing: 'catalog.failure.captionMissing',
  figure_caption_evidence_conflict: 'catalog.failure.captionConflict',
  color_plate_relation_conflict: 'catalog.failure.colorPlateConflict',
  artifact_crop_missing: 'catalog.failure.artifactCropMissing',
  structured_measurements_error: 'catalog.failure.measurements',
  structured_classification_error: 'catalog.failure.classification',
  structured_field_evidence_conflict: 'catalog.failure.structuredConflict',
  text_evidence_conflict: 'catalog.failure.textEvidence',
  unclassified_failure: 'catalog.failure.unclassified',
  field_error: 'catalog.failure.field',
  text_evidence_error: 'catalog.failure.textEvidence',
  caption_match_error: 'catalog.failure.captionMatch',
  number_match_error: 'catalog.failure.numberMatch',
  artifact_crop_error: 'catalog.failure.artifactCrop',
  color_plate_error: 'catalog.failure.colorPlate',
  other: 'catalog.failure.other',
}

function failureLabel(key: string) {
  return failureLabelKeys[key] ? t(failureLabelKeys[key]) : key
}

function percentage(count: number, total: number) {
  return total > 0 ? Math.round(count / total * 100) : 0
}

function recordTitle(record: ExtractionRecord) {
  const field = record.fields.artifact_id
  return String(field?.value ?? field?.raw_value ?? record.linkage?.identity.artifact_id_normalized ?? '').trim()
    || label.value.noId
}

function chooseCategory(category: string) {
  if (chartDimension.value === 'material') {
    selectedMaterial.value = selectedMaterial.value === category ? '' : category
    selectedCategory.value = ''
  } else {
    selectedCategory.value = selectedCategory.value === category ? '' : category
    selectedMaterial.value = ''
  }
  selectedRecordId.value = ''
}

function selectDimension(dimension: 'category' | 'material') {
  chartDimension.value = dimension
  selectedCategory.value = ''
  selectedMaterial.value = ''
  showAllCategories.value = false
}

function choosePage(page: number) {
  selectedPage.value = selectedPage.value === page ? null : page
  selectedRecordId.value = ''
}

function clearFilters() {
  selectedCategory.value = ''
  selectedMaterial.value = ''
  selectedPage.value = null
  selectedRecordId.value = ''
}

async function loadVersions() {
  const request = ++versionRequest
  versions.value = []
  selectedVersionId.value = ''
  versionsError.value = false
  if (!props.active || !props.jobId) return
  versionsLoading.value = true
  try {
    const experiment = await getActiveVerificationExperiment(props.jobId)
    if (request !== versionRequest) return
    const result = await getVerificationVersions(props.jobId, { experimentId: experiment.id })
    if (request !== versionRequest) return
    versions.value = [...result].sort((a, b) => b.version - a.version)
    selectedVersionId.value = versions.value[0]?.id ?? ''
  } catch {
    if (request === versionRequest) versionsError.value = true
  } finally {
    if (request === versionRequest) versionsLoading.value = false
  }
}

watch(() => [props.active, props.jobId] as const, () => { void loadVersions() }, { immediate: true })
watch(() => props.records, () => { selectedRecordId.value = '' })
watch(() => insights.value.categoryGroups, (groups) => {
  if (!groups.some((item) => item.label !== '未归类') && insights.value.materialGroups.some((item) => item.label !== '未记录')) {
    chartDimension.value = 'material'
  }
}, { immediate: true })
</script>

<template>
  <div class="insights">
    <section class="insights-hero">
      <div class="insights-hero__top">
        <span class="insights-hero__eyebrow"><span class="insights-hero__diamond" />{{ label.eyebrow }}</span>
        <span class="insights-hero__seal">数</span>
      </div>
      <div class="insights-hero__body">
        <div>
          <h2>{{ label.title }}</h2>
          <p>{{ label.subtitle }}</p>
        </div>
        <div class="insights-hero__report">
          <span>{{ label.report }}</span>
          <strong :title="documentName">{{ documentName || label.noReport }}</strong>
        </div>
      </div>
      <div class="insights-hero__metrics">
        <div><strong>{{ records.length }}</strong><span>{{ label.cards }}</span></div>
        <div><strong>{{ rawRecordCount }}</strong><span>{{ label.raw }}</span></div>
        <div><strong>{{ insights.pageGroups.filter((item) => item.count > 0).length }}</strong><span>{{ label.pages }}</span></div>
        <div><strong>{{ insights.crossPage }}</strong><span>{{ label.linked }}</span></div>
      </div>
    </section>

    <div
      v-if="!records.length"
      class="insights-empty"
    >
      {{ label.noData }}
    </div>
    <template v-else>
      <div class="insights-section-title">
        <span>01</span><h3>{{ label.overview }}</h3>
      </div>
      <div class="insights-grid insights-grid--overview">
        <section class="insights-panel">
          <div class="insights-panel__heading">
            <div><h4>{{ chartDimension === 'category' ? label.distribution : label.materialDistribution }}</h4><p>{{ label.distributionHint }}</p></div>
            <div
              class="insights-dimension-switch"
              :aria-label="label.distribution"
            >
              <button
                type="button"
                :class="{ 'is-active': chartDimension === 'category' }"
                @click="selectDimension('category')"
              >
                {{ label.categoryDimension }}
              </button>
              <button
                type="button"
                :class="{ 'is-active': chartDimension === 'material' }"
                @click="selectDimension('material')"
              >
                {{ label.materialDimension }}
              </button>
            </div>
          </div>
          <div class="insights-bars">
            <button
              v-for="item in visibleCategories"
              :key="item.label"
              type="button"
              class="insights-bar"
              :class="{ 'insights-bar--active': (chartDimension === 'category' ? selectedCategory : selectedMaterial) === item.label }"
              @click="chooseCategory(item.label)"
            >
              <span class="insights-bar__name">{{ item.label === '未归类' ? label.unknown : item.label }}</span>
              <span class="insights-bar__track"><span :style="{ width: `${percentage(item.count, categoryMax)}%` }" /></span>
              <strong>{{ item.count }}</strong>
            </button>
            <button
              v-if="remainingCategoryCount"
              type="button"
              class="insights-bar insights-bar--other"
              @click="showAllCategories = !showAllCategories"
            >
              <span class="insights-bar__name">{{ showAllCategories ? label.collapse : label.more }}</span>
              <span class="insights-bar__track"><span
                v-if="!showAllCategories"
                :style="{ width: `${percentage(remainingCategoryCount, categoryMax)}%` }"
              /></span>
              <strong>{{ showAllCategories ? '⌃' : remainingCategoryCount }}</strong>
            </button>
          </div>
        </section>
        <section class="insights-panel">
          <div class="insights-panel__heading">
            <div><h4>{{ label.pageTrend }}</h4><p>{{ label.pageTrendHint }}</p></div><span class="insights-panel__count">{{ activePageGroups.length }} {{ label.pageUnit }}</span>
          </div>
          <div class="insights-page-trend">
            <div
              class="insights-page-trend__plot"
              :style="{ minWidth: `${pageChartWidth}px` }"
            >
              <button
                v-for="item in activePageGroups"
                :key="item.page"
                type="button"
                class="insights-page-trend__bar"
                :class="{ 'is-active': selectedPage === item.page }"
                :style="{ height: `${Math.max(4, percentage(item.count, maxPageCount))}%` }"
                :title="`${label.pagePrefix}${item.page}${label.pageSuffix} · ${item.count} ${label.cardsUnit}`"
                :aria-label="`${label.pagePrefix}${item.page}${label.pageSuffix} · ${item.count} ${label.cardsUnit}`"
                @click="choosePage(item.page)"
              />
            </div>
          </div>
          <div class="insights-page-trend__axis">
            <span>{{ activePageGroups[0]?.page }}</span>
            <span>{{ label.pagePrefix }}{{ activePageGroups[Math.floor(activePageGroups.length / 2)]?.page }}{{ label.pageSuffix }}</span>
            <span>{{ activePageGroups[activePageGroups.length - 1]?.page }}</span>
          </div>
          <div class="insights-page-trend__summary">
            {{ label.pageTrend }} · {{ activePageGroups.length }} {{ label.pageUnit }} / {{ records.length }} {{ label.cardsUnit }}
          </div>
        </section>
        <section class="insights-panel">
          <div class="insights-panel__heading">
            <div><h4>{{ label.evidenceCoverage }}</h4><p>{{ label.evidenceCoverageHint }}</p></div>
          </div>
          <div class="insights-rings">
            <div
              v-for="item in evidenceRings"
              :key="item.key"
              class="insights-rings__item"
            >
              <div
                class="insights-rings__circle"
                :style="{ background: `conic-gradient(${item.color} ${percentage(item.count, records.length)}%, #eee5db 0)` }"
              >
                <span>{{ percentage(item.count, records.length) }}%</span>
              </div>
              <strong>{{ label[item.key] }}</strong>
              <small>{{ item.count }} / {{ records.length }} {{ label.cardsUnit }}</small>
            </div>
          </div>
        </section>
      </div>

      <div class="insights-section-title">
        <span>02</span><h3>{{ label.quality }}</h3>
      </div>
      <div class="insights-grid insights-grid--quality">
        <section class="insights-panel">
          <div class="insights-panel__heading">
            <div><h4>{{ label.pageTitle }}</h4><p>{{ label.pageHint }}</p></div><span class="insights-panel__count">{{ activePageGroups.length }} {{ label.pageUnit }}</span>
          </div>
          <div class="insights-page-map">
            <button
              v-for="item in activePageGroups"
              :key="item.page"
              type="button"
              class="insights-page"
              :class="{ 'insights-page--active': selectedPage === item.page }"
              :style="{ '--density': item.count ? (0.18 + 0.82 * item.count / maxPageCount).toFixed(2) : '0' }"
              :title="`${label.pagePrefix}${item.page}${label.pageSuffix} · ${item.count} ${label.cardsUnit}`"
              :aria-label="`${label.pagePrefix}${item.page}${label.pageSuffix} · ${item.count} ${label.cardsUnit}`"
              @click="choosePage(item.page)"
            >
              {{ item.page }}
            </button>
          </div>
          <div class="insights-page-map__legend">
            <span>0</span><span class="insights-page-map__scale" /><span>{{ maxPageCount }} {{ label.cardsUnit }}</span>
          </div>
        </section>
        <section class="insights-panel">
          <div class="insights-panel__heading">
            <div><h4>{{ label.coverage }}</h4><p>{{ label.coverageHint }}</p></div>
          </div>
          <div class="insights-coverage">
            <div
              v-for="item in insights.fields"
              :key="item.key"
              class="insights-coverage__row"
            >
              <span>{{ label[`field_${item.key}`] }}</span>
              <div class="insights-coverage__track">
                <span :style="{ width: `${percentage(item.count, item.total)}%` }" />
              </div>
              <strong>{{ percentage(item.count, item.total) }}%</strong>
            </div>
          </div>
        </section>
        <section class="insights-panel">
          <div class="insights-panel__heading">
            <div><h4>{{ label.verification }}</h4><p>{{ label.verificationHint }}</p></div><button
              class="insights-refresh"
              type="button"
              :disabled="versionsLoading"
              :title="label.refresh"
              @click="loadVersions"
            >
              ↻
            </button>
          </div>
          <div
            v-if="versionsError"
            class="insights-notice"
          >
            {{ label.versionError }}
          </div>
          <div
            v-else-if="!selectedVersion"
            class="insights-notice"
          >
            {{ versionsLoading ? '…' : label.noVersion }}
          </div>
          <template v-else>
            <label class="insights-version">{{ label.version }}
              <select v-model="selectedVersionId"><option
                v-for="item in versions"
                :key="item.id"
                :value="item.id"
              >{{ item.experiment_name ? `${item.experiment_name} · ` : '' }}V{{ item.version }}</option></select>
            </label>
            <div class="insights-verification-summary">
              <strong>{{ sampleCount }}</strong><span>{{ label.sample }}</span>
            </div>
            <div
              class="insights-verification-track"
              :aria-label="`${label.passed} ${passCount}, ${label.failed} ${failCount}, ${label.pending} ${pendingCount}`"
            >
              <span
                class="insights-verification-track__pass"
                :style="{ width: `${percentage(passCount, sampleCount)}%` }"
              />
              <span
                class="insights-verification-track__fail"
                :style="{ width: `${percentage(failCount, sampleCount)}%` }"
              />
              <span
                class="insights-verification-track__pending"
                :style="{ width: `${percentage(pendingCount, sampleCount)}%` }"
              />
            </div>
            <div class="insights-verification-legend">
              <span><i class="is-pass" />{{ label.passed }} <b>{{ passCount }}</b></span><span><i class="is-fail" />{{ label.failed }} <b>{{ failCount }}</b></span><span><i class="is-pending" />{{ label.pending }} <b>{{ pendingCount }}</b></span>
            </div>
            <div class="insights-failures">
              <h5>{{ label.failure }}</h5><p v-if="!failureGroups.length">
                {{ label.noFailure }}
              </p><div
                v-for="item in failureGroups"
                :key="item.key"
                class="insights-failures__row"
              >
                <span :title="failureLabel(item.key)">{{ failureLabel(item.key) }}</span><div><span :style="{ width: `${percentage(item.count, maxFailureCount)}%` }" /></div><strong>{{ item.count }}</strong>
              </div>
            </div>
          </template>
        </section>
      </div>

      <div class="insights-section-title">
        <span>03</span><h3>{{ label.evidence }}</h3><p>{{ label.evidenceHint }}</p>
      </div>
      <section class="insights-panel insights-explorer">
        <div class="insights-explorer__list">
          <div class="insights-panel__heading">
            <div><h4>{{ label.cardsList }}</h4><p>{{ selectedCategory || selectedMaterial || label.selectCategory }} · {{ selectedPage === null ? label.selectPage : `${label.pagePrefix}${selectedPage}${label.pageSuffix}` }}</p></div><button
              v-if="selectedCategory || selectedMaterial || selectedPage !== null"
              type="button"
              class="insights-clear"
              @click="clearFilters"
            >
              {{ label.clearFilter }}
            </button>
          </div>
          <div
            v-if="!filteredRecords.length"
            class="insights-notice"
          >
            {{ label.emptyFilter }}
          </div>
          <div
            v-else
            class="insights-explorer__cards"
          >
            <button
              v-for="record in displayedRecords"
              :key="record.id"
              type="button"
              class="insights-record"
              :class="{ 'insights-record--active': selectedRecord?.id === record.id }"
              @click="selectedRecordId = record.id"
            >
              <span class="insights-record__icon">{{ insightCategory(record).slice(0, 1) }}</span><span><strong>{{ recordTitle(record) }}</strong><small>{{ insightCategory(record) }} · {{ record.source_pages.map((page) => `${label.pagePrefix}${page}${label.pageSuffix}`).join('、') }}</small></span><span class="insights-record__arrow">›</span>
            </button>
          </div>
          <div
            v-if="filteredRecords.length > displayedRecords.length"
            class="insights-explorer__more"
          >
            + {{ filteredRecords.length - displayedRecords.length }} {{ label.cardsUnit }}
          </div>
        </div>
        <div
          v-if="selectedRecord"
          class="insights-evidence"
        >
          <div class="insights-evidence__heading">
            <div><span>{{ insightCategory(selectedRecord) }}</span><h4>{{ artifactId }}</h4><p>{{ catalogTextureText(selectedRecord) || catalogMorphologyText(selectedRecord) }}</p></div><button
              type="button"
              @click="emit('open-record', selectedRecord)"
            >
              {{ label.open }}
            </button>
          </div>
          <div class="insights-evidence__flow">
            <div class="insights-evidence__node">
              <span>01</span><strong>{{ label.text }}</strong><small>{{ textQuote ? label.available : label.absent }}</small>
            </div><span class="insights-evidence__connector">→</span>
            <div class="insights-evidence__node">
              <span>02</span><strong>{{ label.drawing }}</strong><small>{{ drawingRegionId ? label.available : label.absent }}</small>
            </div><span class="insights-evidence__connector">→</span>
            <div class="insights-evidence__node">
              <span>03</span><strong>{{ label.plate }}</strong><small>{{ selectedRecord.linkage?.visual_link.plate_no ? label.available : label.absent }}</small>
            </div>
          </div>
          <div class="insights-evidence__detail">
            <div
              v-if="drawingUrl"
              class="insights-evidence__image"
            >
              <img
                :src="drawingUrl"
                :alt="`${artifactId} ${label.drawing}`"
                loading="lazy"
              >
            </div><div class="insights-evidence__quote">
              <span>{{ label.text }}</span><p>{{ textQuote || label.absent }}</p><small>{{ label.source }} · {{ sourcePages.map((page) => `${label.pagePrefix}${page}${label.pageSuffix}`).join('、') || '—' }}</small>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped lang="scss">
.insights { width: 100%; min-width: 0; padding: 0 19px 42px; color: #352a23; }
.insights-hero { position: relative; overflow: hidden; padding: 25px 32px 0; color: #fff8ee; background: radial-gradient(circle at 87% 16%, #805939 0, transparent 31%), linear-gradient(115deg, #382b24, #5d3d2a 76%, #75482e); border: 1px solid #b78b64; border-radius: 19px; box-shadow: 0 15px 28px #664a3020; }
.insights-hero::after { position: absolute; right: -80px; top: -130px; width: 470px; height: 470px; border: 1px solid #e3c5a030; border-radius: 50%; box-shadow: 0 0 0 48px #e3c5a010, 0 0 0 95px #e3c5a00b; content: ''; pointer-events: none; }
.insights-hero__top, .insights-hero__body, .insights-hero__metrics { position: relative; z-index: 1; }
.insights-hero__top { display: flex; align-items: center; justify-content: space-between; }
.insights-hero__eyebrow { display: flex; align-items: center; gap: 10px; font-size: 11px; letter-spacing: .16em; color: #e7caa7; }
.insights-hero__diamond { width: 10px; height: 10px; border: 1px solid #e6c497; transform: rotate(45deg); }
.insights-hero__seal { display: grid; place-items: center; width: 31px; height: 31px; color: #ebc99e; border: 1px solid #d1a66b80; border-radius: 50%; font-family: serif; }
.insights-hero__body { display: flex; justify-content: space-between; align-items: end; gap: 25px; padding: 10px 0 23px; }
.insights-hero h2 { margin: 0 0 8px; font-size: clamp(26px, 2.4vw, 36px); font-weight: 680; letter-spacing: .04em; }
.insights-hero p { margin: 0; font-size: 13px; color: #f3d9b9; }
.insights-hero__report { display: grid; gap: 7px; max-width: 42%; text-align: right; }
.insights-hero__report span { font-size: 11px; color: #d5b99c; }
.insights-hero__report strong { overflow: hidden; font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.insights-hero__metrics { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid #ebc69b42; }
.insights-hero__metrics > div { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 14px 8px 17px; }
.insights-hero__metrics > div + div { border-left: 1px solid #ebc69b30; }
.insights-hero__metrics strong { font-size: 23px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.insights-hero__metrics span { font-size: 11px; color: #e6c5a2; }
.insights-section-title { display: flex; align-items: baseline; gap: 10px; margin: 27px 0 12px; }
.insights-section-title > span { font-size: 11px; font-weight: 700; color: #a55d2e; }
.insights-section-title h3 { margin: 0; font-size: 19px; }
.insights-section-title p { margin: 0; font-size: 12px; color: #9c8675; }
.insights-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.insights-grid--overview > .insights-panel { min-height: 300px; }
.insights-grid--quality > .insights-panel { min-height: 285px; }
.insights-panel { min-width: 0; padding: 19px 21px 20px; background: #fffdf9; border: 1px solid #ead9c7; border-radius: 15px; box-shadow: 0 5px 17px #71523c0b; }
.insights-panel__heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 18px; }
.insights-panel__heading h4 { margin: 0 0 5px; font-size: 16px; }
.insights-panel__heading p { margin: 0; font-size: 11px; color: #a28a78; }
.insights-panel__count { padding: 4px 9px; color: #98572d; background: #f9eee2; border-radius: 16px; font-size: 11px; white-space: nowrap; }
.insights-dimension-switch { display: flex; flex: 0 0 auto; gap: 3px; padding: 3px; background: #f3e9dd; border-radius: 8px; }
.insights-dimension-switch button { padding: 4px 8px; color: #967964; background: none; border: 0; border-radius: 6px; font-size: 11px; cursor: pointer; }
.insights-dimension-switch button.is-active { color: #814a29; background: #fffaf4; box-shadow: 0 1px 4px #7253381b; }
.insights-bars { display: grid; gap: 9px; }
.insights-bar { display: grid; grid-template-columns: minmax(75px, 110px) minmax(30px, 1fr) 38px; align-items: center; gap: 12px; width: 100%; padding: 3px 5px; background: none; border: 0; border-radius: 7px; color: #634b3a; text-align: left; cursor: pointer; }
.insights-bar:hover, .insights-bar--active { background: #f8ede1; }
.insights-bar__name { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.insights-bar__track { overflow: hidden; height: 13px; background: #f3ebe4; border-radius: 3px; }
.insights-bar__track > span { display: block; height: 100%; min-width: 3px; background: linear-gradient(90deg, #b77847, #dcaf78); border-radius: inherit; transition: width .35s ease; }
.insights-bar--active .insights-bar__track > span { background: #844821; }
.insights-bar strong { font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }
.insights-bar--other { opacity: .78; }
.insights-page-trend { position: relative; height: 170px; padding: 12px 0 0; overflow-x: auto; background: repeating-linear-gradient(to top, transparent 0, transparent 39px, #ecdfd1 40px, transparent 41px); border-bottom: 1px solid #cdb8a5; }
.insights-page-trend__plot { display: flex; align-items: end; gap: 2px; width: 100%; height: 100%; }
.insights-page-trend__bar { flex: 1 0 5px; min-width: 5px; max-width: 18px; padding: 0; background: linear-gradient(180deg, #dcaf77, #9b5d38); border: 0; border-radius: 3px 3px 0 0; cursor: pointer; transform-origin: bottom; animation: insights-rise .55s ease-out both; transition: filter .15s, background .15s; }
.insights-page-trend__bar:hover, .insights-page-trend__bar.is-active { background: #704329; filter: drop-shadow(0 0 4px #70432966); }
.insights-page-trend__axis { display: flex; justify-content: space-between; padding-top: 7px; color: #917a65; font-size: 11px; font-variant-numeric: tabular-nums; }
.insights-page-trend__summary { margin-top: 13px; color: #9b806b; font-size: 12px; }
.insights-rings { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: center; min-height: 215px; }
.insights-rings__item { display: flex; flex-direction: column; align-items: center; gap: 7px; text-align: center; }
.insights-rings__circle { display: grid; place-items: center; width: clamp(76px, 6vw, 120px); height: clamp(76px, 6vw, 120px); border-radius: 50%; }
.insights-rings__circle::before { grid-area: 1 / 1; width: 75%; height: 75%; background: #fffdf9; border-radius: 50%; content: ''; }
.insights-rings__circle span { z-index: 1; grid-area: 1 / 1; font-size: clamp(15px, 1.4vw, 23px); font-weight: 700; color: #66442e; font-variant-numeric: tabular-nums; }
.insights-rings__item strong { font-size: 13px; }
.insights-rings__item small { color: #9c856f; font-size: 11px; }
@keyframes insights-rise { from { transform: scaleY(.06); opacity: .45; } to { transform: scaleY(1); opacity: 1; } }
.insights-page-map { display: grid; grid-template-columns: repeat(auto-fill, minmax(33px, 1fr)); align-content: start; gap: 6px; min-height: 182px; max-height: 255px; padding: 2px; overflow: auto; }
.insights-page { height: 31px; color: #71523d; background: #f4ece4; border: 1px solid #e7d5c1; border-radius: 5px; font-size: 10px; cursor: pointer; font-variant-numeric: tabular-nums; }
.insights-page[style*="--density"] { background: rgb(170 100 51 / calc(var(--density) * .8)); }
.insights-page:hover, .insights-page--active { outline: 2px solid #8f4e24; outline-offset: 1px; }
.insights-page-map__legend { display: flex; align-items: center; gap: 8px; margin-top: 15px; font-size: 10px; color: #9b8473; }
.insights-page-map__scale { width: 95px; height: 8px; background: linear-gradient(90deg, #f4ece4, #9a592d); border-radius: 4px; }
.insights-coverage { display: grid; gap: 17px; padding: 5px 0 2px; }
.insights-coverage__row { display: grid; grid-template-columns: 96px 1fr 42px; align-items: center; gap: 12px; font-size: 12px; }
.insights-coverage__row > span { color: #69503d; }
.insights-coverage__row strong { font-variant-numeric: tabular-nums; text-align: right; color: #97592f; }
.insights-coverage__track { height: 10px; background: #f2e9df; border-radius: 10px; overflow: hidden; }
.insights-coverage__track > span { display: block; height: 100%; background: linear-gradient(90deg, #99613c, #d5a26b); border-radius: inherit; }
.insights-refresh, .insights-clear { color: #9a5a30; background: #fbf3ea; border: 1px solid #e8d4bd; border-radius: 7px; cursor: pointer; }
.insights-refresh { width: 29px; height: 29px; font-size: 19px; line-height: 1; }
.insights-clear { padding: 5px 8px; font-size: 11px; white-space: nowrap; }
.insights-version { display: flex; align-items: center; gap: 9px; color: #927b68; font-size: 11px; }
.insights-version select { max-width: 210px; padding: 4px 8px; color: #644630; background: #fbf7f1; border: 1px solid #e7d8c7; border-radius: 6px; }
.insights-verification-summary { display: flex; align-items: baseline; gap: 8px; margin-top: 14px; }
.insights-verification-summary strong { font-size: 27px; font-variant-numeric: tabular-nums; }
.insights-verification-summary span { color: #947b68; font-size: 11px; }
.insights-verification-track { display: flex; overflow: hidden; height: 13px; margin-top: 8px; background: #eee8e1; border-radius: 8px; }
.insights-verification-track span { height: 100%; }
.insights-verification-track__pass, .is-pass { background: #718f72; }
.insights-verification-track__fail, .is-fail { background: #b66b51; }
.insights-verification-track__pending, .is-pending { background: #d6c8b8; }
.insights-verification-legend { display: flex; flex-wrap: wrap; gap: 8px 17px; margin-top: 10px; }
.insights-verification-legend span { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #816b5a; }
.insights-verification-legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
.insights-verification-legend b { color: #4e3b2e; }
.insights-failures { margin-top: 18px; padding-top: 14px; border-top: 1px solid #efe4d9; }
.insights-failures h5 { margin: 0 0 10px; font-size: 12px; }
.insights-failures p { color: #ad9786; font-size: 11px; }
.insights-failures__row { display: grid; grid-template-columns: 120px 1fr 25px; gap: 8px; align-items: center; margin: 6px 0; color: #856e5b; font-size: 10px; }
.insights-failures__row > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.insights-failures__row > div { height: 6px; background: #f4ede6; border-radius: 4px; }
.insights-failures__row > div span { display: block; height: 100%; background: #b88361; border-radius: inherit; }
.insights-failures__row strong { text-align: right; }
.insights-explorer { display: grid; grid-template-columns: minmax(240px, 34%) minmax(0, 1fr); gap: 19px; }
.insights-explorer__list { min-width: 0; padding-right: 18px; border-right: 1px solid #eee1d3; }
.insights-explorer__cards { display: grid; gap: 6px; max-height: 340px; overflow: auto; }
.insights-record { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px; color: #624731; background: transparent; border: 1px solid transparent; border-radius: 9px; text-align: left; cursor: pointer; }
.insights-record:hover, .insights-record--active { background: #f9f0e6; border-color: #e9d5bf; }
.insights-record__icon { display: grid; flex: 0 0 36px; place-items: center; width: 36px; height: 36px; color: #a26135; background: #f1e4d4; border-radius: 8px; font-family: serif; }
.insights-record > span:nth-child(2) { display: grid; gap: 3px; min-width: 0; flex: 1; }
.insights-record strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.insights-record small { overflow: hidden; color: #9d8572; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.insights-record__arrow { font-size: 20px; color: #ad8e74; }
.insights-explorer__more { padding: 10px; font-size: 11px; color: #ae9179; text-align: center; }
.insights-evidence { min-width: 0; }
.insights-evidence__heading { display: flex; justify-content: space-between; gap: 12px; }
.insights-evidence__heading > div { min-width: 0; }
.insights-evidence__heading span { color: #ad7047; font-size: 11px; }
.insights-evidence__heading h4 { margin: 2px 0; font-size: 21px; }
.insights-evidence__heading p { overflow: hidden; margin: 0; color: #927a68; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.insights-evidence__heading button { align-self: start; padding: 7px 9px; color: #985526; background: #f8ede0; border: 1px solid #e7d0b6; border-radius: 7px; font-size: 11px; cursor: pointer; white-space: nowrap; }
.insights-evidence__flow { display: flex; align-items: center; gap: 8px; margin: 20px 0; }
.insights-evidence__node { display: grid; gap: 4px; flex: 1; min-width: 0; padding: 12px 11px; background: #faf5ed; border: 1px solid #ebd9c4; border-radius: 10px; }
.insights-evidence__node > span { color: #b48158; font-size: 10px; }
.insights-evidence__node strong { font-size: 12px; }
.insights-evidence__node small { color: #997961; font-size: 10px; }
.insights-evidence__connector { color: #bc8b60; font-size: 18px; }
.insights-evidence__detail { display: flex; gap: 15px; min-height: 155px; }
.insights-evidence__image { display: grid; flex: 0 0 135px; place-items: center; height: 155px; background: #f8f4ee; border: 1px solid #ece0d1; border-radius: 9px; }
.insights-evidence__image img { max-width: 100%; max-height: 100%; object-fit: contain; }
.insights-evidence__quote { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; padding: 13px; background: #fbf8f2; border-radius: 9px; }
.insights-evidence__quote span { color: #aa7049; font-size: 10px; }
.insights-evidence__quote p { display: -webkit-box; overflow: hidden; margin: 0; color: #5c493b; font-size: 12px; line-height: 1.65; -webkit-line-clamp: 4; -webkit-box-orient: vertical; }
.insights-evidence__quote small { margin-top: auto; color: #a3907f; font-size: 10px; }
.insights-notice, .insights-empty { display: grid; place-items: center; min-height: 155px; color: #a38b77; font-size: 12px; text-align: center; }
.insights-empty { min-height: 360px; }
@media (max-width: 1450px) { .insights-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .insights-grid > :last-child { grid-column: 1 / -1; } }
@media (max-width: 980px) { .insights-grid, .insights-explorer { grid-template-columns: 1fr; } .insights-grid > :last-child { grid-column: auto; } .insights-explorer__list { padding-right: 0; padding-bottom: 16px; border-right: 0; border-bottom: 1px solid #eee1d3; } }
@media (max-width: 600px) { .insights { padding-inline: 12px; } .insights-hero { padding: 19px 18px 0; } .insights-hero__body { display: block; } .insights-hero__report { max-width: 100%; margin-top: 18px; text-align: left; } .insights-hero__metrics strong { font-size: 18px; } .insights-hero__metrics span { font-size: 9px; } .insights-panel { padding: 15px; } .insights-evidence__flow { gap: 4px; } .insights-evidence__node { padding: 9px 6px; } .insights-evidence__node strong { font-size: 10px; } .insights-evidence__image { flex-basis: 90px; } }
@media (prefers-reduced-motion: reduce) { .insights-bar__track > span { transition: none; } .insights-page-trend__bar { animation: none; } }
</style>
