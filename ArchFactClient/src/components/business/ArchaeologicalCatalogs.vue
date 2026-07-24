<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getRegionCropContentUrl } from '@/api/modules/extraction'
import { useI18n } from '@/i18n'
import type { ExtractionRecord } from '@/types/extraction'
import type { PdfPageItem } from '@/types/pdf'
import type {
  VerificationFailureCode,
  VerificationItem,
} from '@/types/verification'

interface Props {
  records: ExtractionRecord[]
  pages: PdfPageItem[]
  selectedPage: number | null
  selectedRecordId?: string
  activeAnnotationId?: string
  savingReviewId?: string
  mode?: 'browse' | 'verify'
  jobId?: string
  reviewStatuses?: Record<string, ExtractionRecord['review_status']>
  reviewItems?: Record<string, VerificationItem>
  staleItems?: VerificationItem[]
  matchingVersionId?: string
  detailsOpen?: boolean
  reviewLocked?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  selectedRecordId: '',
  activeAnnotationId: '',
  savingReviewId: '',
  mode: 'browse',
  jobId: '',
  reviewStatuses: () => ({}),
  reviewItems: () => ({}),
  staleItems: () => [],
  matchingVersionId: 'M0',
  detailsOpen: true,
  reviewLocked: false,
})
const { localize, t } = useI18n()

const emit = defineEmits<{
  selectRecord: [record: ExtractionRecord]
  review: [
    record: ExtractionRecord,
    status: 'passed' | 'failed',
    failureCode?: VerificationFailureCode,
    failureReason?: string,
  ]
}>()

const searchText = ref('')
type CatalogFilter =
  | 'all'
  | 'artifact_id'
  | 'category'
  | 'surface_color'
  | 'texture'
  | 'measurements'
  | 'morphological_description'
  | 'figure_caption'
  | 'completeness'
  | 'page'
  | 'status:unreviewed'
  | 'status:passed'
  | 'status:failed'
  | 'status:stale'

const catalogFilter = ref<CatalogFilter>('all')
const catalogScrollRef = ref<HTMLElement>()
const filterMenuRef = ref<HTMLDetailsElement>()
const failureMenuRef = ref<HTMLDetailsElement | HTMLDetailsElement[]>()
const failureEditorRecordId = ref('')
const failureCode = ref<VerificationFailureCode>('other')
const failureReason = ref('')

const failureOptions: Array<{ value: VerificationFailureCode; labelKey: string }> = [
  { value: 'field_error', labelKey: 'catalog.failure.field' },
  { value: 'text_evidence_error', labelKey: 'catalog.failure.textEvidence' },
  { value: 'caption_match_error', labelKey: 'catalog.failure.captionMatch' },
  { value: 'number_match_error', labelKey: 'catalog.failure.numberMatch' },
  { value: 'artifact_crop_error', labelKey: 'catalog.failure.artifactCrop' },
  { value: 'color_plate_error', labelKey: 'catalog.failure.colorPlate' },
  { value: 'other', labelKey: 'catalog.failure.other' },
]

const activeFailureLabel = computed(() => {
  const option = failureOptions.find((item) => item.value === failureCode.value)
  return option ? t(option.labelKey) : t('catalog.failure.other')
})

function closeFailureMenu() {
  const menu = Array.isArray(failureMenuRef.value)
    ? failureMenuRef.value[0]
    : failureMenuRef.value
  menu?.removeAttribute('open')
}

function selectFailureCode(value: VerificationFailureCode) {
  failureCode.value = value
  closeFailureMenu()
}

function openFailureEditor(record: ExtractionRecord) {
  closeFailureMenu()
  const previous = props.reviewItems[record.id]
  failureEditorRecordId.value = record.id
  failureCode.value = previous?.failure_code ?? 'other'
  failureReason.value = previous?.failure_reason ?? ''
}

function closeFailureEditor() {
  closeFailureMenu()
  failureEditorRecordId.value = ''
}

function submitFailure(record: ExtractionRecord) {
  emit('review', record, 'failed', failureCode.value, failureReason.value.trim())
  closeFailureEditor()
}

const fieldFilterOptions: Array<{ value: CatalogFilter; labelKey: string }> = [
  { value: 'all', labelKey: 'catalog.filterAll' },
  { value: 'artifact_id', labelKey: 'catalog.filterArtifactId' },
  { value: 'category', labelKey: 'catalog.filterCategory' },
  { value: 'surface_color', labelKey: 'catalog.filterSurfaceColor' },
  { value: 'texture', labelKey: 'catalog.filterTexture' },
  { value: 'measurements', labelKey: 'catalog.filterMeasurements' },
  { value: 'morphological_description', labelKey: 'catalog.filterMorphology' },
  { value: 'figure_caption', labelKey: 'catalog.filterFigureCaption' },
  { value: 'completeness', labelKey: 'catalog.filterCompleteness' },
  { value: 'page', labelKey: 'catalog.filterPage' },
]

