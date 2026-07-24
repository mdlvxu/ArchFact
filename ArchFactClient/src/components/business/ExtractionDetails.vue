<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from '@/i18n'

interface Props {
  page: number
  total: number
  fileName: string
}

interface CatalogSeed {
  code: string
  category: 'Pottery' | 'Stone' | 'Bronze'
  subtype: string
  type: string
  texture: string
  measurements: string
  description: string
  glyph: string
}

interface CatalogEntry extends CatalogSeed {
  artifactId: string
  page: number
}

const props = defineProps<Props>()
const { localize, t } = useI18n()

const catalogSeeds: CatalogSeed[] = [
  {
    code: 'Q2:2',
    category: 'Pottery',
    subtype: 'Footed vessel',
    type: 'Type C',
    texture: 'Argillaceous red pottery',
    measurements: 'Bottom diameter 3.4 cm; height 15.6 cm',
    description: 'Concave arcuate foot, angular rim and an arc-shaped body.',
    glyph: 'M25 12h30l-4 10 8 12-6 22-8 12v10H35V68l-8-12-6-22 8-12z M30 28h20 M27 48h26',
  },
  {
    code: 'T03⑨A:13',
    category: 'Pottery',
    subtype: 'Long-necked vessel',
    type: 'Type A',
    texture: 'Black clay pottery',
    measurements: 'Remaining height 36 cm; mouth 8.2 cm',
    description: 'Long neck with a folded rim and a rounded lower body.',
    glyph: 'M34 8h12l-2 20 12 10-5 20-8 8v15H37V66l-8-8-5-20 12-10z M27 42h26 M31 60h18',
  },
  {
    code: 'T04△A:65',
    category: 'Pottery',
    subtype: 'Open bowl',
    type: 'Type B',
    texture: 'Argillaceous black pottery',
    measurements: 'Bottom diameter 12 cm; height 9.8 cm',
    description: 'Wide open mouth, shallow belly and a small flat base.',
    glyph: 'M13 24q27 45 54 0 M13 24h54 M29 61h22v10H29z M20 38h40',
  },
  {
    code: 'T1101⑨A:31',
    category: 'Stone',
    subtype: 'Decorated vessel',
    type: 'Type D',
    texture: 'Fine-grained gray stone',
    measurements: 'Caliber 11.2 cm; height 18.4 cm',
    description: 'Straight rim and rounded body decorated with diagonal bands.',
    glyph: 'M24 14h32l-4 10 8 18-7 27H27l-7-27 8-18z M24 34h32 M23 42l32 18 M22 50l27 19 M29 24h22',
  },
  {
    code: 'T1101⑨A:20',
    category: 'Bronze',
    subtype: 'Handled cup',
    type: 'Type E',
    texture: 'Cast bronze with dark patina',
    measurements: 'Width 14.7 cm; remaining height 12 cm',
    description: 'Small handled cup with a flared mouth and ring foot.',
    glyph: 'M22 20h34l-4 33-8 10H34l-8-10z M56 28q18 2 10 20-5 8-14 7 M34 63v10h12V63',
  },
]

const searchText = ref('')
const categoryFilter = ref<'All' | CatalogSeed['category']>('All')
const selectedArtifactId = ref('')

const catalogEntries = computed<CatalogEntry[]>(() => {
  if (!props.fileName || props.total === 0) return []

  const normalizedPage = String(props.page).padStart(3, '0')
  return catalogSeeds.map((seed, index) => ({
    ...seed,
    artifactId: `AF-${normalizedPage}-${String(index + 1).padStart(2, '0')}`,
    page: props.page,
  }))
})

const filteredEntries = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  return catalogEntries.value.filter((entry) => {
    const matchesCategory = categoryFilter.value === 'All' || entry.category === categoryFilter.value
    const searchableText = [
      entry.artifactId,
      entry.code,
      entry.category,
      entry.texture,
      entry.description,
    ]
      .join(' ')
      .toLowerCase()

    return matchesCategory && (!keyword || searchableText.includes(keyword))
  })
})

const selectedEntry = computed(
  () =>
    catalogEntries.value.find((entry) => entry.artifactId === selectedArtifactId.value) ??
    catalogEntries.value[0],
)

watch(
  [() => props.page, () => props.fileName],
  () => {
    selectedArtifactId.value = catalogEntries.value[0]?.artifactId ?? ''
  },
  { immediate: true },
)
</script>

