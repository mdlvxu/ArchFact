<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type { VerificationRule } from '@/types/verification'

interface Props {
  modelValue: boolean
  rule?: VerificationRule
}

const props = defineProps<Props>()
const { t } = useI18n()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [rule: Pick<VerificationRule, 'title' | 'description'>]
}>()

const ruleName = ref('')
const description = ref('')
const attempted = ref(false)
const nameInputRef = ref<HTMLInputElement>()
const isEditing = computed(() => Boolean(props.rule))
const isValid = computed(() => Boolean(ruleName.value.trim() && description.value.trim()))

watch(
  () => props.modelValue,
  async (visible) => {
    if (!visible) return

    ruleName.value = props.rule?.title ?? ''
    description.value = props.rule?.description ?? ''
    attempted.value = false
    await nextTick()
    nameInputRef.value?.focus()
  },
)

function closeDialog() {
  emit('update:modelValue', false)
}

function saveRule() {
  attempted.value = true
  if (!isValid.value) return

  emit('save', {
    title: ruleName.value.trim(),
    description: description.value.trim(),
  })
  closeDialog()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="rule-dialog-fade">
      <div
        v-if="modelValue"
        class="rule-dialog-backdrop"
        data-testid="verification-rule-dialog"
        @click.self="closeDialog"
        @keydown.esc="closeDialog"
      >
        <form
          class="rule-dialog"
          role="dialog"
          aria-modal="true"
          :aria-label="isEditing ? t('verification.modifyRule') : t('verification.addRule')"
          @submit.prevent="saveRule"
        >
          <h2>{{ isEditing ? t('verification.modifyRule') : t('verification.addRule') }}</h2>

          <label for="verification-rule-name">{{ t('verification.ruleName') }}</label>
          <input
            id="verification-rule-name"
            ref="nameInputRef"
            v-model="ruleName"
            type="text"
            maxlength="60"
            autocomplete="off"
            :placeholder="t('verification.ruleNamePlaceholder')"
            :aria-invalid="attempted && !ruleName.trim()"
          >
          <small v-if="attempted && !ruleName.trim()">{{ t('verification.ruleNameRequired') }}</small>

          <label for="verification-rule-description">{{ t('verification.ruleDescription') }}</label>
          <textarea
            id="verification-rule-description"
            v-model="description"
            rows="4"
            maxlength="220"
            :placeholder="t('verification.ruleDescriptionPlaceholder')"
            :aria-invalid="attempted && !description.trim()"
          />
          <small v-if="attempted && !description.trim()">{{ t('verification.ruleDescriptionRequired') }}</small>

          <div class="rule-dialog__actions">
            <button
              type="button"
              class="rule-dialog__cancel"
              @click="closeDialog"
            >
              {{ t('common.cancel') }}
            </button>
            <button
              type="submit"
              class="rule-dialog__save"
            >
              {{ t('common.save') }}
            </button>
          </div>
        </form>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped lang="scss">
.rule-dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 3200;
  display: grid;
  place-items: center;
  padding: 16px;
  background: rgb(52 43 35 / 24%);
  backdrop-filter: blur(1px);
}

.rule-dialog {
  width: min(520px, calc(100vw - 32px));
  padding: 22px 26px 18px;
  color: #3e3935;
  background: #fffefa;
  border: 1px solid #decdbc;
  border-radius: 10px;
  box-shadow: 0 16px 42px rgb(58 42 29 / 24%);
}

.rule-dialog h2 {
  margin: 0 0 18px;
  font-size: var(--af-font-page-title);
  font-weight: 500;
  color: #352f2b;
}

.rule-dialog label {
  display: block;
  margin: 0 0 7px;
  font-size: var(--af-font-body);
  font-weight: 500;
  color: #49423d;
}

.rule-dialog input,
.rule-dialog textarea {
  display: block;
  width: 100%;
  padding: 9px 11px;
  font: inherit;
  font-size: var(--af-font-body);
  color: #4e4842;
  outline: none;
  resize: none;
  background: #fff;
  border: 1px solid #d7cec3;
  border-radius: 5px;
  box-shadow: inset 0 1px 2px rgb(66 48 34 / 4%);
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease;
}

.rule-dialog input {
  height: 38px;
  margin-bottom: 18px;
}

.rule-dialog textarea {
  min-height: 94px;
}

.rule-dialog input::placeholder,
.rule-dialog textarea::placeholder {
  color: #a8afb9;
}

.rule-dialog input:focus,
.rule-dialog textarea:focus {
  border-color: #c2743f;
  box-shadow: 0 0 0 3px rgb(194 116 63 / 11%);
}

.rule-dialog input[aria-invalid='true'],
.rule-dialog textarea[aria-invalid='true'] {
  border-color: #c55c4e;
}

.rule-dialog small {
  display: block;
  margin: 5px 0 10px;
  font-size: 12px;
  color: #b3483c;
}

.rule-dialog__actions {
  display: flex;
  gap: 9px;
  justify-content: flex-end;
  margin-top: 9px;
}

.rule-dialog__actions button {
  min-width: 72px;
  height: 34px;
  padding: 0 14px;
  font-size: var(--af-font-body);
  cursor: pointer;
  background: #fff;
  border: 1px solid #cfc6bc;
  border-radius: 5px;
}

.rule-dialog__cancel {
  color: #7c756f;
}

.rule-dialog__save {
  color: #c35f2d;
  border-color: #d36b36 !important;
}

.rule-dialog__actions button:hover {
  background: #faf4ed;
}

.rule-dialog-fade-enter-active,
.rule-dialog-fade-leave-active {
  transition: opacity 160ms ease;
}

.rule-dialog-fade-enter-active .rule-dialog,
.rule-dialog-fade-leave-active .rule-dialog {
  transition: transform 160ms ease;
}

.rule-dialog-fade-enter-from,
.rule-dialog-fade-leave-to {
  opacity: 0;
}

.rule-dialog-fade-enter-from .rule-dialog,
.rule-dialog-fade-leave-to .rule-dialog {
  transform: translateY(8px) scale(0.985);
}
</style>