const statusFilterOptions: Array<{ value: CatalogFilter; labelKey: string }> = [
  { value: 'status:unreviewed', labelKey: 'catalog.unreviewed' },
  { value: 'status:passed', labelKey: 'catalog.passed' },
  { value: 'status:failed', labelKey: 'catalog.failed' },
  { value: 'status:stale', labelKey: 'catalog.stale' },
]

const activeFilterLabel = computed(() => {
  const option = [...fieldFilterOptions, ...statusFilterOptions]
    .find((item) => item.value === catalogFilter.value)
  return option ? t(option.labelKey) : t('catalog.filterAll')
})

function closeFilterMenu() {
  filterMenuRef.value?.removeAttribute('open')
}

function selectCatalogFilter(value: CatalogFilter) {
  catalogFilter.value = value
  closeFilterMenu()
}

function closeFilterMenuOnOutsideClick(event: PointerEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  if (!filterMenuRef.value?.contains(target)) closeFilterMenu()
  const failureMenu = Array.isArray(failureMenuRef.value)
    ? failureMenuRef.value[0]
    : failureMenuRef.value
  if (!failureMenu?.contains(target)) closeFailureMenu()
}

onMounted(() => document.addEventListener('pointerdown', closeFilterMenuOnOutsideClick))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeFilterMenuOnOutsideClick))

const fieldFilterKeys: Partial<Record<CatalogFilter, string[]>> = {
  artifact_id: ['artifact_id', 'context_id'],
  category: ['category', 'type', 'subtype'],
  surface_color: ['surface_color'],
  texture: ['texture', 'material'],
  measurements: ['measurements', 'depth'],
  morphological_description: ['morphological_description', 'shape'],
  figure_caption: ['figure_caption'],
  completeness: ['completeness'],
}

const scopedRecords = computed(() => {
  // 考古目录始终展示当前模式的完整记录集合；选中PDF页只影响中间预览和详情焦点。
  return props.records
})

const filteredRecords = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return scopedRecords.value.filter((record) => {
    const status = catalogFilter.value.startsWith('status:')
      ? catalogFilter.value.slice('status:'.length) as ExtractionRecord['review_status']
      : null
    if (props.mode === 'verify' && status && recordStatus(record) !== status) return false
    if (!keyword) return true

    let values: unknown[]
    if (catalogFilter.value === 'page') {
      values = record.source_pages
    } else {
      const fieldKeys = fieldFilterKeys[catalogFilter.value]
      const fields = fieldKeys
        ? fieldKeys.flatMap((key) => record.fields[key] ? [record.fields[key]!] : [])
        : Object.values(record.fields)
      values = [
        ...(fieldKeys ? [] : [record.record_type, ...record.source_pages]),
        ...fields.flatMap((field) => [field.raw_value, field.value]),
      ]
    }

    return values
      .filter((value) => value !== null && value !== undefined && value !== '')
      .map((value) => typeof value === 'object' ? JSON.stringify(value) : String(value))
      .join(' ')
      .toLowerCase()
      .includes(keyword)
  })
})

const visibleStaleItems = computed(() => {
  if (props.mode !== 'verify') return []
  if (catalogFilter.value.startsWith('status:') && catalogFilter.value !== 'status:stale') return []
  if (!['all', 'status:stale'].includes(catalogFilter.value)) return []
  const keyword = searchText.value.trim().toLowerCase()
  return props.staleItems.filter((item) =>
    !keyword || `${item.record_id} ${item.failure_reason}`.toLowerCase().includes(keyword),
  )
})

watch(
  () => props.mode,
  (mode) => {
    if (mode === 'browse' && catalogFilter.value.startsWith('status:')) {
      catalogFilter.value = 'all'
    }
  },
)

const selectedRecord = computed(
  () => scopedRecords.value.find((record) => record.id === props.selectedRecordId),
)

watch(
  () => [props.selectedRecordId, props.activeAnnotationId] as const,
  async ([recordId]) => {
    if (!recordId) return
    await nextTick()
    const entry = Array.from(
      catalogScrollRef.value?.querySelectorAll<HTMLElement>('[data-record-id]') ?? [],
    ).find((element) => element.dataset.recordId === recordId)
    entry?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  },
  { immediate: true },
)

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function humanizeKey(key: string) {
  return localize(key
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' '))
}

