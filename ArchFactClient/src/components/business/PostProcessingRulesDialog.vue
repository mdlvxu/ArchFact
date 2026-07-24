<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type { PostProcessingRule } from '@/types/extraction'

interface Props {
  modelValue: boolean
  rules: PostProcessingRule[]
}

const props = defineProps<Props>()
const { t, localize } = useI18n()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  confirm: [rules: PostProcessingRule[]]
}>()

const dialogRef = ref<HTMLElement>()
const draftRules = ref<PostProcessingRule[]>([])
const editingId = ref<string>()
const editName = ref('')
const editDescription = ref('')
const editExample = ref('')
const newName = ref('')
const newDescription = ref('')
const newExample = ref('')
const formError = ref('')

function cloneRules(rules: PostProcessingRule[]) {
  return rules.map((rule) => ({ ...rule }))
}

function closeDialog() {
  emit('update:modelValue', false)
}

function toggleRule(ruleId: string) {
  draftRules.value = draftRules.value.map((rule) =>
    rule.id === ruleId ? { ...rule, enabled: !rule.enabled } : rule,
  )
}

function removeRule(ruleId: string) {
  draftRules.value = draftRules.value.filter((rule) => rule.id !== ruleId)
  if (editingId.value === ruleId) editingId.value = undefined
}

function beginEdit(rule: PostProcessingRule) {
  editingId.value = rule.id
  editName.value = rule.name
  editDescription.value = rule.description
  editExample.value = rule.example
  formError.value = ''
}

function cancelEdit() {
  editingId.value = undefined
}

function saveEdit(ruleId: string) {
  const name = editName.value.trim()
  const description = editDescription.value.trim()
  const example = editExample.value.trim()
  if (!name || !description || !example) {
    formError.value = t('rules.fieldsRequired')
    return
  }
  const duplicated = draftRules.value.some(
    (rule) => rule.id !== ruleId && rule.name.toLowerCase() === name.toLowerCase(),
  )
  if (duplicated) {
    formError.value = t('rules.duplicate', { name })
    return
  }

  draftRules.value = draftRules.value.map((rule) =>
    rule.id === ruleId ? { ...rule, name, description, example } : rule,
  )
  editingId.value = undefined
  formError.value = ''
}

/** 新规则创建后放到列表最上方，便于立即查看和调整开关。 */
function addRule() {
  const name = newName.value.trim()
  const description = newDescription.value.trim()
  const example = newExample.value.trim()

  if (!name || !description || !example) {
    formError.value = t('rules.fieldsRequired')
    return false
  }

  const duplicated = draftRules.value.some((rule) => rule.name.toLowerCase() === name.toLowerCase())
  if (duplicated) {
    formError.value = t('rules.duplicate', { name })
    return false
  }

  const uniqueId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
  draftRules.value = [
    {
      id: `custom-${uniqueId}`,
      key: `custom_rule_${uniqueId.replace(/[^a-zA-Z0-9]/g, '_')}`,
      name,
      description,
      example,
      handler: 'instruction',
      enabled: true,
      builtin: false,
      custom: true,
    },
    ...draftRules.value,
  ]
  newName.value = ''
  newDescription.value = ''
  newExample.value = ''
  formError.value = ''
  return true
}

function confirmRules() {
  const hasPendingRule = Boolean(newName.value.trim() || newDescription.value.trim() || newExample.value.trim())
  if (hasPendingRule && !addRule()) return

  emit('confirm', cloneRules(draftRules.value))
  closeDialog()
}

