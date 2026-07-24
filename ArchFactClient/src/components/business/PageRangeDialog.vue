<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type { PdfPageItem } from '@/types/pdf'

interface Props {
  modelValue: boolean
  pages: PdfPageItem[]
  selectedPages: number[]
}

const props = defineProps<Props>()
const { t } = useI18n()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  confirm: [pages: number[]]
  thumbnailNeeded: [page: number]
}>()

const dialogRef = ref<HTMLElement>()
const pageGridRef = ref<HTMLElement>()
const draftSelection = ref<number[]>([])
const pageRangeInput = ref('')
const rangeError = ref('')
const parityMode = ref<'odd' | 'even' | ''>('')
const gridScrollTop = ref(0)
const gridViewportHeight = ref(590)
const gridColumnCount = ref(globalThis.innerWidth <= 760 ? 3 : 4)

const VIRTUAL_ROW_HEIGHT = 170
const VIRTUAL_OVERSCAN_ROWS = 2

const selectedPageSet = computed(() => new Set(draftSelection.value))
const totalVirtualRows = computed(() => Math.ceil(props.pages.length / gridColumnCount.value))
const firstVirtualRow = computed(() => Math.max(
  0,
  Math.floor(gridScrollTop.value / VIRTUAL_ROW_HEIGHT) - VIRTUAL_OVERSCAN_ROWS,
))
const visibleVirtualRowCount = computed(() =>
  Math.ceil(gridViewportHeight.value / VIRTUAL_ROW_HEIGHT) + VIRTUAL_OVERSCAN_ROWS * 2,
)
const visiblePages = computed(() => {
  const start = firstVirtualRow.value * gridColumnCount.value
  const end = Math.min(
    props.pages.length,
    start + visibleVirtualRowCount.value * gridColumnCount.value,
  )
  return props.pages.slice(start, end)
})
const virtualGridHeight = computed(() => totalVirtualRows.value * VIRTUAL_ROW_HEIGHT)
const virtualWindowTop = computed(() => firstVirtualRow.value * VIRTUAL_ROW_HEIGHT)

function normalizePages(pages: Iterable<number>) {
  const validPages = new Set(
    [...pages].filter((page) => Number.isInteger(page) && page >= 1 && page <= props.pages.length),
  )
  return [...validPages].sort((left, right) => left - right)
}

function setSelection(pages: Iterable<number>) {
  draftSelection.value = normalizePages(pages)
  rangeError.value = ''
}

function togglePage(page: number) {
  const nextSelection = new Set(draftSelection.value)
  if (nextSelection.has(page)) nextSelection.delete(page)
  else nextSelection.add(page)
  setSelection(nextSelection)
}

function selectAll() {
  setSelection(props.pages.map((page) => page.page))
}

function clearSelection() {
  setSelection([])
}

function invertSelection() {
  const currentSelection = selectedPageSet.value
  setSelection(props.pages.filter((page) => !currentSelection.has(page.page)).map((page) => page.page))
}

function applyParity() {
  if (!parityMode.value) return
  const remainder = parityMode.value === 'odd' ? 1 : 0
  setSelection(props.pages.filter((page) => page.page % 2 === remainder).map((page) => page.page))
  parityMode.value = ''
}

/** 解析单页和闭区间组合，例如 1-5, 10, 12-15。 */
function parsePageRange(value: string) {
  const segments = value
    .split(',')
    .map((segment) => segment.trim())
    .filter(Boolean)

  if (!segments.length) throw new Error(t('range.inputRequired'))

  const result = new Set<number>()
  segments.forEach((segment) => {
    const singlePage = segment.match(/^\d+$/)
    const pageRange = segment.match(/^(\d+)\s*-\s*(\d+)$/)

    if (singlePage) {
      const page = Number(segment)
      if (page < 1 || page > props.pages.length) {
        throw new Error(t('range.pageOutside', { page }))
      }
      result.add(page)
      return
    }

    if (pageRange) {
      const start = Number(pageRange[1])
      const end = Number(pageRange[2])
      if (start > end) throw new Error(t('range.invalidOrder', { range: segment }))
      if (start < 1 || end > props.pages.length) {
        throw new Error(t('range.rangeOutside', { range: segment, total: props.pages.length }))
      }
      for (let page = start; page <= end; page += 1) result.add(page)
      return
    }

    throw new Error(t('range.unsupported', { range: segment }))
  })

  return normalizePages(result)
}