<template>
  <aside class="extraction-details panel">
    <div class="catalog-header">
      <div class="catalog-title">
        <h2>{{ t('catalog.title') }}</h2>
        <p v-if="fileName" :title="fileName">{{ fileName }} · {{ t('common.page', { page }) }}</p>
      </div>
      <div class="catalog-tools">
        <label class="search-box">
          <svg class="tool-icon" viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="8.5" cy="8.5" r="5.5" />
            <path d="m13 13 4 4" />
          </svg>
          <input
            v-model="searchText"
            type="search"
            :placeholder="t('details.search')"
            :aria-label="t('details.search')"
          >
        </label>
        <label class="filter-box">
          <svg class="tool-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3 5h14M6 10h8M8 15h4" />
          </svg>
          <select v-model="categoryFilter" :aria-label="t('details.filter')">
            <option value="All">{{ t('details.all') }}</option>
            <option value="Pottery">{{ localize('Pottery') }}</option>
            <option value="Stone">{{ localize('Stone') }}</option>
            <option value="Bronze">{{ localize('Bronze') }}</option>
          </select>
        </label>
      </div>
    </div>

    <div v-if="selectedEntry" class="details-scroll">
      <section class="verification-card">
        <div class="verification-title">
          <h3>{{ t('catalog.verification') }}</h3>
          <span>{{ t('details.previewData') }}</span>
        </div>

        <dl class="detail-grid">
          <div>
            <dt>{{ localize('Artifact ID') }}</dt>
            <dd>{{ selectedEntry.artifactId }}</dd>
          </div>
          <div>
            <dt>{{ localize('Subtype') }}</dt>
            <dd>{{ selectedEntry.subtype }}</dd>
          </div>
          <div>
            <dt>{{ localize('Category') }}</dt>
            <dd>{{ selectedEntry.category }}</dd>
          </div>
          <div>
            <dt>{{ localize('Type') }}</dt>
            <dd>{{ selectedEntry.type }}</dd>
          </div>
          <div class="detail-grid__wide">
            <dt>{{ localize('Morphological Description') }}</dt>
            <dd>{{ selectedEntry.description }}</dd>
          </div>
          <div class="detail-grid__wide">
            <dt>{{ localize('Texture') }}</dt>
            <dd>{{ selectedEntry.texture }}</dd>
          </div>
          <div class="detail-grid__wide">
            <dt>{{ localize('Measurements') }}</dt>
            <dd>{{ selectedEntry.measurements }}</dd>
          </div>
          <div class="detail-grid__wide figure-caption">
            <dt>{{ localize('Figure Caption') }}</dt>
            <dd>
              <svg viewBox="0 0 80 92" role="img" :aria-label="`${selectedEntry.code} 器物线图`">
                <path :d="selectedEntry.glyph" />
              </svg>
              <span>
                <strong>{{ selectedEntry.code }}</strong>
                {{ t('details.sourcePage', { pages: selectedEntry.page }) }}
              </span>
            </dd>
          </div>
        </dl>
      </section>

      <div class="catalog-count">
        <span>{{ t('details.records', { count: filteredEntries.length }) }}</span>
        <span>{{ page }}/{{ total }}</span>
      </div>

      <div v-if="filteredEntries.length" class="catalog-list">
        <button
          v-for="entry in filteredEntries"
          :key="entry.artifactId"
          type="button"
          class="catalog-item"
          :class="{ 'catalog-item--active': entry.artifactId === selectedEntry.artifactId }"
          @click="selectedArtifactId = entry.artifactId"
        >
          <svg viewBox="0 0 80 92" aria-hidden="true">
            <path :d="entry.glyph" />
          </svg>
          <span>
            <strong>{{ entry.code }}</strong>
            <small>{{ t('catalog.category') }} · {{ localize(entry.category) }}</small>
            <small>{{ t('catalog.texture') }} · {{ entry.texture }}</small>
            <small>{{ t('catalog.measurements') }} · {{ entry.measurements }}</small>
          </span>
        </button>
      </div>

      <div v-else class="catalog-empty catalog-empty--small">
        {{ t('catalog.empty') }}
      </div>
    </div>

    <div v-else class="catalog-empty">
      <span>PDF</span>
      <h3>{{ t('details.emptyTitle') }}</h3>
      <p>{{ t('details.emptyHint') }}</p>
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

.extraction-details {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 10px;
  overflow: hidden;
}