watch(
  () => props.modelValue,
  async (visible) => {
    if (!visible) return

    draftRules.value = cloneRules(props.rules)
    editingId.value = undefined
    newName.value = ''
    newDescription.value = ''
    newExample.value = ''
    formError.value = ''
    await nextTick()
    dialogRef.value?.focus()
  },
  { immediate: true },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="rules-dialog">
      <div
        v-if="modelValue"
        ref="dialogRef"
        class="rules-dialog-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rules-dialog-title"
        tabindex="-1"
        @click.self="closeDialog"
        @keydown.esc="closeDialog"
      >
        <section class="rules-dialog-card">
          <header class="rules-dialog-header">
            <div>
              <span>{{ t('rules.eyebrow') }}</span>
              <h2 id="rules-dialog-title">{{ t('settings.rules') }}</h2>
            </div>
            <button
              type="button"
              class="confirm-rules-button"
              @click="confirmRules"
            >
              {{ t('common.confirm') }}
            </button>
          </header>

          <div class="rule-dialog-list" :aria-label="t('settings.rules')">
            <article
              v-for="rule in draftRules"
              :key="rule.id"
              class="dialog-rule-item"
              :class="{ 'dialog-rule-item--disabled': !rule.enabled }"
            >
              <template v-if="editingId !== rule.id">
                <button
                  type="button"
                  class="dialog-rule-toggle"
                  :class="{ 'dialog-rule-toggle--enabled': rule.enabled }"
                  :aria-pressed="rule.enabled"
                  :aria-label="`${rule.enabled ? t('common.disable') : t('common.enable')} ${localize(rule.name)}`"
                  @click="toggleRule(rule.id)"
                >
                  <span />
                </button>

                <div class="dialog-rule-content" :title="`${t('common.example')}: ${rule.example}`">
                  <strong>{{ localize(rule.name) }}</strong>
                  <p>{{ localize(rule.description) }}</p>
                </div>

                <div class="dialog-rule-actions">
                  <button
                    type="button"
                    :aria-label="`${t('common.edit')} ${localize(rule.name)}`"
                    @click="beginEdit(rule)"
                  >
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <path d="m4 14-1 4 4-1L17 7l-3-3zM12.5 5.5l3 3" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    :aria-label="`${t('common.delete')} ${localize(rule.name)}`"
                    @click="removeRule(rule.id)"
                  >
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <path d="M4 6h12M8 3h4l1 3H7zM6 6l1 11h6l1-11M9 9v5M11 9v5" />
                    </svg>
                  </button>
                </div>
              </template>

              <form
                v-else
                class="dialog-rule-editor"
                @submit.prevent="saveEdit(rule.id)"
              >
                <input v-model="editName" type="text" maxlength="70" :aria-label="t('common.name')">
                <input v-model="editDescription" type="text" maxlength="220" :aria-label="t('common.description')">
                <input v-model="editExample" type="text" maxlength="160" :aria-label="t('common.example')">
                <div>
                  <button type="button" @click="cancelEdit">{{ t('common.cancel') }}</button>
                  <button type="submit">{{ t('common.save') }}</button>
                </div>
              </form>
            </article>

            <p v-if="draftRules.length === 0" class="rules-empty">
              {{ t('rules.empty') }}
            </p>
          </div>

          <section class="new-rule-card">
            <div class="new-rule-header">
              <div>
                <h3>{{ t('rules.new') }}</h3>
                <span>{{ t('rules.defaultEnabled') }}</span>
              </div>
              <button type="button" @click="addRule">
                <b>+</b> {{ t('common.add') }}
              </button>
            </div>

            <label>
              <span>{{ t('common.name') }}</span>
              <input
                v-model="newName"
                type="text"
                maxlength="70"
                :placeholder="t('rules.namePlaceholder')"
              >
            </label>
            <label>
              <span>{{ t('rules.describe') }}</span>
              <input
                v-model="newDescription"
                type="text"
                maxlength="220"
                :placeholder="t('rules.descriptionPlaceholder')"
              >
            </label>
            <label>
              <span>{{ t('common.example') }}</span>
              <input
                v-model="newExample"
                type="text"
                maxlength="160"
                :placeholder="t('rules.examplePlaceholder')"
                @keydown.enter.prevent="addRule"
              >
            </label>

            <p v-if="formError" class="rule-form-error" role="alert">
              {{ formError }}
            </p>
          </section>

          <p class="rules-dialog-hint">{{ t('rules.hint') }}</p>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped lang="scss">
.rules-dialog-backdrop {
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

.rules-dialog-card {
  width: min(680px, 100%);
  max-height: calc(100vh - 44px);
  padding: 22px;
  overflow-y: auto;
  color: #563a28;
  background:
    radial-gradient(circle at 50% 0, rgb(255 255 255 / 92%), transparent 45%),
    #fffaf4;
  border: 1px solid #dfc8b3;
  border-radius: 13px;
  box-shadow: 0 18px 56px rgb(74 48 28 / 24%);
  scrollbar-width: none;
}

.rules-dialog-card::-webkit-scrollbar,
.rule-dialog-list::-webkit-scrollbar {
  display: none;
}

.rules-dialog-header {
  display: flex;
  gap: 20px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.rules-dialog-header > div > span {
  display: block;
  margin-bottom: 3px;
  font-size: 10px;
  color: #aa7b5b;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.rules-dialog-header h2 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
  color: #251d18;
}

.confirm-rules-button,
.new-rule-header > button {
  min-width: 92px;
  height: 40px;
  padding: 0 14px;
  font-size: var(--af-font-body);
  color: #bd6c36;
  cursor: pointer;
  background: #fffaf4;
  border: 1px solid #c97943;
  border-radius: 8px;
}

.confirm-rules-button:hover,
.new-rule-header > button:hover {
  color: #fff;
  background: #b8662f;
}

.rule-dialog-list {
  display: grid;
  gap: 10px;
  max-height: min(390px, 42vh);
  padding: 1px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.dialog-rule-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) 58px;
  gap: 8px;
  align-items: center;
  min-height: 70px;
  padding: 10px 12px;
  background: rgb(255 255 255 / 82%);
  border: 1px solid #e7d9cc;
  border-radius: 8px;
  box-shadow: 0 2px 6px rgb(88 57 35 / 5%);
}

.dialog-rule-item--disabled .dialog-rule-content {
  opacity: 0.62;
}

.dialog-rule-toggle {
  position: relative;
  width: 28px;
  height: 17px;
  cursor: pointer;
  background: #bbb9b6;
  border: 0;
  border-radius: 10px;
}

.dialog-rule-toggle span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 11px;
  height: 11px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 3px rgb(0 0 0 / 18%);
  transition: transform 0.18s;
}

.dialog-rule-toggle--enabled {
  background: #a45117;
}

.dialog-rule-toggle--enabled span {
  transform: translateX(11px);
}

.dialog-rule-content {
  min-width: 0;
}

.dialog-rule-content strong {
  display: block;
  margin-bottom: 3px;
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  color: #76472a;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dialog-rule-content p {
  overflow: hidden;
  font-size: 12px;
  color: #657080;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dialog-rule-actions {
  display: flex;
  gap: 5px;
  justify-content: flex-end;
}

.dialog-rule-actions button {
  display: grid;
  place-items: center;
  width: 25px;
  height: 25px;
  color: #747e8c;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 4px;
}

.dialog-rule-actions button:hover {
  color: #a45117;
  background: #f8ede3;
}

.dialog-rule-actions svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.7;
}

