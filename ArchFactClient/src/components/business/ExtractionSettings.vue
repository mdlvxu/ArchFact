<script setup lang="ts">
import { Check, Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  getExtractionTemplates,
  getPostProcessingRules,
  replaceExtractionTemplates,
  replacePostProcessingRules,
} from '@/api/modules/extraction'
import ExtractionTemplateDialog from '@/components/business/ExtractionTemplateDialog.vue'
import LabelConstraintSelector from '@/components/business/LabelConstraintSelector.vue'
import PageRangeDialog from '@/components/business/PageRangeDialog.vue'
import PostProcessingRulesDialog from '@/components/business/PostProcessingRulesDialog.vue'
import {
  defaultExtractionTemplates,
  defaultPostProcessingRules,
} from '@/domain/extraction-defaults'
import { buildExtractionConfig } from '@/domain/extraction-config'
import { useI18n } from '@/i18n'
import type {
  ExtractionConfigPayload,
  ExtractionTemplate,
  ExtractionTemplateField,
  PostProcessingRule,
} from '@/types/extraction'
import type { PdfPageItem } from '@/types/pdf'

interface Props {
  pages?: PdfPageItem[]
  selectedPages?: number[]
}

const props = withDefaults(defineProps<Props>(), {
  pages: () => [],
  selectedPages: () => [],
})

const { t, localize } = useI18n()

const emit = defineEmits<{
  extract: [config: ExtractionConfigPayload]
  'update:selectedPages': [pages: number[]]
  thumbnailNeeded: [page: number]
}>()

const templateDialogVisible = ref(false)
const rulesDialogVisible = ref(false)
const pageRangeDialogVisible = ref(false)
const configurationLoading = ref(false)
const templates = ref(defaultExtractionTemplates.map(cloneTemplate))
const activeTemplate = ref(cloneTemplate(templates.value[0]!))
const rules = ref(defaultPostProcessingRules.map((rule) => ({ ...rule })))
let templateSaveTimer: number | undefined

function cloneTemplate(template: ExtractionTemplate): ExtractionTemplate {
  return {
    ...template,
    fields: template.fields.map((field) => ({ ...field })),
  }
}

const enabledRules = computed(() => rules.value.filter((rule) => rule.enabled))

/** 将 UI 状态转换为稳定的后端契约，组件外部无需理解弹框内部状态。 */
function requestExtraction() {
  emit(
    'extract',
    buildExtractionConfig({
      template: activeTemplate.value,
      constraints: activeTemplate.value.fields,
      rules: rules.value,
      pages: props.selectedPages,
    }),
  )
}

/** 删除指定后处理规则 */
function removeRule(ruleId: string) {
  rules.value = rules.value.filter((rule) => rule.id !== ruleId)
  void persistRules()
}

/** 应用弹框中选中或新建的抽取模板。 */
function applyTemplate(template: ExtractionTemplate) {
  const savedTemplate = cloneTemplate(template)
  activeTemplate.value = savedTemplate
  const existingIndex = templates.value.findIndex((item) => item.id === template.id)
  if (existingIndex === -1) templates.value = [savedTemplate, ...templates.value]
  else templates.value = templates.value.map((item) =>
    item.id === template.id ? savedTemplate : item,
  )
  scheduleTemplateSave()
}

function updateTemplateField(fieldKey: string, patch: Partial<ExtractionTemplateField>) {
  activeTemplate.value = {
    ...activeTemplate.value,
    fields: activeTemplate.value.fields.map((field) =>
      field.key === fieldKey ? { ...field, ...patch } : field,
    ),
  }
  templates.value = templates.value.map((template) =>
    template.id === activeTemplate.value.id ? cloneTemplate(activeTemplate.value) : template,
  )
  scheduleTemplateSave()
}

/** 仅在弹框确认后替换当前后处理规则，关闭弹框不会保存草稿。 */
function applyRules(updatedRules: PostProcessingRule[]) {
  rules.value = updatedRules.map((rule) => ({ ...rule }))
  void persistRules()
}

function applyPageSelection(pages: number[]) {
  emit('update:selectedPages', [...pages])
}