const detailTrailingKeys = [
  'category',
  'completeness',
  'morphological_description',
  'figure_caption',
] as const

function orderedDetailFields(record: ExtractionRecord) {
  const trailingKeys = new Set<string>(detailTrailingKeys)
  const entries = Object.entries(record.fields).map(([key, field]) => ({ key, field }))

  return [
    ...entries.filter(({ key }) => !trailingKeys.has(key)),
    ...detailTrailingKeys.flatMap((key) => {
      const field = record.fields[key]
      return field ? [{ key, field }] : []
    }),
  ]
}

function detailFieldClass(key: string) {
  return {
    'verification-field--category': key === 'category',
    'verification-field--completeness': key === 'completeness',
    'verification-field--wide': key === 'morphological_description' || key === 'figure_caption',
    'verification-field--description': key === 'morphological_description',
    'verification-field--figure': key === 'figure_caption',
  }
}

function fieldValue(record: ExtractionRecord, keys: string[]) {
  for (const key of keys) {
    const value = record.fields[key]?.value
    if (value !== null && value !== undefined && value !== '') return displayValue(value)
  }
  return ''
}

function recordTitle(record: ExtractionRecord) {
  return (
    fieldValue(record, ['artifact_id', 'context_id', 'figure_caption']) ||
    localize(humanizeKey(record.record_type))
  )
}

function recordSubtitle(record: ExtractionRecord) {
  return fieldValue(record, ['category', 'type', 'subtype']) || t('common.page', { page: record.source_pages[0] ?? '—' })
}

function recordTexture(record: ExtractionRecord) {
  return fieldValue(record, ['texture', 'surface_color', 'material', 'page_text'])
}

function recordMeasurements(record: ExtractionRecord) {
  return fieldValue(record, ['measurements', 'depth', 'completeness'])
}

function pageThumbnail(record: ExtractionRecord) {
  if (props.jobId && record.thumbnail_region_id) {
    return getRegionCropContentUrl(props.jobId, record.thumbnail_region_id)
  }
  const evidenceRegion = Object.values(record.fields)
    .flatMap((field) => field.evidence)
    .find((evidence) =>
      Boolean(evidence.region_id) &&
      ['artifact', 'line_drawing', 'color_plate', 'grave_drawing'].includes(evidence.kind ?? ''),
    )
  const regionId = evidenceRegion?.region_id
  if (props.jobId && regionId) return getRegionCropContentUrl(props.jobId, regionId)
  // 器物目录只能展示区域裁剪图；没有匹配区域时使用占位图，避免把整页 PDF
  // 误认为某一件器物的图片。
  return ''
}

function recordStatus(record: ExtractionRecord): ExtractionRecord['review_status'] {
  return props.reviewStatuses[record.id] ?? record.review_status
}

function recordVerificationItem(record: ExtractionRecord) {
  return props.reviewItems[record.id]
}
</script>

