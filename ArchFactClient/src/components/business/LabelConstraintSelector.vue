<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
import { useI18n } from '@/i18n'
import type { LabelConstraintType } from '@/types/extraction'

interface Props {
  label: string
  modelValue: LabelConstraintType
  required: boolean
  instruction: string
}

defineProps<Props>()
const { t, localize } = useI18n()

const emit = defineEmits<{
  'update:modelValue': [value: LabelConstraintType]
  'update:required': [value: boolean]
  'update:instruction': [value: string]
}>()

const constraintTypes: LabelConstraintType[] = [
  'Num',
  'Text',
  'Date',
  'Yes/No',
  'Image',
  'Obj',
  'Arr',
]

const isOpen = ref(false)
const menuRef = ref<HTMLElement>()
const menuPosition = ref({ top: 0, left: 0 })

const MENU_WIDTH = 260
const MENU_HEIGHT = 470
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

async function toggleMenu(event: MouseEvent) {
  if (isOpen.value) {
    closeMenu()
    return
  }

  updateMenuPosition(event.currentTarget as HTMLElement)
  isOpen.value = true
  globalThis.addEventListener('resize', closeMenu)
  await nextTick()
  menuRef.value?.focus()
}

function selectType(type: LabelConstraintType) {
  emit('update:modelValue', type)
  closeMenu()
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
    aria-haspopup="listbox"
    :aria-expanded="isOpen"
    :aria-label="t('constraint.type', { label, type: localize(modelValue) })"
    @click="toggleMenu"
  >
    <span>{{ localize(modelValue) }}</span>
    <b aria-hidden="true">›</b>
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
        role="listbox"
        :aria-label="t('settings.constraints')"
        @keydown.esc="closeMenu"
      >
        <h3>{{ t('settings.constraints') }}</h3>
        <button
          v-for="type in constraintTypes"
          :key="type"
          class="constraint-option"
          :class="{ 'constraint-option--active': type === modelValue }"
          type="button"
          role="option"
          :aria-selected="type === modelValue"
          @click="selectType(type)"
        >
          <svg
            class="constraint-option__icon"
            viewBox="0 0 20 20"
            aria-hidden="true"
          >
            <template v-if="type === 'Num'">
              <path d="M3.5 16.5V11m4 5.5V5m4 11.5V8m4 8.5V3.5" />
            </template>
            <template v-else-if="type === 'Text'">
              <path d="M5 2.8h6.5l3.5 3.5v10.9H5z" />
              <path d="M11.5 2.8v3.5H15M7.5 9h5M7.5 12h5M7.5 15h3.5" />
            </template>
            <template v-else-if="type === 'Date'">
              <rect x="3" y="4.5" width="14" height="12" rx="1.5" />
              <path d="M6.5 2.5v4M13.5 2.5v4M3 8h14M6.5 11h.1M10 11h.1M13.5 11h.1M6.5 14h.1M10 14h.1" />
            </template>
            <template v-else-if="type === 'Yes/No'">
              <circle cx="10" cy="10" r="7" />
              <path d="m6.8 10 2.1 2.2 4.5-5" />
            </template>
            <template v-else-if="type === 'Image'">
              <rect x="3" y="3.5" width="14" height="13" rx="1.5" />
              <circle cx="7" cy="7.5" r="1.5" />
              <path d="m4.5 15 4.2-4.4 2.5 2.3 2-2 3.3 3.4" />
            </template>
            <template v-else-if="type === 'Obj'">
              <circle
                v-for="point in 9"
                :key="point"
                :cx="4 + ((point - 1) % 3) * 6"
                :cy="4 + Math.floor((point - 1) / 3) * 6"
                r="1.1"
              />
            </template>
            <template v-else>
              <path d="M7 5h10M7 10h10M7 15h10" />
              <circle cx="3.5" cy="5" r=".8" />
              <circle cx="3.5" cy="10" r=".8" />
              <circle cx="3.5" cy="15" r=".8" />
            </template>
          </svg>
          <span>{{ localize(type) }}</span>
          <b
            v-if="type === modelValue"
            aria-hidden="true"
          >✓</b>
        </button>

        <div class="constraint-details">
          <label class="constraint-required">
            <input
              type="checkbox"
              :checked="required"
              @change="emit('update:required', ($event.target as HTMLInputElement).checked)"
            >
            {{ t('common.required') }}
          </label>
          <label class="constraint-instruction">
            <span>{{ t('constraint.instruction') }}</span>
            <textarea
              :value="instruction"
              maxlength="500"
              :placeholder="t('constraint.instructionPlaceholder')"
              @input="emit('update:instruction', ($event.target as HTMLTextAreaElement).value)"
            />
          </label>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.constraint-trigger {
  display: flex;
  gap: 7px;
  align-items: center;
  justify-content: flex-end;
  min-width: 68px;
  min-height: 32px;
  padding: 5px 9px;
  font-size: var(--af-font-body);
  color: #665f59;
  cursor: pointer;
  background: #f7f8fa;
  border: 1px solid transparent;
  border-radius: 10px;
  transition: 160ms ease;
}

.constraint-trigger:hover,
.constraint-trigger--open {
  color: #914b20;
  background: #fff7ee;
  border-color: #e5c8aa;
}

.constraint-trigger b {
  font-size: 21px;
  font-weight: 400;
  line-height: 0.8;
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
  width: 260px;
  padding: 8px 0 7px;
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
  gap: 9px;
  padding: 10px 16px 4px;
  border-top: 1px solid #eee1d5;
}

.constraint-required {
  display: flex;
  gap: 7px;
  align-items: center;
  font-size: 12px;
  color: #5c5149;
}

.constraint-required input {
  accent-color: #a45117;
}

.constraint-instruction {
  display: grid;
  gap: 5px;
  font-size: 11px;
  color: #7a6d63;
}

.constraint-instruction textarea {
  min-height: 70px;
  padding: 7px;
  font: inherit;
  line-height: 1.35;
  color: #4f4640;
  resize: vertical;
  outline: none;
  background: #fff;
  border: 1px solid #decab7;
  border-radius: 6px;
}

.constraint-instruction textarea:focus {
  border-color: #bd6535;
}

.constraint-type-menu h3 {
  padding: 0 21px 9px;
  margin: 0;
  font-size: var(--af-font-section-title);
  font-weight: 600;
}

.constraint-option {
  display: grid;
  grid-template-columns: 24px 1fr 18px;
  gap: 4px;
  align-items: center;
  width: 100%;
  min-height: 39px;
  padding: 6px 16px;
  font-size: var(--af-font-body);
  color: #3d3732;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  transition: background-color 140ms ease;
}

.constraint-option:hover,
.constraint-option--active {
  background: #f8eee5;
}

.constraint-option b {
  font-size: 16px;
  font-weight: 500;
  color: #bd6535;
}

.constraint-option__icon {
  width: 17px;
  height: 17px;
  overflow: visible;
  fill: none;
  stroke: #bd6535;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.15;
}

.constraint-option__icon circle[r='1.1'] {
  fill: #bd6535;
  stroke: none;
}
</style>