function applyPageRange() {
  try {
    setSelection(parsePageRange(pageRangeInput.value))
    pageRangeInput.value = ''
    return true
  } catch (error: unknown) {
    rangeError.value = error instanceof Error ? error.message : t('range.invalid')
    return false
  }
}

function closeDialog() {
  emit('update:modelValue', false)
}

function confirmSelection() {
  if (pageRangeInput.value.trim() && !applyPageRange()) return
  if (!draftSelection.value.length) {
    rangeError.value = t('range.selectionRequired')
    return
  }
  emit('confirm', [...draftSelection.value])
  closeDialog()
}

/** 使用固定行高窗口化页面网格，并只请求当前窗口附近的缩略图。 */
async function updateVirtualViewport() {
  await nextTick()
  if (!props.modelValue || !pageGridRef.value) return
  gridViewportHeight.value = pageGridRef.value.clientHeight || 590
  gridColumnCount.value = globalThis.innerWidth <= 760 ? 3 : 4
}

function handleGridScroll(event: Event) {
  gridScrollTop.value = (event.currentTarget as HTMLElement).scrollTop
}

function requestVisibleThumbnails() {
  if (!props.modelValue) return
  visiblePages.value.forEach((item) => {
    if (!item.thumbnailUrl && !item.loading) emit('thumbnailNeeded', item.page)
  })
}

watch(
  [() => props.modelValue, () => props.pages.length],
  async ([visible]) => {
    if (!visible) {
      return
    }

    draftSelection.value = normalizePages(props.selectedPages)
    pageRangeInput.value = ''
    rangeError.value = ''
    parityMode.value = ''
    gridScrollTop.value = 0
    await updateVirtualViewport()
    if (pageGridRef.value) pageGridRef.value.scrollTop = 0
    requestVisibleThumbnails()
    dialogRef.value?.focus()
  },
  { immediate: true },
)

watch(visiblePages, requestVisibleThumbnails, { flush: 'post' })

globalThis.addEventListener('resize', updateVirtualViewport)
onBeforeUnmount(() => globalThis.removeEventListener('resize', updateVirtualViewport))
</script>

<template>
  <Teleport to="body">
    <Transition name="page-range-dialog">
      <div
        v-if="modelValue"
        ref="dialogRef"
        class="page-range-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="page-range-title"
        tabindex="-1"
        @click.self="closeDialog"
        @keydown.esc="closeDialog"
      >
        <section class="page-range-card">
          <header>
            <div>
              <span>{{ t('range.eyebrow') }}</span>
              <h2 id="page-range-title">{{ t('range.title') }}</h2>
            </div>
            <b>{{ t('common.pages', { count: pages.length }) }}</b>
          </header>

          <div class="page-range-content">
            <div
              v-if="pages.length"
              ref="pageGridRef"
              class="page-selection-grid"
              :aria-label="t('settings.selectPage')"
              @scroll="handleGridScroll"
            >
              <div
                class="virtual-page-spacer"
                :style="{ height: `${virtualGridHeight}px` }"
              >
                <div
                  class="virtual-page-window"
                  :style="{ transform: `translateY(${virtualWindowTop}px)` }"
                >
                  <button
                    v-for="item in visiblePages"
                    :key="item.page"
                    type="button"
                    class="page-selection-item"
                    :class="{ 'page-selection-item--selected': selectedPageSet.has(item.page) }"
                    :data-range-page="item.page"
                    :aria-pressed="selectedPageSet.has(item.page)"
                    :aria-label="`${selectedPageSet.has(item.page) ? t('range.clear') : t('settings.selectPage')} ${t('common.page', { page: item.page })}`"
                    @click="togglePage(item.page)"
                  >
                    <span class="page-selection-preview">
                      <img
                        v-if="item.thumbnailUrl"
                        :src="item.thumbnailUrl"
                        :alt="`PDF ${t('common.page', { page: item.page })}`"
                      >
                      <span v-else class="page-selection-placeholder" aria-hidden="true">
                        <i v-for="line in 7" :key="line" />
                      </span>
                      <b v-if="selectedPageSet.has(item.page)">✓</b>
                    </span>
                    <small>{{ t('common.page', { page: item.page }) }}</small>
                  </button>
                </div>
              </div>
            </div>

            <div v-else class="page-range-empty">
              <span>PDF</span>
              <h3>{{ t('range.noPages') }}</h3>
              <p>{{ t('range.uploadFirst') }}</p>
            </div>

            <aside class="page-range-actions">
              <section>
                <h3>{{ t('range.quickActions') }}</h3>
                <div class="quick-action-grid">
                  <button type="button" :disabled="!pages.length" @click="selectAll">{{ t('range.selectAll') }}</button>
                  <button type="button" :disabled="!pages.length" @click="clearSelection">{{ t('range.clear') }}</button>
                  <button type="button" :disabled="!pages.length" @click="invertSelection">{{ t('range.invert') }}</button>
                  <select
                    v-model="parityMode"
                    :disabled="!pages.length"
                    :aria-label="t('range.oddEven')"
                    @change="applyParity"
                  >
                    <option value="">{{ t('range.oddEven') }}</option>
                    <option value="odd">{{ t('range.odd') }}</option>
                    <option value="even">{{ t('range.even') }}</option>
                  </select>
                </div>
              </section>

              <section class="range-input-section">
                <h3>{{ t('range.enter') }}</h3>
                <div>
                  <input
                    v-model="pageRangeInput"
                    type="text"
                    :disabled="!pages.length"
                    :placeholder="t('range.placeholder')"
                    :aria-label="t('range.enter')"
                    @keydown.enter.prevent="applyPageRange"
                  >
                  <button
                    type="button"
                    :disabled="!pages.length || !pageRangeInput.trim()"
                    @click="applyPageRange"
                  >
                    {{ t('range.apply') }}
                  </button>
                </div>
                <p>{{ t('range.formatHint') }}</p>
                <strong v-if="rangeError" role="alert">{{ rangeError }}</strong>
              </section>

              <div class="selected-page-count">
                <span>✓</span>
                <strong>{{ t('range.selected', { selected: draftSelection.length, total: pages.length }) }}</strong>
              </div>

              <footer>
                <button type="button" @click="closeDialog">{{ t('common.cancel') }}</button>
                <button
                  type="button"
                  class="confirm-pages-button"
                  :disabled="!draftSelection.length"
                  @click="confirmSelection"
                >
                  {{ t('common.confirm') }}
                </button>
              </footer>
            </aside>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped lang="scss">