<template>
  <aside class="catalogs panel">
    <header
      class="catalogs__header"
      :class="{ 'catalogs__header--browse': mode === 'browse' }"
    >
      <h2>{{ t('catalog.title') }}</h2>
      <label class="search-box">
        <span aria-hidden="true">⌕</span>
        <input
          v-model="searchText"
          type="search"
          :placeholder="t('catalog.search')"
          :aria-label="t('catalog.searchAria')"
        >
      </label>
      <details
        ref="filterMenuRef"
        class="filter-menu"
        @keydown.esc.stop="closeFilterMenu"
      >
        <summary :aria-label="t('catalog.filterAria')">
          <span class="filter-menu__current">{{ activeFilterLabel }}</span>
          <svg
            viewBox="0 0 20 20"
            aria-hidden="true"
          >
            <path d="M4 5h12M6.5 10h7M9 15h2" />
          </svg>
        </summary>
        <div
          class="filter-menu__popover"
          role="listbox"
          :aria-label="t('catalog.filterAria')"
        >
          <p class="filter-menu__group-title">
            {{ t('catalog.filterFields') }}
          </p>
          <button
            v-for="option in fieldFilterOptions"
            :key="option.value"
            type="button"
            role="option"
            class="filter-menu__option"
            :class="{ 'filter-menu__option--active': catalogFilter === option.value }"
            :aria-selected="catalogFilter === option.value"
            :data-filter-value="option.value"
            @click="selectCatalogFilter(option.value)"
          >
            <span>{{ t(option.labelKey) }}</span>
            <b aria-hidden="true">{{ catalogFilter === option.value ? '✓' : '' }}</b>
          </button>
          <template v-if="mode === 'verify'">
            <p class="filter-menu__group-title filter-menu__group-title--divided">
              {{ t('catalog.filterStatus') }}
            </p>
            <button
              v-for="option in statusFilterOptions"
              :key="option.value"
              type="button"
              role="option"
              class="filter-menu__option"
              :class="{ 'filter-menu__option--active': catalogFilter === option.value }"
              :aria-selected="catalogFilter === option.value"
              :data-filter-value="option.value"
              @click="selectCatalogFilter(option.value)"
            >
              <span>{{ t(option.labelKey) }}</span>
              <b aria-hidden="true">{{ catalogFilter === option.value ? '✓' : '' }}</b>
            </button>
          </template>
        </div>
      </details>
    </header>

    <div
      ref="catalogScrollRef"
      class="catalogs__scroll"
    >
      <div
        v-if="filteredRecords.length || visibleStaleItems.length"
        class="catalog-list"
      >
        <div
          v-for="(record, recordIndex) in filteredRecords"
          :key="record.id"
          class="catalog-entry"
          :class="{
            'catalog-entry--first-selected':
              recordIndex === 0 && selectedPage !== null && record.id === selectedRecord?.id,
          }"
          :data-record-id="record.id"
        >
          <section
            v-if="detailsOpen && selectedPage !== null && record.id === selectedRecord?.id"
            class="verification-panel verification-panel--inline"
          >
            <div class="verification-panel__header">
              <h3>{{ mode === 'verify' ? t('catalog.verification') : t('catalog.details') }}</h3>
              <div
                v-if="mode === 'verify'"
                class="review-actions"
              >
                <button
                  type="button"
                  class="review-button review-button--pass"
                  :class="{ 'review-button--selected': recordStatus(record) === 'passed' }"
                  :disabled="savingReviewId === record.id || reviewLocked"
                  @click="emit('review', record, 'passed')"
                >
                  {{ t('catalog.pass') }}
                </button>
                <button
                  type="button"
                  class="review-button review-button--fail"
                  :class="{ 'review-button--selected': recordStatus(record) === 'failed' }"
                  :disabled="savingReviewId === record.id || reviewLocked"
                  @click="openFailureEditor(record)"
                >
                  {{ t('catalog.fail') }}
                </button>
              </div>
            </div>

            <div
              v-if="mode === 'verify'"
              class="verification-context"
              :class="{ 'verification-context--changed': recordVerificationItem(record)?.relation_changed }"
            >
              <span>{{ t('catalog.matchingVersion') }} {{ matchingVersionId }}</span>
              <strong v-if="recordVerificationItem(record)?.relation_changed">
                {{ t('catalog.relationChanged') }}
              </strong>
            </div>

            <div
              v-if="mode === 'verify' && recordVerificationItem(record)?.ai_verdict"
              class="ai-review-result"
              :class="{
                'ai-review-result--conflict': recordVerificationItem(record)?.consensus_status === 'conflict',
                'ai-review-result--agreed': recordVerificationItem(record)?.consensus_status === 'agreed',
              }"
            >
              <div>
                <strong>
                  {{ t('catalog.aiReview') }} ·
                  {{ t(`catalog.ai.${recordVerificationItem(record)?.ai_verdict}`) }}
                </strong>
                <span v-if="recordVerificationItem(record)?.ai_confidence != null">
                  {{ Math.round((recordVerificationItem(record)?.ai_confidence ?? 0) * 100) }}%
                </span>
                <b v-if="recordVerificationItem(record)?.consensus_status === 'conflict'">
                  {{ t('catalog.ai.conflict') }}
                </b>
              </div>
              <p>{{ recordVerificationItem(record)?.ai_reason }}</p>
            </div>

            <form
              v-if="mode === 'verify' && failureEditorRecordId === record.id"
              class="failure-editor"
              @submit.prevent="submitFailure(record)"
            >
              <div class="failure-editor__field">
                <span>{{ t('catalog.failure.type') }}</span>
                <details
                  ref="failureMenuRef"
                  class="failure-select"
                  @keydown.esc.stop="closeFailureMenu"
                >
                  <summary :aria-label="t('catalog.failure.type')">
                    <span>{{ activeFailureLabel }}</span>
                    <svg
                      viewBox="0 0 20 20"
                      aria-hidden="true"
                    >
                      <path d="m5 7.5 5 5 5-5" />
                    </svg>
                  </summary>
                  <div
                    class="failure-select__menu"
                    role="listbox"
                    :aria-label="t('catalog.failure.type')"
                  >
                    <button
                      v-for="option in failureOptions"
                      :key="option.value"
                      type="button"
                      role="option"
                      class="failure-select__option"
                      :class="{ 'failure-select__option--active': failureCode === option.value }"
                      :aria-selected="failureCode === option.value"
                      :data-failure-code="option.value"
                      @click="selectFailureCode(option.value)"
                    >
                      <span>{{ t(option.labelKey) }}</span>
                      <b aria-hidden="true">{{ failureCode === option.value ? '✓' : '' }}</b>
                    </button>
                  </div>
                </details>
              </div>
              <label class="failure-editor__note">
                <span>{{ t('catalog.failure.note') }}</span>
                <input
                  v-model="failureReason"
                  type="text"
                  maxlength="500"
                  :placeholder="t('catalog.failure.notePlaceholder')"
                >
              </label>
              <div class="failure-editor__actions">
                <button
                  type="button"
                  @click="closeFailureEditor"
                >
                  {{ t('common.cancel') }}
                </button>
                <button
                  type="submit"
                  class="failure-editor__confirm"
                  :disabled="savingReviewId === record.id || reviewLocked"
                >
                  {{ t('catalog.failure.confirm') }}
                </button>
              </div>
            </form>

            <dl class="verification-fields">
              <div
                v-for="entry in orderedDetailFields(record)"
                :key="entry.key"
                :class="detailFieldClass(entry.key)"
              >
                <dt>{{ humanizeKey(entry.key) }}</dt>
                <dd
                  v-if="entry.key === 'figure_caption'"
                  class="verification-figure"
                >
                  <img
                    v-if="pageThumbnail(record)"
                    :src="pageThumbnail(record)"
                    :alt="displayValue(entry.field.value)"
                  >
                  <span>{{ displayValue(entry.field.value) }}</span>
                </dd>
                <dd v-else>
                  {{ displayValue(entry.field.value) }}
                </dd>
              </div>
            </dl>

            <p
              v-for="warning in record.warnings"
              :key="warning"
              class="record-warning"
            >
              {{ warning }}
            </p>
          </section>

          <button
            type="button"
            class="catalog-item"
            :class="{ 'catalog-item--active': record.id === selectedRecord?.id && selectedPage !== null }"
            @click="emit('selectRecord', record)"
          >
            <span class="catalog-item__visual">
              <img
                v-if="pageThumbnail(record)"
                :src="pageThumbnail(record)"
                alt=""
              >
              <svg
                v-else
                viewBox="0 0 80 92"
                aria-hidden="true"
              >
                <path d="M24 12h32l-4 12 8 17-7 28H27l-7-28 8-17zM25 34h30M23 46h34M31 69v10h18V69" />
              </svg>
            </span>

            <span class="catalog-item__content">
              <strong>{{ recordTitle(record) }}</strong>
              <small>{{ t('catalog.category') }} : {{ recordSubtitle(record) }}</small>
              <small
                v-if="recordTexture(record)"
                :title="recordTexture(record)"
              >
                {{ t('catalog.texture') }} : {{ recordTexture(record) }}
              </small>
              <small
                v-if="recordMeasurements(record)"
                :title="recordMeasurements(record)"
              >
                {{ t('catalog.measurements') }} : {{ recordMeasurements(record) }}
              </small>
            </span>

            <span
              v-if="mode === 'verify'"
              class="review-status"
              :class="`review-status--${recordStatus(record)}`"
              :title="t(`catalog.${recordStatus(record)}`)"
            >
              {{ recordStatus(record) === 'passed' ? '✓' : recordStatus(record) === 'failed' ? '×' : t('catalog.unreviewed') }}
            </span>
          </button>
        </div>


        <article
          v-for="item in visibleStaleItems"
          :key="`stale-${item.record_id}`"
          class="stale-sample"
          :data-record-id="item.record_id"
        >
          <span aria-hidden="true">!</span>
          <div>
            <strong>{{ item.record_id }}</strong>
            <small>{{ t('catalog.staleHint') }}</small>
          </div>
          <b>{{ matchingVersionId }}</b>
        </article>
      </div>

      <div
        v-else
        class="catalog-empty"
      >
        {{ t('catalog.empty') }}
      </div>
    </div>
  </aside>
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.catalogs {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 9px 8px 8px;
  overflow: visible;
}

