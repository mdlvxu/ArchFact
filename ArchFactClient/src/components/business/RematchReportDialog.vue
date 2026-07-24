<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from '@/i18n'
import type {
  RematchChangeKind,
  RematchRelationChange,
  RematchRun,
} from '@/types/extraction'

interface Props {
  run: RematchRun
  changes: RematchRelationChange[]
  loading?: boolean
  applying?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  applying: false,
})
const emit = defineEmits<{
  close: []
  discard: []
  apply: []
}>()
const { t } = useI18n()
const filter = ref<'all' | RematchChangeKind | 'protected'>('all')

const visibleChanges = computed(() => {
  if (filter.value === 'all') return props.changes
  if (filter.value === 'protected') return props.changes.filter((item) => item.protected)
  return props.changes.filter((item) => item.change === filter.value)
})

function changeCount(kind: RematchChangeKind) {
  return props.changes.filter((item) => item.change === kind).length
}

function score(value: number | null) {
  return value === null ? '—' : `${Math.round(value * 100)}%`
}
</script>

<template>
  <div
    class="rematch-dialog"
    role="dialog"
    aria-modal="true"
    :aria-label="t('matching.reportTitle')"
    @click.self="emit('close')"
  >
    <section class="rematch-report">
      <header class="rematch-report__header">
        <div>
          <span>{{ t('matching.reportEyebrow') }}</span>
          <h2>{{ t('matching.reportTitle') }}</h2>
          <p>{{ run.base_matching_version_id }} → {{ run.id }}</p>
        </div>
        <button
          type="button"
          :aria-label="t('common.cancel')"
          @click="emit('close')"
        >
          ×
        </button>
      </header>

      <div
        v-if="run.report"
        class="rematch-report__body"
      >
        <section class="report-metrics report-metrics--primary">
          <article>
            <strong>{{ run.report.complete_chains }}</strong>
            <span>{{ t('matching.completeChains') }}</span>
          </article>
          <article>
            <strong>{{ run.report.linked_records }}</strong>
            <span>{{ t('matching.linkedRecords') }}</span>
          </article>
          <article>
            <strong>{{ run.report.partial_records }}</strong>
            <span>{{ t('matching.partialRecords') }}</span>
          </article>
          <article>
            <strong>{{ run.report.unlinked_records }}</strong>
            <span>{{ t('matching.unlinkedRecords') }}</span>
          </article>
        </section>

        <section class="report-quality-grid">
          <article>
            <h3>{{ t('matching.methods') }}</h3>
            <dl>
              <div><dt>{{ t('matching.ocrExact') }}</dt><dd>{{ run.report.ocr_exact_relations }}</dd></div>
              <div><dt>{{ t('matching.layoutFallback') }}</dt><dd>{{ run.report.layout_fallback_relations }}</dd></div>
              <div class="metric-warning">
                <dt>{{ t('matching.conflicts') }}</dt><dd>{{ run.report.conflict_relations }}</dd>
              </div>
            </dl>
          </article>
          <article>
            <h3>{{ t('matching.confidence') }}</h3>
            <dl>
              <div><dt>{{ t('matching.high') }}</dt><dd>{{ run.report.confidence.high }}</dd></div>
              <div><dt>{{ t('matching.medium') }}</dt><dd>{{ run.report.confidence.medium }}</dd></div>
              <div><dt>{{ t('matching.low') }}</dt><dd>{{ run.report.confidence.low }}</dd></div>
            </dl>
          </article>
          <article>
            <h3>{{ t('matching.protection') }}</h3>
            <dl>
              <div><dt>{{ t('matching.passedProtected') }}</dt><dd>{{ run.report.protection.passed_records }}</dd></div>
              <div><dt>{{ t('matching.relationsProtected') }}</dt><dd>{{ run.report.protection.protected_relations }}</dd></div>
              <div><dt>{{ t('matching.rejectedProtected') }}</dt><dd>{{ run.report.protection.rejected_relations }}</dd></div>
            </dl>
          </article>
        </section>

        <section class="relation-changes">
          <div class="relation-changes__heading">
            <div>
              <h3>{{ t('matching.changes') }}</h3>
              <p>{{ t('matching.changesHint', { count: changes.length }) }}</p>
            </div>
            <nav :aria-label="t('matching.changeFilters')">
              <button
                :class="{ active: filter === 'all' }"
                @click="filter = 'all'"
              >
                {{ t('matching.all') }} {{ changes.length }}
              </button>
              <button
                :class="{ active: filter === 'added' }"
                @click="filter = 'added'"
              >
                + {{ changeCount('added') }}
              </button>
              <button
                :class="{ active: filter === 'removed' }"
                @click="filter = 'removed'"
              >
                − {{ changeCount('removed') }}
              </button>
              <button
                :class="{ active: filter === 'changed' }"
                @click="filter = 'changed'"
              >
                ± {{ changeCount('changed') }}
              </button>
              <button
                :class="{ active: filter === 'protected' }"
                @click="filter = 'protected'"
              >
                {{ t('matching.protected') }}
              </button>
            </nav>
          </div>

          <div class="relation-change-list">
            <p
              v-if="loading"
              class="relation-change-empty"
            >
              {{ t('common.loading') }}
            </p>
            <p
              v-else-if="!visibleChanges.length"
              class="relation-change-empty"
            >
              {{ t('matching.noChanges') }}
            </p>
            <article
              v-for="item in visibleChanges"
              v-else
              :key="item.relation_id"
            >
              <span :data-kind="item.change">{{ t(`matching.change.${item.change}`) }}</span>
              <div>
                <strong>{{ item.relation_type || item.relation_id }}</strong>
                <p>{{ item.source_region_id }} → {{ item.target_region_id }}</p>
              </div>
              <div class="relation-change-method">
                <small>{{ item.before_method || '—' }} · {{ score(item.before_score) }}</small>
                <b>→</b>
                <small>{{ item.after_method || '—' }} · {{ score(item.after_score) }}</small>
              </div>
              <em v-if="item.protected">{{ t('matching.protected') }}</em>
            </article>
          </div>
        </section>
      </div>

      <footer>
        <button
          type="button"
          @click="emit('discard')"
        >
          {{ t('matching.discard') }}
        </button>
        <button
          type="button"
          @click="emit('close')"
        >
          {{ t('matching.keepPreview') }}
        </button>
        <button
          type="button"
          class="report-apply"
          :disabled="applying"
          @click="emit('apply')"
        >
          {{ applying ? t('matching.applying') : t('matching.apply') }}
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped lang="scss">
.rematch-dialog {
  position: fixed;
  z-index: 90;
  inset: 0;
  display: grid;
  padding: 36px;
  background: rgb(42 35 29 / 38%);
  backdrop-filter: blur(3px);
  place-items: center;
}