.page-range-backdrop {
  position: fixed;
  z-index: 3000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 22px;
  overflow-y: auto;
  background: rgb(38 31 25 / 30%);
  backdrop-filter: blur(3px);
  outline: none;
}

.page-range-card {
  width: min(900px, 100%);
  max-height: calc(100vh - 44px);
  padding: 24px;
  overflow-y: auto;
  color: #4c4139;
  background:
    radial-gradient(circle at 50% 0, rgb(255 255 255 / 92%), transparent 45%),
    #fffaf4;
  border: 1px solid #dfc8b3;
  border-radius: 14px;
  box-shadow: 0 18px 56px rgb(74 48 28 / 24%);
  scrollbar-width: none;
}

.page-range-card::-webkit-scrollbar,
.page-selection-grid::-webkit-scrollbar {
  display: none;
}

.page-range-card > header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 18px;
}

.page-range-card > header span {
  display: block;
  margin-bottom: 3px;
  font-size: 10px;
  color: #aa7b5b;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.page-range-card > header h2 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
  color: #251d18;
}

.page-range-card > header b {
  font-size: 12px;
  font-weight: 500;
  color: #9b7456;
}

.page-range-content {
  display: grid;
  grid-template-columns: minmax(420px, 1.55fr) minmax(280px, 0.9fr);
  gap: 26px;
  min-height: 490px;
}

