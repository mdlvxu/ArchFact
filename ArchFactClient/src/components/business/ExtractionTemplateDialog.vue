<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { createFieldKey } from '@/domain/extraction-config'
import { useI18n } from '@/i18n'
import type { ExtractionTemplate } from '@/types/extraction'

interface Props {
  modelValue: boolean
  selectedTemplateId: string
  templates: ExtractionTemplate[]
}

const props = defineProps<Props>()
const { t, localize } = useI18n()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  confirm: [template: ExtractionTemplate]
}>()

const dialogRef = ref<HTMLElement>()
const selectedId = ref('basic-research')
const newTemplateName = ref('')
const newLabel = ref('')
const newLabels = ref<string[]>([])
const formError = ref('')

const selectedTemplate = computed(
  () => props.templates.find((template) => template.id === selectedId.value) ?? props.templates[0],
)

function closeDialog() {
  emit('update:modelValue', false)
}

function selectTemplate(templateId: string) {
  selectedId.value = templateId
  formError.value = ''
}

function addLabel() {
  const label = newLabel.value.trim()
  if (!label) return

  const duplicated = newLabels.value.some((item) => item.toLowerCase() === label.toLowerCase())
  if (duplicated) {
    formError.value = t('template.duplicate', { label })
    return
  }

  newLabels.value = [...newLabels.value, label]
  newLabel.value = ''
  formError.value = ''
}

function removeLabel(label: string) {
  newLabels.value = newLabels.value.filter((item) => item !== label)
}

/** 有新模板输入时保存新模板，否则确认当前选中的内置模板。 */
function confirmTemplate() {
  const name = newTemplateName.value.trim()
  const isCreatingTemplate = Boolean(name || newLabels.value.length || newLabel.value.trim())

  if (newLabel.value.trim()) addLabel()

  if (isCreatingTemplate) {
    if (!name) {
      formError.value = t('template.nameRequired')
      return
    }
    if (!newLabels.value.length) {
      formError.value = t('template.labelRequired')
      return
    }

    const fieldKeys: string[] = []
    const template: ExtractionTemplate = {
      id: `custom-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`,
      name,
      fields: newLabels.value.map((label) => {
        const key = createFieldKey(label, fieldKeys)
        fieldKeys.push(key)
        return { key, label, type: 'Text', required: false }
      }),
      builtin: false,
      custom: true,
    }
    selectedId.value = template.id
    emit('confirm', template)
  } else if (selectedTemplate.value) {
    emit('confirm', {
      ...selectedTemplate.value,
      fields: selectedTemplate.value.fields.map((field) => ({ ...field })),
    })
  }

  newTemplateName.value = ''
  newLabel.value = ''
  newLabels.value = []
  formError.value = ''
  closeDialog()
}

watch(
  () => props.modelValue,
  async (visible) => {
    if (!visible) return

    if (props.templates.some((template) => template.id === props.selectedTemplateId)) {
      selectedId.value = props.selectedTemplateId
    }
    newTemplateName.value = ''
    newLabel.value = ''
    newLabels.value = []
    formError.value = ''
    await nextTick()
    dialogRef.value?.focus()
  },
  { immediate: true },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="template-dialog">
      <div
        v-if="modelValue"
        ref="dialogRef"
        class="template-dialog-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="template-dialog-title"
        tabindex="-1"
        @click.self="closeDialog"
        @keydown.esc="closeDialog"
      >
        <section class="template-dialog-card">
          <header class="template-dialog-header">
            <div>
              <span>{{ t('template.eyebrow') }}</span>
              <h2 id="template-dialog-title">{{ t('settings.template') }}</h2>
            </div>
            <button
              type="button"
              class="confirm-template-button"
              @click="confirmTemplate"
            >
              {{ t('common.confirm') }}
            </button>
          </header>

          <nav class="template-tabs" :aria-label="t('settings.template')">
            <button
              v-for="template in templates"
              :key="template.id"
              type="button"
              :class="{ 'template-tab--active': template.id === selectedId }"
              @click="selectTemplate(template.id)"
            >
              {{ localize(template.name) }}
              <small v-if="template.custom">{{ t('common.custom') }}</small>
            </button>
          </nav>

          <section v-if="selectedTemplate" class="selected-template-card">
            <div class="selected-template-title">
              <h3>{{ localize(selectedTemplate.name) }}</h3>
              <span>{{ t('template.labels', { count: selectedTemplate.fields.length }) }}</span>
            </div>
            <div class="selected-labels">
              <span v-for="field in selectedTemplate.fields" :key="field.key">
                {{ localize(field.label) }} <b>✓</b>
              </span>
            </div>
          </section>

          <section class="new-template-card">
            <div class="new-template-title">
              <h3>{{ t('template.new') }}</h3>
              <span>{{ t('common.optional') }}</span>
            </div>

            <label>
              <span>{{ t('common.name') }}</span>
              <input
                v-model="newTemplateName"
                type="text"
                maxlength="70"
                :placeholder="t('template.namePlaceholder')"
              >
            </label>

            <label>
              <span>{{ t('common.label') }}</span>
              <div class="label-input-row">
                <input
                  v-model="newLabel"
                  type="text"
                  maxlength="50"
                  :placeholder="t('template.labelPlaceholder')"
                  @keydown.enter.prevent="addLabel"
                >
                <button
                  type="button"
                  :disabled="!newLabel.trim()"
                  @click="addLabel"
                >
                  <b>+</b> {{ t('common.add') }}
                </button>
              </div>
            </label>

            <div v-if="newLabels.length" class="draft-labels" :aria-label="t('common.label')">
              <button
                v-for="label in newLabels"
                :key="label"
                type="button"
                :aria-label="`${t('common.delete')} ${label}`"
                @click="removeLabel(label)"
              >
                {{ label }} <b>×</b>
              </button>
            </div>

            <p v-if="formError" class="template-form-error" role="alert">
              {{ formError }}
            </p>
          </section>

          <p class="dialog-hint">{{ t('template.hint') }}</p>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped lang="scss">
.template-dialog-backdrop {
  position: fixed;
  z-index: 3000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  overflow-y: auto;
  background: rgb(38 31 25 / 30%);
  backdrop-filter: blur(3px);
  outline: none;
}

.template-dialog-card {
  width: min(760px, 100%);
  max-height: calc(100vh - 48px);
  padding: 28px 30px 22px;
  overflow-y: auto;
  color: #443930;
  background:
    radial-gradient(circle at 50% 0, rgb(255 255 255 / 90%), transparent 42%),
    #fffaf4;
  border: 1px solid #dfc8b3;
  border-radius: 14px;
  box-shadow: 0 18px 56px rgb(74 48 28 / 24%);
  scrollbar-width: none;
}

.template-dialog-card::-webkit-scrollbar {
  display: none;
}

.template-dialog-header {
  display: flex;
  gap: 20px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 22px;
}

.template-dialog-header span {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  color: #ac8060;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.template-dialog-header h2 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
  color: #211c18;
}

.confirm-template-button {
  min-width: 112px;
  height: 44px;
  padding: 0 18px;
  font-size: var(--af-font-body);
  color: #bd6c36;
  cursor: pointer;
  background: #fffaf4;
  border: 1px solid #c97943;
  border-radius: 9px;
}

.confirm-template-button:hover {
  color: #fff;
  background: #b8662f;
}

.template-tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.template-tabs button {
  min-width: 0;
  min-height: 60px;
  padding: 9px;
  font-size: 14px;
  line-height: 1.2;
  color: #a85f2b;
  cursor: pointer;
  background: #faf0df;
  border: 1px solid #e2c39e;
  border-radius: 8px;
}

.template-tabs button:hover,
.template-tabs .template-tab--active {
  color: #fff;
  background: #ae6127;
  border-color: #ae6127;
  box-shadow: 0 4px 9px rgb(139 76 31 / 16%);
}

.template-tabs small {
  display: block;
  margin-top: 3px;
  font-size: 9px;
  opacity: 0.8;
}

.selected-template-card,
.new-template-card {
  padding: 16px;
  background: rgb(255 255 255 / 76%);
  border: 1px solid #eadfd4;
  border-radius: 9px;
  box-shadow: 0 2px 7px rgb(84 55 34 / 6%);
}

.selected-template-card {
  margin-bottom: 16px;
}

.selected-template-title,
.new-template-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 13px;
}

