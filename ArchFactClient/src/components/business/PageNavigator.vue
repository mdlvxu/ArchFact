<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { formatByteSize, type PdfImportProgress } from '@/domain/pdf-import'
import { useI18n } from '@/i18n'
import type { PdfPageItem } from '@/types/pdf'

/** 左侧页码导航：按需加载 PDF 缩略图并允许切换当前页 */
interface Props {
  pages: PdfPageItem[]
  activePage: number
  total: number
  fileName: string
  importProgress?: PdfImportProgress | null
}

const props = withDefaults(defineProps<Props>(), {
  importProgress: null,
})
const { t } = useI18n()

const emit = defineEmits<{
  select: [page: number]
  thumbnailNeeded: [page: number]
}>()

const pageListRef = ref<HTMLElement>()
let thumbnailObserver: IntersectionObserver | undefined

const importing = computed(() => Boolean(props.importProgress?.active))
const stageLabel = computed(() => {
  const stage = props.importProgress?.stage
  if (stage === 'saving') return t('navigator.saving')
  if (stage === 'parsing') return t('navigator.parsing')
  return t('navigator.uploading')
})

/** 仅为进入可视区域的页码请求缩略图，避免大型 PDF 一次性占用过多内存 */
async function observeVisiblePages() {
  await nextTick()
  thumbnailObserver?.disconnect()
  if (!props.pages.length || !pageListRef.value) return
  if (typeof globalThis.IntersectionObserver !== 'function') return

  thumbnailObserver = new globalThis.IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return

        const page = Number((entry.target as HTMLElement).dataset.page)
        const item = props.pages.find((candidate) => candidate.page === page)
        if (item && !item.thumbnailUrl && !item.loading) {
          emit('thumbnailNeeded', page)
        }
      })
    },
    {
      root: pageListRef.value,
      rootMargin: '180px 0px',
    },
  )

  pageListRef.value
    ?.querySelectorAll<HTMLElement>('[data-page]')
    .forEach((element) => thumbnailObserver?.observe(element))
}

watch(
  () => [props.pages.length, importing.value] as const,
  ([pageCount, isImporting]) => {
    if (!isImporting && pageCount > 0) void observeVisiblePages()
  },
  { immediate: true },
)

onBeforeUnmount(() => thumbnailObserver?.disconnect())
</script>

<template>
  <aside class="page-navigator">
    <h2 class="panel-title">
      {{ t('navigator.title') }}
    </h2>

    <p
      v-if="fileName"
      class="file-name"
      :title="fileName"
    >
      {{ fileName }}
    </p>

    <div
      v-if="importing && importProgress"
      class="import-progress"
      role="status"
      :aria-label="t('navigator.importing')"
    >
      <p class="import-progress__stage">{{ stageLabel }}</p>
      <p class="import-progress__meta">
        {{ formatByteSize(importProgress.fileSize) }}
      </p>

      <div class="import-meter">
        <div class="import-meter__row">
          <span>{{ t('navigator.uploadPercent', { percent: importProgress.uploadPercent }) }}</span>
        </div>
        <div
          class="import-meter__track"
          :class="{ 'import-meter__track--busy': importProgress.stage === 'saving' }"
        >
          <span
            class="import-meter__fill"
            :style="{ width: `${importProgress.uploadPercent}%` }"
          />
        </div>
      </div>

      <div class="import-meter">
        <div class="import-meter__row">
          <span>{{ t('navigator.parsePercent', { percent: importProgress.parsePercent }) }}</span>
        </div>
        <div class="import-meter__track">
          <span
            class="import-meter__fill"
            :style="{ width: `${importProgress.parsePercent}%` }"
          />
        </div>
      </div>
    </div>

    <div
      v-else-if="pages.length"
      ref="pageListRef"
      class="page-list"
    >
      <button
        v-for="item in pages"
        :key="item.page"
        class="page-item"
        :class="{ 'page-item--active': item.page === activePage }"
        type="button"
        :aria-label="t('navigator.open', { page: item.page })"
        :data-page="item.page"
        @click="emit('select', item.page)"
      >
        <img
          v-if="item.thumbnailUrl"
          class="page-thumbnail"
          :src="item.thumbnailUrl"
          :alt="t('navigator.thumbnail', { page: item.page })"
        />
        <div
          v-else
          class="page-placeholder"
          aria-hidden="true"
        >
          <span class="page-placeholder__sheet" />
          <small>{{ item.loading ? t('common.loading') : t('common.page', { page: item.page }) }}</small>
        </div>
        <span class="page-number">{{ item.page }}/{{ total }}</span>
      </button>
    </div>

    <div
      v-else
      class="empty-pages"
    >
      <span class="empty-pages__icon">PDF</span>
      <p>{{ t('navigator.upload') }}</p>
    </div>
  </aside>
