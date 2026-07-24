<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type { PdfPageItem } from '@/types/pdf'

interface Props {
  pages: PdfPageItem[]
  activePage: number
  total: number
}

const props = defineProps<Props>()
const { t } = useI18n()

const emit = defineEmits<{
  select: [page: number]
  thumbnailNeeded: [page: number]
}>()

const overviewRef = ref<HTMLElement>()
let thumbnailObserver: IntersectionObserver | undefined

/** 只加载总览中可见的微缩页，长文档也不会一次创建数百张图片。 */
async function observeVisiblePages() {
  await nextTick()
  thumbnailObserver?.disconnect()

  thumbnailObserver = new globalThis.IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return

        const page = Number((entry.target as HTMLElement).dataset.overviewPage)
        const item = props.pages.find((candidate) => candidate.page === page)
        if (item && !item.thumbnailUrl && !item.loading) {
          emit('thumbnailNeeded', page)
        }
      })
    },
    {
      root: overviewRef.value,
      rootMargin: '260px 0px',
    },
  )

  overviewRef.value
    ?.querySelectorAll<HTMLElement>('[data-overview-page]')
    .forEach((element) => thumbnailObserver?.observe(element))
}

/** 当前预览页变化时，将其滚动到总览视口中央并确保缩略图已请求。 */
async function revealActivePage() {
  await nextTick()
  const container = overviewRef.value
  const activeElement = container?.querySelector<HTMLElement>(
    `[data-overview-page="${props.activePage}"]`,
  )

  if (!container || !activeElement) return

  const targetTop = activeElement.offsetTop - (container.clientHeight - activeElement.offsetHeight) / 2
  container.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' })

  const item = props.pages.find((candidate) => candidate.page === props.activePage)
  if (item && !item.thumbnailUrl && !item.loading) {
    emit('thumbnailNeeded', props.activePage)
  }
}

watch(() => props.pages.length, observeVisiblePages, { immediate: true })
watch(() => props.activePage, revealActivePage, { immediate: true })

onBeforeUnmount(() => thumbnailObserver?.disconnect())
</script>

<template>
  <aside class="pdf-overview panel">
    <div class="overview-header">
      <h2>{{ t('overview.title') }}</h2>
      <span v-if="pages.length">{{ activePage }}/{{ total }}</span>
    </div>

    <div
      v-if="pages.length"
      ref="overviewRef"
      class="overview-scroll"
      :aria-label="t('overview.title')"
    >
      <div class="overview-track">
        <button
          v-for="item in pages"
          :key="item.page"
          type="button"
          class="overview-page"
          :class="{ 'overview-page--active': item.page === activePage }"
          :data-overview-page="item.page"
          :aria-label="t('overview.open', { page: item.page })"
          @click="emit('select', item.page)"
        >
          <img
            v-if="item.thumbnailUrl"
            :src="item.thumbnailUrl"
            :alt="t('overview.thumbnail', { page: item.page })"
          >
          <span
            v-else
            class="overview-placeholder"
            aria-hidden="true"
          >
            <i v-for="line in 9" :key="line" />
          </span>
          <b>{{ item.page }}</b>
        </button>
      </div>
    </div>

    <div v-else class="overview-empty">
      <span>PDF</span>
      <p>{{ t('overview.empty') }}</p>
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

.pdf-overview {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 10px 7px 8px;
  overflow: hidden;
}

.overview-header {
  display: flex;
  gap: 4px;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 8px;
}

.overview-header h2 {
  overflow: hidden;
  font-size: var(--af-font-body);
  font-weight: 500;
  color: var(--af-heading);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-header span {
  flex: 0 0 auto;
  font-size: 12px;
  color: #9d7b5c;
}

.overview-scroll {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  background: #eeeae5;
  border: 1px solid #e3ddd6;
  border-radius: 4px;
  scrollbar-color: #bfae9e transparent;
  scrollbar-width: thin;
}

.overview-scroll::-webkit-scrollbar {
  display: block;
  width: 4px;
}

.overview-scroll::-webkit-scrollbar-thumb {
  background: #bfae9e;
  border-radius: 4px;
}

.overview-track {
  display: grid;
  gap: 2px;
  padding: 2px;
}

.overview-page {
  position: relative;
  width: 100%;
  height: 102px;
  padding: 0;
  overflow: hidden;
  cursor: pointer;
  background: #fff;
  border: 0;
  border-radius: 1px;
}

.overview-page img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  filter: grayscale(0.78) contrast(0.8);
  opacity: 0.72;
}

.overview-placeholder {
  display: grid;
  gap: 6px;
  align-content: center;
  width: 100%;
  height: 100%;
  padding: 10px 7px;
  background: #faf9f7;
}

.overview-placeholder i {
  display: block;
  height: 2px;
  background: #d9d4ce;
  border-radius: 2px;
}

.overview-placeholder i:nth-child(3n) {
  width: 72%;
}

.overview-page b {
  position: absolute;
  right: 3px;
  bottom: 2px;
  padding: 1px 3px;
  font-size: 10px;
  font-weight: 600;
  color: #5a5148;
  background: rgb(255 255 255 / 82%);
  border-radius: 2px;
}

.overview-page--active {
  z-index: 1;
  box-shadow:
    inset 0 4px 0 #8bcbdc,
    inset 0 -4px 0 #e89a9f,
    inset 0 0 0 2px #7eb3c4;
}

.overview-page--active::after {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: '';
  background: rgb(123 203 221 / 12%);
}

.overview-empty {
  display: grid;
  flex: 1;
  place-items: center;
  align-content: center;
  color: #9b938a;
  text-align: center;
}

.overview-empty span {
  display: grid;
  place-items: center;
  width: 42px;
  height: 54px;
  margin-bottom: 9px;
  font-size: 12px;
  color: #b26a29;
  background: #fff;
  border: 1px solid #dfd7ce;
}

.overview-empty p {
  font-size: 12px;
  line-height: 1.5;
}
</style>