.selected-template-title h3,
.new-template-title h3 {
  font-size: var(--af-font-section-title);
  font-weight: 500;
}

.selected-template-title span,
.new-template-title span {
  padding: 3px 7px;
  font-size: 10px;
  color: #9a6a43;
  background: #f8ebdc;
  border-radius: 9px;
}

.selected-labels,
.draft-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 12px;
}

.selected-labels > span {
  padding: 8px 10px;
  font-size: 13px;
  color: #5f4e40;
  background: #fbe0b3;
  border-radius: 10px;
}

.selected-labels b {
  color: #a45c24;
}

.new-template-card {
  display: grid;
  gap: 13px;
}

.new-template-card label {
  display: grid;
  gap: 6px;
}

.new-template-card label > span {
  font-size: 14px;
  color: #75513a;
}

.new-template-card input {
  width: 100%;
  height: 48px;
  padding: 0 14px;
  font: inherit;
  font-size: var(--af-font-body);
  color: #4a4038;
  outline: none;
  background: #fffaf0;
  border: 2px solid #dfad70;
  border-radius: 5px;
}

.new-template-card input:focus {
  border-color: #b96830;
  box-shadow: 0 0 0 3px rgb(185 104 48 / 10%);
}

.new-template-card input::placeholder {
  color: #a4adbc;
}

.label-input-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
}

.label-input-row > button {
  min-width: 100px;
  font-size: var(--af-font-body);
  color: #bd6c36;
  cursor: pointer;
  background: #fff;
  border: 1px solid #c97943;
  border-radius: 5px;
}

.label-input-row > button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.label-input-row > button b {
  margin-right: 3px;
  font-size: 23px;
}

.draft-labels button {
  padding: 6px 9px;
  font-size: 12px;
  color: #75513a;
  cursor: pointer;
  background: #f8e6ce;
  border: 1px solid #e6c197;
  border-radius: 9px;
}

.draft-labels b {
  margin-left: 4px;
  color: #b25825;
}

.template-form-error {
  font-size: 12px;
  color: #bb3f3f;
}

.dialog-hint {
  margin-top: 12px;
  font-size: 10px;
  color: #ab9d91;
  text-align: center;
}

.template-dialog-enter-active,
.template-dialog-leave-active {
  transition: opacity 0.18s ease;
}

.template-dialog-enter-active .template-dialog-card,
.template-dialog-leave-active .template-dialog-card {
  transition: transform 0.18s ease;
}

.template-dialog-enter-from,
.template-dialog-leave-to {
  opacity: 0;
}

.template-dialog-enter-from .template-dialog-card,
.template-dialog-leave-to .template-dialog-card {
  transform: translateY(12px) scale(0.98);
}

@media (max-width: 720px) {
  .template-dialog-backdrop {
    padding: 10px;
  }

  .template-dialog-card {
    max-height: calc(100vh - 20px);
    padding: 20px 16px 16px;
  }

  .template-tabs {
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .label-input-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .label-input-row > button {
    height: 42px;
  }
}
</style>