</template>

<style scoped lang="scss">
.page-navigator {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  min-height: 0;
  padding: 14px 11px 12px;
  overflow: hidden;
  background: rgb(255 255 255 / 82%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.panel-title {
  flex-shrink: 0;
  margin-bottom: 8px;
  font-size: var(--af-font-panel-title);
  font-weight: 500;
  color: var(--af-heading);
}

.file-name {
  flex-shrink: 0;
  margin-bottom: 9px;
  overflow: hidden;
  font-size: var(--af-font-caption);
  color: var(--af-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-progress {
  display: grid;
  flex: 1;
  gap: 12px;
  min-height: 0;
  padding: 8px 2px 4px;
}

.import-progress__stage {
  font-size: var(--af-font-body);
  font-weight: 600;
  color: var(--af-heading);
}

.import-progress__meta {
  font-size: var(--af-font-caption);
  color: var(--af-muted);
}

.import-meter {
  display: grid;
  gap: 6px;
}

.import-meter__row {
  font-size: var(--af-font-caption);
  color: #6f675f;
}

.import-meter__track {
  height: 8px;
  overflow: hidden;
  background: #efe8e0;
  border: 1px solid #e0d6cb;
  border-radius: 999px;
}

.import-meter__fill {
  display: block;
  height: 100%;
  background: #b26a29;
  border-radius: 999px;
  transition: width 0.2s ease-out;
}

.import-meter__track--busy .import-meter__fill {
  animation: import-busy 1.2s linear infinite;
}

@keyframes import-busy {
  0% {
    opacity: 1;
  }

  50% {
    opacity: 0.45;
  }

  100% {
    opacity: 1;
  }
}

.page-list {
  display: grid;
  flex: 1;
  gap: 10px;
  min-height: 0;
  padding: 2px 2px 20px;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #d6cabd transparent;
}

.page-list::-webkit-scrollbar {
  display: block;
  width: 6px;
}

.page-list::-webkit-scrollbar-thumb {
  background: #d6cabd;
  border-radius: 999px;
}

.page-item:last-child {
  margin-bottom: 4px;
}

.page-item {
  position: relative;
  width: 100%;
  padding: 6px 8px 19px;
  cursor: pointer;
  background: #fff;
  border: 1px solid transparent;
  border-radius: 7px;
  transition:
    border-color 0.2s,
    box-shadow 0.2s;
}

.page-item:hover,
.page-item--active {
  border-color: #d7a979;
  box-shadow: 0 2px 8px rgb(145 91 38 / 12%);
}

.page-thumbnail {
  display: block;
  width: 100%;
  min-height: 100px;
  max-height: 125px;
  object-fit: contain;
  background: #fff;
  border: 1px solid #dedbd4;
}

.page-placeholder {
  display: grid;
  place-items: center;
  width: 100%;
  min-height: 112px;
  color: #a39b92;
  background: #f8f6f3;
  border: 1px solid #e4dfd9;
}

.page-placeholder__sheet {
  width: 35px;
  height: 47px;
  background: #fff;
  border: 1px solid #d8d2ca;
  box-shadow: 0 2px 5px rgb(0 0 0 / 5%);
}

.page-placeholder small {
  font-size: var(--af-font-caption);
}

.page-number {
  position: absolute;
  right: 7px;
  bottom: 4px;
  font-size: var(--af-font-body);
  font-weight: 600;
  color: #3f3c38;
}

.empty-pages {
  display: grid;
  flex: 1;
  place-items: center;
  min-height: 200px;
  color: #9b938a;
  text-align: center;
}

.empty-pages__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 58px;
  margin-bottom: 10px;
  font-size: var(--af-font-caption);
  color: #b26a29;
  background: #fff;
  border: 1px solid #dfd7ce;
  border-radius: 3px;
  box-shadow: 0 3px 9px rgb(81 58 37 / 8%);
}

.empty-pages p {
  font-size: var(--af-font-caption);
  line-height: 1.6;
}
</style>