.catalogs__header {
  position: relative;
  z-index: 5;
  display: grid;
  grid-template-columns: minmax(135px, 1fr) 112px 108px;
  gap: 7px;
  align-items: center;
  padding: 2px 3px 7px;
}

.catalogs__header--browse {
  grid-template-columns: minmax(135px, 1fr) 112px 108px;
}

.catalogs__header h2 {
  overflow: hidden;
  font-size: var(--af-font-panel-title);
  font-weight: 500;
  color: var(--af-heading);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-box {
  display: flex;
  gap: 5px;
  align-items: center;
  min-width: 0;
  height: 31px;
  padding: 0 8px;
  color: #8f9bad;
  background: #fff;
  border: 1px solid #e0e4ea;
  border-radius: 6px;
}

.search-box input {
  min-width: 0;
  width: 100%;
  font: inherit;
  font-size: 10px;
  color: #5b6470;
  outline: none;
  background: transparent;
  border: 0;
}

.filter-menu {
  position: relative;
  min-width: 0;
  height: 31px;
}

.filter-menu summary {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: space-between;
  height: 31px;
  padding: 0 8px 0 10px;
  color: #687385;
  list-style: none;
  cursor: pointer;
  user-select: none;
  background: linear-gradient(180deg, #fff 0%, #fffdfb 100%);
  border: 1px solid #e0e4ea;
  border-radius: 7px;
  transition: border-color 160ms ease, box-shadow 160ms ease, color 160ms ease;
}

.filter-menu summary::-webkit-details-marker {
  display: none;
}

.filter-menu summary:hover,
.filter-menu[open] summary {
  color: #a95a21;
  border-color: #d9ae87;
  box-shadow: 0 2px 7px rgb(120 76 39 / 10%);
}

.filter-menu__current {
  min-width: 0;
  overflow: hidden;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.filter-menu summary svg {
  flex: 0 0 15px;
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-width: 1.5;
}

.filter-menu__popover {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 40;
  width: 178px;
  max-height: min(430px, 70vh);
  padding: 6px;
  overflow-y: auto;
  background: rgb(255 253 251 / 98%);
  border: 1px solid #e4d7ca;
  border-radius: 10px;
  box-shadow: 0 12px 28px rgb(78 54 35 / 18%), 0 2px 6px rgb(78 54 35 / 8%);
  scrollbar-color: #d8c3ae transparent;
  scrollbar-width: thin;
}

.filter-menu__group-title {
  padding: 4px 8px 5px;
  font-size: 9px;
  font-weight: 600;
  color: #9b8979;
  letter-spacing: 0.04em;
}

.filter-menu__group-title--divided {
  padding-top: 8px;
  margin-top: 5px;
  border-top: 1px solid #eee5dc;
}

.filter-menu__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 31px;
  padding: 6px 9px;
  font-size: 11px;
  color: #514b46;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 6px;
  transition: color 130ms ease, background-color 130ms ease;
}

.filter-menu__option:hover {
  color: #9e531d;
  background: #fff3e8;
}

.filter-menu__option--active {
  font-weight: 600;
  color: #a55219;
  background: #ffead7;
}

.filter-menu__option b {
  width: 16px;
  font-size: 11px;
  font-weight: 700;
  color: #b66124;
  text-align: right;
}

.catalogs__scroll {
  flex: 1;
  min-height: 0;
  padding: 2px 7px 2px 2px;
  overflow-y: auto;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: #d3c8bd transparent;
}

.verification-panel {
  padding: 8px;
  margin-bottom: 7px;
  background: rgb(255 255 255 / 92%);
  border: 2px solid #ddd3c8;
  border-radius: 9px;
}

.verification-panel__header {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  margin-bottom: 7px;
}

.verification-panel__header h3 {
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
}

.review-button {
  min-width: 58px;
  height: 30px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  cursor: pointer;
  border: 0;
  border-radius: 4px;
  opacity: 0.82;
  white-space: nowrap;
}

.review-button--pass { background: #45ad50; }
.review-button--fail { background: #e1493e; }
.review-button--selected { opacity: 1; box-shadow: 0 0 0 3px rgb(88 126 175 / 16%); }
.review-button:disabled { cursor: wait; opacity: 0.5; }

.verification-context {
  display: flex;
  gap: 7px;
  align-items: center;
  min-height: 24px;
  padding: 4px 7px;
  margin-bottom: 7px;
  font-size: 9px;
  color: #6d7480;
  background: #f4f7fb;
  border: 1px solid #e1e8f0;
  border-radius: 5px;
}

.verification-context span {
  flex: 0 0 auto;
  padding: 2px 6px;
  color: #426c98;
  background: #e4effa;
  border-radius: 10px;
}

.verification-context strong {
  min-width: 0;
  font-weight: 500;
  color: #a45d21;
}

.verification-context--changed {
  background: #fff7e9;
  border-color: #eed6aa;
}

.ai-review-result {
  padding: 6px 8px;
  margin-bottom: 7px;
  font-size: 9px;
  color: #4e6274;
  background: #f3f8f3;
  border: 1px solid #d6e7d7;
  border-radius: 6px;
}

.ai-review-result > div {
  display: flex;
  gap: 7px;
  align-items: center;
}

.ai-review-result strong { color: #3e7047; }
.ai-review-result span { color: #788795; }
.ai-review-result b { margin-left: auto; color: #bd5a31; }
.ai-review-result p { margin: 4px 0 0; line-height: 1.45; }
.ai-review-result--conflict { background: #fff5ed; border-color: #efc9ad; }
.ai-review-result--agreed { background: #f3faf4; border-color: #cae5ce; }

.failure-editor {
  display: grid;
  grid-template-columns: 96px minmax(108px, 1fr) 117px;
  gap: 6px;
  align-items: end;
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  padding: 8px;
  margin-bottom: 8px;
  background: #fff7f3;
  border: 1px solid #efd3c3;
  border-radius: 6px;
}

.failure-editor label,
.failure-editor__field {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.failure-editor label > span,
.failure-editor__field > span {
  font-size: 9px;
  color: #8c6f5e;
}

.failure-editor input {
  width: 100%;
  height: 29px;
  min-width: 0;
  padding: 0 7px;
  font: inherit;
  font-size: 10px;
  color: #514b46;
  outline: none;
  background: #fff;
  border: 1px solid #dfcbbd;
  border-radius: 5px;
}

.failure-editor input:focus {
  border-color: #c67b48;
  box-shadow: 0 0 0 2px rgb(198 123 72 / 12%);
}

.failure-select {
  position: relative;
  min-width: 0;
  height: 29px;
}

.failure-select summary {
  display: flex;
  gap: 4px;
  align-items: center;
  justify-content: space-between;
  height: 29px;
  padding: 0 6px;
  font-size: 10px;
  color: #514b46;
  list-style: none;
  cursor: pointer;
  user-select: none;
  background: linear-gradient(180deg, #fff 0%, #fffdfb 100%);
  border: 1px solid #dfcbbd;
  border-radius: 5px;
  transition: border-color 150ms ease, box-shadow 150ms ease, color 150ms ease;
}

.failure-select summary::-webkit-details-marker { display: none; }

.failure-select summary:hover,
.failure-select[open] summary {
  color: #a6531f;
  border-color: #c67b48;
  box-shadow: 0 0 0 2px rgb(198 123 72 / 12%);
}

.failure-select summary > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.failure-select summary svg {
  flex: 0 0 12px;
  width: 12px;
  height: 12px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.7;
  transition: transform 150ms ease;
}

.failure-select[open] summary svg { transform: rotate(180deg); }

.failure-select__menu {
  position: absolute;
  top: calc(100% + 5px);
  left: 0;
  z-index: 60;
  width: 142px;
  max-width: min(142px, calc(100vw - 32px));
  padding: 5px;
  background: rgb(255 253 251 / 99%);
  border: 1px solid #e4d7ca;
  border-radius: 9px;
  box-shadow: 0 12px 28px rgb(78 54 35 / 18%), 0 2px 6px rgb(78 54 35 / 8%);
}

.failure-select__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 30px;
  padding: 5px 9px;
  font: inherit;
  font-size: 10px;
  color: #514b46;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 6px;
  transition: color 130ms ease, background-color 130ms ease;
}

.failure-select__option:hover {
  color: #9e531d;
  background: #fff3e8;
}

.failure-select__option--active {
  font-weight: 600;
  color: #a55219;
  background: #ffead7;
}

.failure-select__option b {
  width: 15px;
  color: #b66124;
  text-align: right;
}

.failure-editor__actions {
  display: grid;
  grid-template-columns: 42px 70px;
  gap: 5px;
  min-width: 117px;
}

.failure-editor__actions button {
  height: 29px;
  min-width: 0;
  padding: 0 6px;
  font-size: 9px;
  color: #725d50;
  line-height: 1;
  white-space: nowrap;
  word-break: keep-all;
  writing-mode: horizontal-tb;
  cursor: pointer;
  background: #fff;
  border: 1px solid #dfcbbd;
  border-radius: 5px;
}

.failure-editor__actions .failure-editor__confirm {
  color: #fff;
  background: #d75246;
  border-color: #d75246;
}

.verification-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 5px 8px;
}

.verification-fields > div {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  min-height: 34px;
  overflow: hidden;
  border: 1px solid #ece5de;
  border-radius: 4px;
}

.verification-fields > .verification-field--category {
  grid-column: 1;
}

.verification-fields > .verification-field--completeness {
  grid-column: 2;
}

.verification-fields > .verification-field--wide {
  grid-column: 1 / -1;
}

.verification-fields dt,
.verification-fields dd {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 6px;
  font-size: 9px;
  line-height: 1.4;
}

.verification-fields dt {
  justify-content: center;
  background: #fdfbf9;
  border-right: 1px solid #ece5de;
}

.verification-fields dd {
  max-height: 72px;
  overflow: auto;
  color: #4f4a45;
}

.verification-field--description dd {
  align-items: flex-start;
  min-height: 54px;
  max-height: none;
  overflow: visible;
  white-space: normal;
  overflow-wrap: anywhere;
}

.verification-fields .verification-figure {
  display: flex;
  flex-direction: column;
  gap: 5px;
  align-items: center;
  justify-content: center;
  max-height: none;
  min-height: 112px;
  overflow: hidden;
}

.verification-figure img {
  width: min(132px, 100%);
  height: 84px;
  object-fit: contain;
  background: #fff;
  border: 1px solid #eee7df;
  border-radius: 4px;
}

.verification-figure span {
  width: 100%;
  min-width: 0;
  line-height: 1.3;
  text-align: center;
  overflow-wrap: anywhere;
}

.record-warning {
  padding: 5px 7px;
  margin-top: 6px;
  font-size: 9px;
  color: #9d5b35;
  background: #fff3e9;
  border-radius: 4px;
}

.catalog-list {
  display: grid;
  gap: 6px;
}

.catalog-entry {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.stale-sample {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  min-height: 58px;
  padding: 9px 10px;
  color: #7a6657;
  background: #fff8ed;
  border: 1px dashed #dcba8d;
  border-radius: 7px;
}

.stale-sample > span {
  display: grid;
  place-items: center;
  width: 25px;
  height: 25px;
  font-weight: 700;
  color: #fff;
  background: #d78a3e;
  border-radius: 50%;
}

.stale-sample > div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.stale-sample strong,
.stale-sample small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stale-sample small { color: #9a7860; }

.stale-sample b {
  padding: 3px 6px;
  font-size: 9px;
  color: #9b5a28;
  background: #f6e1ca;
  border-radius: 9px;
}

.catalog-entry--first-selected .catalog-item {
  order: 1;
}

.catalog-entry--first-selected .verification-panel {
  order: 2;
}

.verification-panel--inline {
  margin-bottom: 0;
  animation: reveal-details 180ms ease-out;
}

@keyframes reveal-details {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.catalog-item {
  position: relative;
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  align-items: center;
  min-width: 0;
  min-height: 92px;
  padding: 7px 38px 7px 7px;
  overflow: hidden;
  text-align: left;
  cursor: pointer;
  background: rgb(255 255 255 / 76%);
  border: 1px solid #ece5de;
  border-radius: 7px;
}

.catalog-item:hover,
.catalog-item--active {
  background: #fffaf5;
  border-color: #d6b18c;
  box-shadow: 0 2px 6px rgb(108 71 36 / 8%);
}

.catalog-item__visual {
  display: grid;
  place-items: center;
  width: 74px;
  height: 78px;
  overflow: hidden;
  background: #fff;
}

.catalog-item__visual img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.catalog-item__visual svg {
  width: 56px;
  height: 67px;
  fill: none;
  stroke: #3f3b36;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.4;
}

.catalog-item__content {
  display: grid;
  min-width: 0;
}

.catalog-item__content strong {
  margin-bottom: 3px;
  font-size: 13px;
  color: #8b6f50;
}

.catalog-item__content small {
  overflow: hidden;
  font-size: 10px;
  line-height: 1.5;
  color: #586170;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-status {
  position: absolute;
  top: 8px;
  right: 7px;
  min-width: 24px;
  height: 24px;
  padding: 0 5px;
  font-size: 9px;
  line-height: 24px;
  color: #79756f;
  text-align: center;
  background: #efede8;
  border-radius: 12px;
}

.review-status--passed,
.review-status--failed {
  width: 24px;
  padding: 0;
  font-size: 18px;
  color: #fff;
}

.review-status--passed { background: #4eaf59; }
.review-status--failed { background: #ea4b40; }

.catalog-empty {
  display: grid;
  place-items: center;
  min-height: 160px;
  padding: 24px;
  font-size: 12px;
  color: var(--af-muted);
  text-align: center;
  border: 1px dashed #ddd4cb;
  border-radius: 7px;
}

@media (max-width: 1280px) {
  .catalogs__header { grid-template-columns: 1fr 96px; }
  .catalogs__header h2 { grid-column: 1 / -1; }
}
</style>
