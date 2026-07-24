<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '@/i18n'
import type { ExtractionRecord } from '@/types/extraction'

interface Props {
  page: number
  records: ExtractionRecord[]
}

const props = defineProps<Props>()
const { localize, t } = useI18n()

const visibleRecords = computed(() => {
  const onCurrentPage = props.records.filter((record) => record.source_pages.includes(props.page))
  return onCurrentPage.length ? onCurrentPage : props.records
})

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return t('results.noContent')
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}
</script>

<template>
  <aside class="results panel">
    <header class="results__header">
      <div>
        <h2>{{ t('results.title') }}</h2>
        <p>{{ t('results.pageRecords', { page, count: visibleRecords.length }) }}</p>
      </div>
      <span>{{ t('results.live') }}</span>
    </header>

    <div class="results__scroll">
      <article
        v-for="record in visibleRecords"
        :key="record.id"
        class="record-card"
      >
        <div class="record-card__title">
          <strong>{{ localize(record.record_type) }}</strong>
          <small>{{ t('details.sourcePage', { pages: record.source_pages.join(', ') }) }}</small>
        </div>

        <dl>
          <div
            v-for="(field, key) in record.fields"
            :key="key"
          >
            <dt>{{ localize(String(key)) }}</dt>
            <dd>
              <pre>{{ displayValue(field.value) }}</pre>
              <blockquote v-if="field.evidence[0]?.quote">
                {{ field.evidence[0].quote }}
              </blockquote>
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
      </article>
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

.results {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 12px;
  overflow: hidden;
}

.results__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid #ece3da;
}

.results__header h2 {
  font-size: var(--af-font-panel-title);
  font-weight: 500;
}

.results__header p {
  margin-top: 3px;
  font-size: 11px;
  color: var(--af-muted);
}

.results__header > span {
  padding: 3px 8px;
  font-size: 10px;
  color: #7f5d3d;
  background: #f7ecdf;
  border-radius: 10px;
}

.results__scroll {
  display: grid;
  gap: 9px;
  padding-top: 10px;
  overflow-y: auto;
}

.record-card {
  padding: 10px;
  background: #fff;
  border: 1px solid #e9dfd5;
  border-radius: 8px;
}

.record-card__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.record-card__title strong {
  font-size: 13px;
  color: #805c3d;
}

.record-card__title small {
  font-size: 10px;
  color: var(--af-muted);
}

.record-card dl {
  display: grid;
  gap: 7px;
}

.record-card dl > div {
  overflow: hidden;
  border: 1px solid #eee7e0;
  border-radius: 6px;
}

.record-card dt {
  padding: 5px 7px;
  font-size: 11px;
  color: #6c625a;
  background: #faf7f4;
}

.record-card dd {
  padding: 7px;
}

.record-card pre {
  overflow: auto;
  font: inherit;
  font-size: 12px;
  line-height: 1.55;
  color: #403b37;
  white-space: pre-wrap;
}

.record-card blockquote {
  padding: 6px 8px;
  margin-top: 7px;
  font-size: 10px;
  line-height: 1.45;
  color: #8a7f75;
  background: #faf6f1;
  border-left: 2px solid #c9a17d;
}

.record-warning {
  padding: 6px 8px;
  margin-top: 7px;
  font-size: 10px;
  color: #9d5b35;
  background: #fff3e9;
  border-radius: 5px;
}
</style>
