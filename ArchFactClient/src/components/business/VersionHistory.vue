<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type { VerificationVersion } from '@/types/verification'

interface Props {
  versions: VerificationVersion[]
  selectedVersion?: number | null
}

const props = withDefaults(defineProps<Props>(), {
  selectedVersion: null,
})
const emit = defineEmits<{
  selectVersion: [version: number]
}>()
const { isChinese, localize, t } = useI18n()

const historyRef = ref<HTMLElement>()
const sortedVersions = computed(() => [...props.versions].sort((left, right) => right.version - left.version))

/** 新版本出现时回到列表顶部，确保最新结果立即可见。 */
watch(
  () => sortedVersions.value[0]?.version,
  async () => {
    await nextTick()
    const container = historyRef.value
    if (!container) return

    if (typeof container.scrollTo === 'function') {
      container.scrollTo({ top: 0, behavior: 'smooth' })
    } else {
      container.scrollTop = 0
    }
  },
)

function versionTone(version: number) {
  const tones = ['blue', 'violet', 'orange', 'green']
  return tones[version % tones.length]
}

function displayVersionText(value: string) {
  const known = localize(value)
  if (known !== value || !isChinese.value) return known

  const execution = value.match(/^(\d+) active assertions executed · (.+)$/)
  if (execution) return `${execution[1]} 条已启用规则已执行 · ${localize(execution[2])}`

  const required = value.match(/^Alignment (\d+)%, (\d+) records required review\.$/)
  if (required) return `一致性 ${required[1]}%，${required[2]} 条记录需要审核。`

  const requireReview = value.match(/^Alignment (\d+)%, (\d+) records require review\.$/)
  if (requireReview) return `一致性 ${requireReview[1]}%，${requireReview[2]} 条记录需要审核。`

  return value
}
</script>

<template>
  <section class="version-history">
    <h3>{{ t('version.title') }}</h3>

    <div
      ref="historyRef"
      class="history-scroll"
      :aria-label="t('version.list')"
    >
      <p
        v-if="!sortedVersions.length"
        class="version-empty"
      >
        {{ t('version.empty') }}
      </p>
      <template
        v-for="(version, index) in sortedVersions"
        :key="version.version"
      >
        <div
          v-if="index === 1"
          class="earlier-divider"
        >
          <span>{{ t('version.earlier') }}</span>
        </div>

        <article
          class="version-card"
          :class="{
            'version-card--current': index === 0,
            'version-card--selected': version.version === selectedVersion,
          }"
          role="button"
          tabindex="0"
          :aria-pressed="version.version === selectedVersion"
          @click="emit('selectVersion', version.version)"
          @keydown.enter="emit('selectVersion', version.version)"
          @keydown.space.prevent="emit('selectVersion', version.version)"
        >
          <header>
            <span
              class="version-badge"
              :data-tone="versionTone(version.version)"
            >
              V{{ version.version }}
            </span>
            <strong>{{ index === 0 ? t('version.current') : displayVersionText(version.title) }}</strong>
            <time>{{ version.createdAt }}</time>
          </header>

          <div class="version-body">
            <p class="version-summary">
              {{ displayVersionText(version.summary) }}
            </p>

            <div class="version-metadata">
              <span>{{ t('version.matching') }} {{ version.matchingVersionId || 'M0' }}</span>
              <span
                v-if="version.relationChangedCount"
                class="version-meta--warning"
              >
                {{ t('version.relationChanged', { count: version.relationChangedCount }) }}
              </span>
              <span
                v-if="version.staleCount"
                class="version-meta--danger"
              >
                {{ t('version.stale', { count: version.staleCount }) }}
              </span>
              <span :class="version.exportable ? 'version-meta--ready' : 'version-meta--blocked'">
                {{ version.exportable ? t('version.exportable') : t('version.exportBlocked') }}
              </span>
            </div>

            <template v-if="index === 0">
              <div class="change-comparison">
                <div>
                  <span>{{ t('version.before') }}</span>
                  <p>{{ displayVersionText(version.before) }}</p>
                </div>
                <div>
                  <span>{{ t('version.after') }}</span>
                  <p>{{ displayVersionText(version.after) }}</p>
                </div>
              </div>

              <div class="version-impact">
                <strong>{{ t('version.impact') }}</strong>
                <dl>
                  <div>
                    <dt>{{ t('summary.alignment') }}</dt>
                    <dd>{{ version.impact.alignmentBefore }}% → {{ version.impact.alignmentAfter }}% ↑</dd>
                  </div>
                  <div>
                    <dt>{{ t('summary.error') }}</dt>
                    <dd>{{ version.impact.errorsBefore }} → {{ version.impact.errorsAfter }} ↓</dd>
                  </div>
                  <div>
                    <dt>{{ t('catalog.pass') }}</dt>
                    <dd>{{ version.impact.passedBefore }} → {{ version.impact.passedAfter }} ↑</dd>
                  </div>
                </dl>
              </div>
            </template>
          </div>
        </article>
      </template>
    </div>
  </section>
