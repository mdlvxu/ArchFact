<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useI18n } from '@/i18n'

interface Props {
  label: string
  required: boolean
  instruction: string
  defaultInstruction?: string
}

const props = defineProps<Props>()
const { t } = useI18n()

const emit = defineEmits<{
  'update:instruction': [value: string]
}>()

const isOpen = ref(false)
const menuRef = ref<HTMLElement>()
const menuPosition = ref({ top: 0, left: 0 })
const instructionDraft = ref('')

const MENU_WIDTH = 390
const MENU_HEIGHT = 360
const VIEWPORT_GAP = 12
const TARGET_GAP = 8

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max))
}

function updateMenuPosition(target: HTMLElement) {
  const rect = target.getBoundingClientRect()
  const viewportWidth = globalThis.innerWidth
  const viewportHeight = globalThis.innerHeight
  const rightPosition = rect.right + TARGET_GAP
  const leftPosition = rect.left - MENU_WIDTH - TARGET_GAP
  const left = rightPosition + MENU_WIDTH <= viewportWidth - VIEWPORT_GAP
    ? rightPosition
    : leftPosition >= VIEWPORT_GAP
      ? leftPosition
      : clamp(rect.right - MENU_WIDTH, VIEWPORT_GAP, viewportWidth - MENU_WIDTH - VIEWPORT_GAP)

  menuPosition.value = {
    top: clamp(rect.top - 13, VIEWPORT_GAP, viewportHeight - MENU_HEIGHT - VIEWPORT_GAP),
    left,
  }
}

const isCustomized = computed(() => {
  const instruction = props.instruction.trim()
  const defaultInstruction = (props.defaultInstruction || '').trim()
  return Boolean(instruction && instruction !== defaultInstruction)
})

async function toggleMenu(event: MouseEvent) {
  if (isOpen.value) {
    closeMenu()
    return
  }

  updateMenuPosition(event.currentTarget as HTMLElement)
  instructionDraft.value = props.instruction
  isOpen.value = true
  globalThis.addEventListener('resize', closeMenu)
  await nextTick()
  menuRef.value?.focus()
}

function saveInstruction() {
  emit('update:instruction', instructionDraft.value.trim())
  closeMenu()
}

function restoreDefaultInstruction() {
  if (props.defaultInstruction) instructionDraft.value = props.defaultInstruction
}

function estimatedTokens(value: string) {
  return Math.max(1, Math.ceil(value.length * 1.15))
}

function closeMenu() {
  isOpen.value = false
  globalThis.removeEventListener('resize', closeMenu)
}

onBeforeUnmount(closeMenu)
</script>