function scheduleTemplateSave() {
  if (templateSaveTimer !== undefined) globalThis.clearTimeout(templateSaveTimer)
  templateSaveTimer = globalThis.setTimeout(() => {
    void persistTemplates()
  }, 400)
}

async function persistTemplates() {
  try {
    await replaceExtractionTemplates(templates.value)
  } catch {
    ElMessage.warning(t('settings.templateSaveFailed'))
  }
}

async function persistRules() {
  try {
    await replacePostProcessingRules(rules.value)
  } catch {
    ElMessage.warning(t('settings.ruleSaveFailed'))
  }
}

async function loadConfiguration() {
  configurationLoading.value = true
  try {
    const [savedTemplates, savedRules] = await Promise.all([
      getExtractionTemplates(),
      getPostProcessingRules(),
    ])
    if (savedTemplates.length) {
      templates.value = savedTemplates.map(cloneTemplate)
      const selected = templates.value.find((template) => template.id === activeTemplate.value.id)
      activeTemplate.value = cloneTemplate(selected ?? templates.value[0]!)
    }
    rules.value = savedRules.map((rule) => ({ ...rule }))
  } catch {
    ElMessage.warning(t('settings.fallbackConfig'))
  } finally {
    configurationLoading.value = false
  }
}

onMounted(() => void loadConfiguration())
onBeforeUnmount(() => {
  if (templateSaveTimer !== undefined) globalThis.clearTimeout(templateSaveTimer)
})
</script>

<template>
  <!-- 右侧抽取设置区：集中配置模板、字段约束、规则和页码范围 -->
  <aside class="settings panel">
    <div class="settings__header">
      <h2>{{ t('settings.prompt') }}</h2>
      <el-button
        class="extract-button"
        size="small"
        :loading="configurationLoading"
        @click="requestExtraction"
      >
        {{ t('settings.start') }}
      </el-button>
    </div>

    <section class="setting-card template-card">
      <h3>{{ t('settings.template') }}</h3>
      <div class="template-card__title">
        <strong>{{ localize(activeTemplate.name) }}</strong>
        <button
          type="button"
          @click="templateDialogVisible = true"
        >
          {{ t('settings.manage') }}
        </button>
      </div>
      <div class="tag-list">
        <span
          v-for="field in activeTemplate.fields"
          :key="field.key"
        >{{ localize(field.label) }}<b v-if="field.required">*</b></span>
      </div>
      <button
        class="dashed-action"
        type="button"
        data-testid="add-template"
        @click="templateDialogVisible = true"
      >
        <el-icon><Plus /></el-icon>
        {{ t('settings.addTemplate') }}
      </button>
    </section>

    <section class="setting-card">
      <h3>{{ t('settings.constraints') }}</h3>
      <div class="constraint-grid">
        <div
          v-for="item in activeTemplate.fields"
          :key="item.key"
          class="constraint-row"
        >
          <span :title="item.instruction || localize(item.label)">
            {{ localize(item.label) }}<b v-if="item.required">*</b>
          </span>
          <LabelConstraintSelector
            :label="localize(item.label)"
            :model-value="item.type"
            :required="item.required"
            :instruction="item.instruction || ''"
            @update:model-value="updateTemplateField(item.key, { type: $event })"
            @update:required="updateTemplateField(item.key, { required: $event })"
            @update:instruction="updateTemplateField(item.key, { instruction: $event })"
          />
        </div>
      </div>
    </section>

    <section class="setting-card">
      <h3>{{ t('settings.rules') }}</h3>
      <div class="rule-list">
        <article
          v-for="rule in enabledRules"
          :key="rule.id"
          class="rule-item"
        >
          <div>
            <strong>{{ localize(rule.name) }}</strong>
            <p>{{ localize(rule.description) }}</p>
            <small>{{ t('common.example') }}: {{ rule.example }}</small>
          </div>
          <button
            type="button"
            :aria-label="`${t('common.delete')} ${localize(rule.name)}`"
            @click="removeRule(rule.id)"
          >
            <el-icon><Delete /></el-icon>
          </button>
        </article>
      </div>
      <button
        class="dashed-action"
        type="button"
        data-testid="add-rules"
        @click="rulesDialogVisible = true"
      >
        <el-icon><Plus /></el-icon>
        {{ t('settings.addRules') }}
      </button>
    </section>

    <section class="setting-card range-card">
      <div class="range-card__title">
        <h3>{{ t('settings.pageRange') }}</h3>
        <span><el-icon><Check /></el-icon> {{ t('settings.pagesSelected', { selected: selectedPages.length, total: pages.length }) }}</span>
      </div>
      <button
        class="dashed-action"
        type="button"
        data-testid="select-page"
        @click="pageRangeDialogVisible = true"
      >
        <el-icon><Plus /></el-icon>
        {{ t('settings.selectPage') }}
      </button>
    </section>
  </aside>

  <ExtractionTemplateDialog
    v-model="templateDialogVisible"
    :templates="templates"
    :selected-template-id="activeTemplate.id"
    @confirm="applyTemplate"
  />

  <PostProcessingRulesDialog
    v-model="rulesDialogVisible"
    :rules="rules"
    @confirm="applyRules"
  />

  <PageRangeDialog
    v-model="pageRangeDialogVisible"
    :pages="pages"
    :selected-pages="selectedPages"
    @confirm="applyPageSelection"
    @thumbnail-needed="emit('thumbnailNeeded', $event)"
  />
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.settings {
  min-width: 0;
  padding: 10px;
  overflow-y: auto;
}