</template>

<style scoped lang="scss">
.version-history {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: rgb(255 255 255 / 82%);
  border: 1px solid #ebe4dd;
  border-radius: 10px;
  box-shadow: 0 2px 6px rgb(75 54 37 / 7%);
}

.version-history > h3 {
  flex: 0 0 auto;
  padding: 15px 16px;
  font-size: 22px;
  font-weight: 600;
  color: #24211e;
  background: rgb(255 255 255 / 74%);
  border-bottom: 1px solid #eee8e2;
}

.history-scroll {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.history-scroll::-webkit-scrollbar {
  display: none;
}

.version-empty {
  display: grid;
  min-height: 180px;
  padding: 24px;
  color: var(--af-muted);
  text-align: center;
  place-items: center;
}

.version-card {
  position: relative;
  cursor: pointer;
  background: rgb(255 255 255 / 72%);
  border-bottom: 1px solid #e8e1da;
  outline: none;
  transition: background 160ms ease, box-shadow 160ms ease;
}

.version-card:hover,
.version-card:focus-visible {
  background: #fffaf5;
}

.version-card--selected {
  background: #fff8f0;
  box-shadow: inset 3px 0 #b76429;
}

.version-card header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 11px 10px;
}

.version-card header strong {
  overflow: hidden;
  font-size: 16px;
  font-weight: 500;
  color: #292622;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-card time {
  font-size: 11px;
  color: #7e7a75;
  white-space: nowrap;
}

.version-badge {
  display: grid;
  place-items: center;
  min-width: 40px;
  height: 38px;
  padding: 0 7px;
  font-size: 20px;
  font-weight: 600;
  color: #246da8;
  background: #e5f2fc;
  border-radius: 8px;
}

.version-badge[data-tone='violet'] {
  color: #6740b6;
  background: #eee8ff;
}

.version-badge[data-tone='orange'] {
  color: #e05b00;
  background: #fff0d8;
}

.version-badge[data-tone='green'] {
  color: #39824b;
  background: #e5f5e9;
}

.version-body {
  padding: 0 12px 14px;
}

.version-summary {
  padding: 2px 0 10px;
  font-size: 14px;
  line-height: 1.45;
  color: #4f4a44;
}

.version-metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 10px;
}

.version-metadata span {
  padding: 3px 7px;
  font-size: 10px;
  color: #6f6258;
  background: #f1ece7;
  border-radius: 10px;
}

.version-metadata .version-meta--warning {
  color: #8a5b13;
  background: #fff1cf;
}

.version-metadata .version-meta--danger,
.version-metadata .version-meta--blocked {
  color: #a54843;
  background: #fde8e6;
}

.version-metadata .version-meta--ready {
  color: #397245;
  background: #e7f4e9;
}

.change-comparison {
  display: grid;
  gap: 8px;
  padding: 4px 0 10px;
}

.change-comparison > div {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  align-items: center;
  min-width: 0;
}

.change-comparison span {
  padding: 4px 6px;
  font-size: 12px;
  color: #c24343;
  text-align: center;
  background: #fceaea;
}

.change-comparison > div:last-child span {
  color: #4a842b;
  background: #eaf5dc;
}

.change-comparison p {
  overflow: hidden;
  padding-left: 8px;
  font-size: 11px;
  color: #687080;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-impact {
  padding-top: 9px;
  border-top: 1px solid #e8e1da;
}

.version-impact > strong {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 500;
}

.version-impact dl {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 5px;
}

.version-impact dl > div {
  min-width: 0;
  text-align: center;
}

.version-impact dt {
  margin-bottom: 4px;
  font-size: 11px;
}

.version-impact dd {
  font-size: 11px;
  font-weight: 600;
  color: #4e832e;
  white-space: nowrap;
}

.earlier-divider {
  position: relative;
  height: 38px;
  border-bottom: 1px solid #e8e1da;
}

.earlier-divider::before {
  position: absolute;
  top: 50%;
  right: 0;
  left: 0;
  border-top: 1px dashed #b7b1ab;
  content: '';
}

.earlier-divider span {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 1;
  padding: 0 10px;
  font-size: 13px;
  color: #6f6a65;
  background: #fffdfa;
  transform: translate(-50%, -50%);
}

@media (max-width: 1280px) {
  .version-history > h3 {
    font-size: 19px;
  }

  .version-card header {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .version-card time {
    grid-column: 2;
  }

  .version-impact dl {
    grid-template-columns: 1fr;
  }
}
</style>