.page-selection-grid {
  position: relative;
  max-height: 590px;
  padding: 2px 4px 10px 2px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.virtual-page-spacer {
  position: relative;
  width: 100%;
}

.virtual-page-window {
  position: absolute;
  inset: 0 0 auto;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-auto-rows: 156px;
  gap: 14px 10px;
  align-content: start;
  will-change: transform;
}

.page-selection-item {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 0;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.page-selection-preview {
  position: relative;
  display: block;
  height: 138px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #dfe2e6;
  border-radius: 6px;
}

.page-selection-item:hover .page-selection-preview,
.page-selection-item--selected .page-selection-preview {
  border: 2px solid #a65016;
  box-shadow: 0 3px 8px rgb(133 73 30 / 12%);
}

.page-selection-preview img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.page-selection-placeholder {
  display: grid;
  gap: 8px;
  align-content: center;
  width: 100%;
  height: 100%;
  padding: 14px 10px;
  background: #fbfaf8;
}

.page-selection-placeholder i {
  height: 2px;
  background: #dedad5;
  border-radius: 2px;
}

.page-selection-placeholder i:nth-child(3n) {
  width: 72%;
}

.page-selection-preview > b {
  position: absolute;
  top: 5px;
  right: 5px;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  font-size: 12px;
  color: #fff;
  background: #9e4a12;
  border-radius: 50%;
}

.page-selection-item > small {
  overflow: hidden;
  font-size: 11px;
  color: #626b78;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-range-actions {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding-top: 5px;
}

.page-range-actions h3 {
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 500;
  color: #534840;
}

.quick-action-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 9px;
}

.quick-action-grid button,
.quick-action-grid select,
.range-input-section button,
.page-range-actions footer button {
  height: 40px;
  padding: 0 10px;
  font: inherit;
  font-size: 13px;
  color: #3e3935;
  cursor: pointer;
  background: #fff;
  border: 1px solid #dfe3e9;
  border-radius: 6px;
}

.quick-action-grid button:hover,
.quick-action-grid select:hover,
.range-input-section button:hover {
  color: #a45117;
  border-color: #d3a278;
}

.quick-action-grid button:disabled,
.quick-action-grid select:disabled,
.range-input-section button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.range-input-section {
  margin-top: 22px;
}

.range-input-section > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 7px;
}

.range-input-section input {
  min-width: 0;
  height: 43px;
  padding: 0 10px;
  font: inherit;
  font-size: 12px;
  color: #534b45;
  outline: none;
  background: #fff;
  border: 1px solid #dfe3e9;
  border-radius: 6px;
}

.range-input-section input:focus {
  border-color: #c47c46;
  box-shadow: 0 0 0 3px rgb(196 124 70 / 10%);
}

.range-input-section input::placeholder {
  color: #aab2c0;
}

.range-input-section button {
  height: 43px;
}

.range-input-section > p {
  margin-top: 7px;
  font-size: 10px;
  line-height: 1.45;
  color: #a2a9b6;
}

.range-input-section > strong {
  display: block;
  margin-top: 7px;
  font-size: 10px;
  font-weight: 500;
  color: #bd4545;
}

.selected-page-count {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: auto;
  padding: 24px 2px 18px;
  font-size: 14px;
  color: #675c53;
}

.selected-page-count span {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  color: #a45117;
  border: 1px solid #a45117;
  border-radius: 50%;
}

.selected-page-count strong {
  font-size: 16px;
  color: #a45117;
}

.page-range-actions footer {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.page-range-actions footer .confirm-pages-button {
  color: #bd6c36;
  border-color: #c97943;
}

.page-range-actions footer .confirm-pages-button:hover {
  color: #fff;
  background: #b8662f;
}

.page-range-actions footer button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.page-range-empty {
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 360px;
  color: #948b82;
  text-align: center;
  border: 1px dashed #d9c6b4;
  border-radius: 8px;
}

.page-range-empty > span {
  display: grid;
  place-items: center;
  width: 54px;
  height: 68px;
  margin-bottom: 12px;
  color: #a85f2b;
  background: #fff;
  border: 1px solid #dfd5ca;
}

.page-range-empty h3 {
  margin-bottom: 5px;
  font-size: 15px;
  font-weight: 500;
}

.page-range-empty p {
  font-size: 11px;
}

.page-range-dialog-enter-active,
.page-range-dialog-leave-active {
  transition: opacity 0.18s ease;
}

.page-range-dialog-enter-active .page-range-card,
.page-range-dialog-leave-active .page-range-card {
  transition: transform 0.18s ease;
}

.page-range-dialog-enter-from,
.page-range-dialog-leave-to {
  opacity: 0;
}

.page-range-dialog-enter-from .page-range-card,
.page-range-dialog-leave-to .page-range-card {
  transform: translateY(12px) scale(0.98);
}

@media (max-width: 760px) {
  .page-range-backdrop {
    padding: 10px;
  }

  .page-range-card {
    max-height: calc(100vh - 20px);
    padding: 16px;
  }

  .page-range-content {
    grid-template-columns: 1fr;
  }

  .virtual-page-window {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .page-selection-grid {
    max-height: 430px;
  }

  .selected-page-count {
    margin-top: 10px;
  }
}
</style>