.rematch-report {
  display: flex;
  flex-direction: column;
  width: min(960px, 96vw);
  max-height: min(820px, 92vh);
  overflow: hidden;
  background: #fffdfb;
  border: 1px solid #dfcbbb;
  border-radius: 15px;
  box-shadow: 0 20px 60px rgb(63 42 28 / 24%);
}

.rematch-report__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 20px 22px 15px;
  border-bottom: 1px solid #eee4dc;
}

.rematch-report__header span,
.rematch-report__header p {
  font-size: 11px;
  color: #a06b45;
}

.rematch-report__header h2 {
  margin: 3px 0;
  font-size: 23px;
  color: #2e2823;
}

.rematch-report__header button {
  width: 32px;
  height: 32px;
  font-size: 22px;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.rematch-report__body {
  padding: 16px 20px;
  overflow-y: auto;
}

.report-metrics,
.report-quality-grid {
  display: grid;
  gap: 10px;
}

.report-metrics--primary {
  grid-template-columns: repeat(4, 1fr);
}

.report-metrics article {
  display: grid;
  gap: 4px;
  padding: 13px;
  background: #f8f2ec;
  border-radius: 9px;
}

.report-metrics strong {
  font-size: 24px;
  color: #9b4e1d;
}

.report-metrics span,
.report-quality-grid dt {
  font-size: 11px;
  color: #786f68;
}

.report-quality-grid {
  grid-template-columns: repeat(3, 1fr);
  margin-top: 10px;
}

.report-quality-grid > article {
  padding: 12px 14px;
  border: 1px solid #eee3da;
  border-radius: 9px;
}

.report-quality-grid h3,
.relation-changes h3 {
  margin-bottom: 8px;
  font-size: 13px;
}

.report-quality-grid dl {
  display: grid;
  gap: 6px;
}

.report-quality-grid dl > div {
  display: flex;
  justify-content: space-between;
}

.report-quality-grid dd {
  font-size: 12px;
  font-weight: 700;
}

.metric-warning dd {
  color: #bd4c43;
}

.relation-changes {
  margin-top: 16px;
}

.relation-changes__heading {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  justify-content: space-between;
}

.relation-changes__heading p {
  font-size: 10px;
  color: #8b837c;
}

.relation-changes nav {
  display: flex;
  gap: 5px;
}

.relation-changes nav button {
  padding: 5px 8px;
  font-size: 10px;
  cursor: pointer;
  background: #fff;
  border: 1px solid #dfd4cb;
  border-radius: 12px;
}

.relation-changes nav button.active {
  color: #fff;
  background: #a95721;
  border-color: #a95721;
}

.relation-change-list {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}

.relation-change-list > article {
  display: grid;
  grid-template-columns: 62px minmax(150px, 1fr) minmax(260px, 1.4fr) auto;
  gap: 9px;
  align-items: center;
  min-width: 0;
  padding: 8px 10px;
  background: #fff;
  border: 1px solid #eee4dd;
  border-radius: 7px;
}

.relation-change-list article > span,
.relation-change-list em {
  padding: 3px 6px;
  font-size: 9px;
  font-style: normal;
  text-align: center;
  border-radius: 9px;
}

.relation-change-list article > span[data-kind='added'] {
  color: #397b4b;
  background: #e6f5e9;
}

.relation-change-list article > span[data-kind='removed'] {
  color: #a54740;
  background: #fde9e7;
}

.relation-change-list article > span[data-kind='changed'],
.relation-change-list em {
  color: #8b5e19;
  background: #fff0cf;
}

.relation-change-list strong,
.relation-change-list p,
.relation-change-list small {
  display: block;
  overflow: hidden;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.relation-change-list p,
.relation-change-list small {
  color: #817870;
}

.relation-change-method {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 6px;
  align-items: center;
}

.relation-change-empty {
  padding: 28px;
  color: #8d847c;
  text-align: center;
  background: #faf7f4;
  border-radius: 8px;
}

.rematch-report footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding: 13px 20px;
  border-top: 1px solid #eee4dc;
}

.rematch-report footer button {
  min-width: 90px;
  height: 34px;
  color: #7f5639;
  cursor: pointer;
  background: #fff;
  border: 1px solid #dbc5b4;
  border-radius: 7px;
}

.rematch-report footer .report-apply {
  color: #fff;
  background: #ad5920;
  border-color: #ad5920;
}

@media (max-width: 720px) {
  .rematch-dialog {
    padding: 12px;
  }

  .report-metrics--primary,
  .report-quality-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .relation-changes__heading {
    align-items: stretch;
    flex-direction: column;
  }

  .relation-change-list > article {
    grid-template-columns: 62px minmax(0, 1fr);
  }
}
</style>