.settings__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 8px 8px;
}

.settings__header h2 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
}

.extract-button {
  font-size: var(--af-font-body);
  color: #fff;
  background: #a65016;
  border-color: #a65016;
  box-shadow: 0 2px 4px rgb(125 59 17 / 22%);
}

.extract-button:hover {
  color: #fff;
  background: #bf6821;
  border-color: #bf6821;
}

.setting-card {
  padding: 9px;
  margin-bottom: 9px;
  background: rgb(255 255 255 / 70%);
  border: 1px solid #ebe5de;
  border-radius: 8px;
}

.setting-card h3 {
  margin-bottom: 9px;
  font-size: var(--af-font-section-title);
  font-weight: 600;
  color: #39342f;
}

.template-card__title,
.range-card__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.template-card__title {
  padding: 0 6px 8px;
}

.template-card__title strong {
  font-size: var(--af-font-body);
  font-weight: 500;
}

.template-card__title button {
  font-size: var(--af-font-body);
  color: #a56a40;
  cursor: pointer;
  background: none;
  border: 0;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 10px;
}

.tag-list span {
  padding: 3px 7px;
  font-size: var(--af-font-body);
  color: #806c5d;
  background: #f7eee3;
  border-radius: 10px;
}

.dashed-action {
  display: flex;
  gap: 5px;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 26px;
  font-size: var(--af-font-body);
  color: #9a622f;
  cursor: pointer;
  background: transparent;
  border: 1px dashed #c8a47f;
  border-radius: 5px;
}

.constraint-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 17px;
}

.constraint-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 5px;
  align-items: center;
  min-width: 0;
  font-size: var(--af-font-body);
}

.constraint-row > span {
  overflow: hidden;
  color: #625c56;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rule-list {
  display: grid;
  gap: 5px;
  margin-bottom: 7px;
}

.rule-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 66px;
  padding: 7px 8px;
  background: #fff;
  border-radius: 5px;
}

.rule-item strong {
  display: block;
  margin-bottom: 2px;
  font-size: var(--af-font-body);
  font-weight: 500;
  color: #6f513a;
}

.rule-item p {
  font-size: 13px;
  color: #9a938c;
}

.rule-item small {
  display: block;
  margin-top: 3px;
  overflow: hidden;
  font-size: var(--af-font-micro);
  color: #b0a79e;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rule-item button {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 25px;
  height: 25px;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.range-card {
  margin-bottom: 0;
}

.range-card__title span {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: var(--af-font-caption);
  color: #80603f;
}

@media (max-width: 980px) {
  .settings {
    max-height: none;
  }
}
</style>