.catalog-header {
  display: grid;
  gap: 9px;
  padding: 2px 2px 9px;
}

.catalog-title {
  min-width: 0;
}

.catalog-title h2 {
  font-size: var(--af-font-panel-title);
  font-weight: 500;
  color: var(--af-heading);
}

.catalog-title p {
  margin-top: 3px;
  overflow: hidden;
  font-size: 12px;
  color: var(--af-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog-tools {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 108px;
  gap: 7px;
}

.search-box,
.filter-box {
  display: flex;
  gap: 5px;
  align-items: center;
  min-width: 0;
  height: 34px;
  padding: 0 9px;
  color: #8e98a8;
  background: #fff;
  border: 1px solid #dfe2e8;
  border-radius: 7px;
}

.search-box input,
.filter-box select {
  min-width: 0;
  width: 100%;
  font: inherit;
  font-size: 12px;
  color: #555d68;
  outline: none;
  background: transparent;
  border: 0;
}

.tool-icon {
  flex: 0 0 auto;
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-width: 1.6;
}

.filter-box select {
  cursor: pointer;
}

.details-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.verification-card {
  padding: 8px;
  border: 2px solid #ddd3c8;
  border-radius: 10px;
}

.verification-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.verification-title h3 {
  font-size: var(--af-font-section-title);
  font-weight: 500;
}

.verification-title span {
  padding: 3px 7px;
  font-size: 10px;
  color: #91643d;
  background: #f7ecdf;
  border-radius: 9px;
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 5px 8px;
}

.detail-grid > div {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  min-height: 40px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #ebe4dc;
  border-radius: 5px;
}

.detail-grid dt,
.detail-grid dd {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 7px;
  font-size: 11px;
  line-height: 1.35;
}

.detail-grid dt {
  justify-content: center;
  color: #393530;
  text-align: center;
  background: #fdfbf9;
  border-right: 1px solid #ebe4dc;
}

.detail-grid dd {
  color: #554f49;
}

.detail-grid .detail-grid__wide {
  grid-column: 1 / -1;
}

.figure-caption dd {
  gap: 10px;
}

.figure-caption svg,
.catalog-item svg {
  flex: 0 0 auto;
  width: 58px;
  height: 68px;
  fill: none;
  stroke: #3f3b36;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.4;
}

.figure-caption dd span {
  display: grid;
  gap: 3px;
  color: #898078;
}

.figure-caption strong {
  color: #7d644b;
}

.catalog-count {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 2px 7px;
  font-size: 11px;
  color: #948b82;
}

.catalog-list {
  display: grid;
  gap: 7px;
}

.catalog-item {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  align-items: center;
  min-width: 0;
  min-height: 108px;
  padding: 8px;
  overflow: hidden;
  cursor: pointer;
  background: rgb(255 255 255 / 78%);
  border: 1px solid #ece5de;
  border-radius: 8px;
}

.catalog-item:hover,
.catalog-item--active {
  background: #fffaf5;
  border-color: #d5af87;
  box-shadow: 0 2px 6px rgb(108 71 36 / 8%);
}

.catalog-item svg {
  width: 75px;
  height: 82px;
  margin: auto;
}

.catalog-item > span {
  display: grid;
  min-width: 0;
}

.catalog-item strong {
  margin-bottom: 4px;
  font-size: 15px;
  color: #8b6f50;
  text-align: left;
}

.catalog-item small {
  overflow: hidden;
  font-size: 12px;
  line-height: 1.5;
  color: #586170;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog-empty {
  display: grid;
  flex: 1;
  place-items: center;
  align-content: center;
  padding: 28px;
  color: #948b82;
  text-align: center;
  background: #faf8f5;
  border: 1px dashed #ddd3c8;
  border-radius: 8px;
}

.catalog-empty > span {
  display: grid;
  place-items: center;
  width: 54px;
  height: 68px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #ae6c31;
  background: #fff;
  border: 1px solid #ded5cc;
}

.catalog-empty h3 {
  margin-bottom: 6px;
  font-size: var(--af-font-section-title);
  font-weight: 500;
  color: #5e5750;
}

.catalog-empty p {
  max-width: 270px;
  font-size: 12px;
  line-height: 1.6;
}

.catalog-empty--small {
  min-height: 120px;
}

@media (max-width: 1280px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }

  .detail-grid .detail-grid__wide {
    grid-column: auto;
  }
}
</style>