.dialog-rule-editor {
  display: grid;
  grid-column: 1 / -1;
  gap: 7px;
}

.dialog-rule-editor input,
.new-rule-card input {
  width: 100%;
  height: 40px;
  padding: 0 11px;
  font: inherit;
  font-size: 13px;
  color: #4f443c;
  outline: none;
  background: #fffaf0;
  border: 1px solid #dfa765;
  border-radius: 5px;
}

.dialog-rule-editor input:focus,
.new-rule-card input:focus {
  border-color: #b8642c;
  box-shadow: 0 0 0 3px rgb(184 100 44 / 10%);
}

.dialog-rule-editor > div {
  display: flex;
  gap: 7px;
  justify-content: flex-end;
}

.dialog-rule-editor button {
  padding: 5px 10px;
  font-size: 11px;
  color: #7d5437;
  cursor: pointer;
  background: #f8ede3;
  border: 1px solid #dfc3a9;
  border-radius: 5px;
}

.dialog-rule-editor button[type='submit'] {
  color: #fff;
  background: #a45117;
  border-color: #a45117;
}

.rules-empty {
  display: grid;
  place-items: center;
  min-height: 90px;
  font-size: 12px;
  color: #96897e;
  border: 1px dashed #dac7b6;
  border-radius: 8px;
}

.new-rule-card {
  display: grid;
  gap: 10px;
  padding: 14px;
  margin-top: 12px;
  background: rgb(255 255 255 / 74%);
  border: 1px solid #e4d3c4;
  border-radius: 8px;
  box-shadow: 0 2px 6px rgb(84 55 34 / 5%);
}

.new-rule-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.new-rule-header h3 {
  font-size: var(--af-font-section-title);
  font-weight: 500;
}

.new-rule-header span {
  display: block;
  margin-top: 2px;
  font-size: 9px;
  color: #a08e80;
}

.new-rule-header > button b {
  margin-right: 2px;
  font-size: 20px;
}

.new-rule-card label {
  display: grid;
  gap: 5px;
}

.new-rule-card label > span {
  font-size: 12px;
  color: #7a5035;
}

.new-rule-card input::placeholder {
  color: #a4adbc;
}

.rule-form-error {
  font-size: 11px;
  color: #bb3f3f;
}

.rules-dialog-hint {
  margin-top: 10px;
  font-size: 9px;
  color: #aa9c90;
  text-align: center;
}

.rules-dialog-enter-active,
.rules-dialog-leave-active {
  transition: opacity 0.18s ease;
}

.rules-dialog-enter-active .rules-dialog-card,
.rules-dialog-leave-active .rules-dialog-card {
  transition: transform 0.18s ease;
}

.rules-dialog-enter-from,
.rules-dialog-leave-to {
  opacity: 0;
}

.rules-dialog-enter-from .rules-dialog-card,
.rules-dialog-leave-to .rules-dialog-card {
  transform: translateY(12px) scale(0.98);
}

@media (max-width: 620px) {
  .rules-dialog-backdrop {
    padding: 10px;
  }

  .rules-dialog-card {
    max-height: calc(100vh - 20px);
    padding: 16px;
  }

  .dialog-rule-item {
    grid-template-columns: 32px minmax(0, 1fr);
  }

  .dialog-rule-actions {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