<template>
  <button
    class="constraint-trigger"
    :class="{ 'constraint-trigger--open': isOpen }"
    type="button"
    aria-haspopup="dialog"
    :aria-expanded="isOpen"
    :aria-label="`${t('constraint.editPrompt')}：${label}（${isCustomized ? t('constraint.customizedPrompt') : t('constraint.defaultState')}）`"
    :title="t('constraint.editPrompt')"
    @click="toggleMenu"
  >
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m4 14.8 1.1-3.7L13.7 2.5a1.8 1.8 0 0 1 2.5 2.5l-8.6 8.6L4 14.8Z" />
      <path d="m11.9 4.3 2.5 2.5M4 14.8l3.6-1.2" />
    </svg>
  </button>

  <Teleport to="body">
    <div
      v-if="isOpen"
      class="constraint-menu-layer"
      data-testid="constraint-menu-layer"
      @click.self="closeMenu"
    >
      <section
        ref="menuRef"
        class="constraint-type-menu"
        :style="{
          top: `${menuPosition.top}px`,
          left: `${menuPosition.left}px`,
        }"
        tabindex="-1"
        role="dialog"
        aria-modal="true"
        :aria-label="t('constraint.editPrompt')"
        @keydown.esc="closeMenu"
      >
        <div class="constraint-details">
          <header class="constraint-details__header">
            <div>
              <h3>{{ label }}</h3>
              <p>{{ t('constraint.fieldHint') }}</p>
            </div>
            <span>{{ required ? t('constraint.requiredField') : t('constraint.optionalField') }}</span>
          </header>
          <label class="constraint-instruction">
            <span>{{ t('constraint.instruction') }}</span>
            <textarea
              v-model="instructionDraft"
              maxlength="1200"
              :placeholder="t('constraint.instructionPlaceholder')"
              @click.stop
            />
            <small>{{ t('constraint.promptLength', { count: instructionDraft.length, max: 1200, tokens: estimatedTokens(instructionDraft) }) }}</small>
          </label>
          <footer class="constraint-details__actions">
            <button type="button" :disabled="!defaultInstruction" @click="restoreDefaultInstruction">
              {{ t('constraint.defaultPrompt') }}
            </button>
            <div>
              <button type="button" @click="closeMenu">{{ t('common.cancel') }}</button>
              <button type="button" class="constraint-details__save" @click="saveInstruction">{{ t('constraint.savePrompt') }}</button>
            </div>
          </footer>
        </div>

      </section>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.constraint-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 29px;
  min-width: 29px;
  height: 29px;
  min-height: 29px;
  padding: 0;
  color: #8a6650;
  cursor: pointer;
  background: transparent;
  border: 1px solid #eadfd4;
  border-radius: 7px;
  transition: 160ms ease;
}

.constraint-trigger:hover,
.constraint-trigger--open {
  color: #914b20;
  background: #fff1e3;
  border-color: #e5c8aa;
}

.constraint-trigger svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.45;
}

.constraint-menu-layer {
  position: fixed;
  inset: 0;
  z-index: 2999;
  background: transparent;
}

.constraint-type-menu {
  position: fixed;
  z-index: 3000;
  width: 360px;
  max-height: calc(100vh - 24px);
  padding: 0;
  overflow: hidden;
  color: #332f2c;
  background: #fffcf8;
  border: 1px solid #e5d4c2;
  border-radius: 12px;
  box-shadow: 0 12px 30px rgb(83 57 36 / 18%);
  outline: none;
}

.constraint-details {
  display: grid;
  gap: 11px;
  padding: 16px;
}

.constraint-details__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.constraint-details__header h3 { margin: 0 0 3px; font-size: 16px; color: #45382f; }
.constraint-details__header p { margin: 0; font-size: 12px; line-height: 1.45; color: #897568; }
.constraint-details__header span { flex: 0 0 auto; padding: 3px 7px; font-size: 11px; color: #9a633d; background: #fff0df; border-radius: 99px; }

.constraint-instruction { display: grid; gap: 5px; font-size: 12px; color: #715d4e; }
.constraint-instruction textarea {
  min-height: 142px;
  padding: 8px;
  font: inherit;
  line-height: 1.5;
  color: #4f4640;
  resize: vertical;
  outline: none;
  background: #fff;
  border: 1px solid #decab7;
  border-radius: 6px;
}
.constraint-instruction textarea:focus { border-color: #bd6535; box-shadow: 0 0 0 3px rgb(189 101 53 / 10%); }
.constraint-instruction small { font-size: 11px; color: #967d6a; text-align: right; }

.constraint-details__actions, .constraint-details__actions > div { display: flex; gap: 8px; align-items: center; }
.constraint-details__actions { justify-content: space-between; }
.constraint-details__actions button {
  min-height: 30px;
  padding: 0 10px;
  font-size: 12px;
  color: #8d542e;
  cursor: pointer;
  background: #fff8f1;
  border: 1px solid #e4c7af;
  border-radius: 6px;
}
.constraint-details__actions button:disabled { color: #b7aaa0; cursor: not-allowed; background: #f5f2ef; border-color: #e5dfda; }
.constraint-details__actions .constraint-details__save { color: #fff; background: #a9501a; border-color: #a9501a; }
</style>
