<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { previewExtractionPrompt, type ExtractionPromptPreview } from '@/api/modules/extraction'
import { useI18n } from '@/i18n'
import type { ExtractionTemplate } from '@/types/extraction'

interface Props {
  template?: ExtractionTemplate
  templates: ExtractionTemplate[]
  systemPromptRevision?: number
}

const props = withDefaults(defineProps<Props>(), {
  systemPromptRevision: 0,
})
const { t, localize } = useI18n()
const preview = ref<ExtractionPromptPreview | null>(null)
const loading = ref(false)
const failed = ref(false)
const activeKey = ref('')
const previewCache = new Map<string, ExtractionPromptPreview>()
const pendingRequests = new Map<string, Promise<ExtractionPromptPreview>>()

const selectedTemplate = computed(() => props.template)

/**
 * Prompt preview is presentation only.  Keep persisted field keys and the
 * extraction schema untouched, while giving the preview service labels in the
 * active UI language.
 */
function toPreviewTemplate(template: ExtractionTemplate): ExtractionTemplate {
  return {
    ...template,
    name: localize(template.name),
    fields: template.fields.map((field) => ({
      ...field,
      label: localize(field.label),
    })),
  }
}

const localizedSelectedTemplate = computed(() =>
  props.template ? toPreviewTemplate(props.template) : undefined,
)

function templateKey(template: ExtractionTemplate) {
  return JSON.stringify({
    id: template.id,
    name: template.name,
    systemPromptRevision: props.systemPromptRevision,
    fields: template.fields.map((field) => ({
      key: field.key,
      label: field.label,
      type: field.type,
      required: field.required,
      instruction: field.instruction,
      evidenceKind: field.evidence_kind,
    })),
  })
}

function requestPreview(template: ExtractionTemplate): Promise<ExtractionPromptPreview> {
  const key = templateKey(template)
  const cached = previewCache.get(key)
  if (cached) return Promise.resolve(cached)
  const pending = pendingRequests.get(key)
  if (pending) return pending
  const request = previewExtractionPrompt(template)
    .then((result) => {
      previewCache.set(key, result)
      return result
    })
    .finally(() => pendingRequests.delete(key))
  pendingRequests.set(key, request)
  return request
}

async function loadPreview(template: ExtractionTemplate, activate = true) {
  const key = templateKey(template)
  if (activate) {
    activeKey.value = key
    loading.value = !previewCache.has(key)
    failed.value = false
  }
  try {
    const result = await requestPreview(template)
    if (activate && activeKey.value === key) preview.value = result
  } catch {
    if (activate && activeKey.value === key) {
      failed.value = true
    }
  } finally {
    if (activate && activeKey.value === key) loading.value = false
  }
}

function preloadOtherTemplates() {
  for (const template of props.templates) {
    if (template.id === selectedTemplate.value?.id) continue
    void loadPreview(toPreviewTemplate(template), false)
  }
}

async function copyPrompt() {
  if (!preview.value) return
  try {
    await navigator.clipboard.writeText(preview.value.composed_prompt)
    ElMessage.success(t('settings.promptCopied'))
  } catch {
    ElMessage.warning(t('settings.promptPreviewFailed'))
  }
}

watch(
  [localizedSelectedTemplate, () => props.systemPromptRevision],
  ([template]) => {
    if (!template) return
    void loadPreview(template)
    preloadOtherTemplates()
  },
  { deep: true, immediate: true },
)

</script>

<template>
  <section class="template-prompt-preview setting-card">
    <header>
      <div>
        <h3>{{ t('settings.promptPreview') }}</h3>
        <small>{{ t('settings.dynamicOcrNote') }}</small>
      </div>
      <div class="template-prompt-preview__actions">
        <span class="template-prompt-preview__template">{{ selectedTemplate ? localize(selectedTemplate.name) : '—' }}</span>
        <button type="button" :disabled="!preview" @click="copyPrompt">{{ t('settings.copyPrompt') }}</button>
      </div>
    </header>

    <div v-if="failed && !preview" class="template-prompt-preview__status template-prompt-preview__status--error">
      {{ t('settings.promptPreviewFailed') }}
    </div>
    <template v-else-if="preview">
      <div class="template-prompt-preview__meta">
        {{ t('settings.approxTokens', { count: preview.estimated_tokens }) }}
        <span v-if="loading">· {{ t('common.loading') }}</span>
      </div>
      <el-collapse class="template-prompt-preview__collapse" :model-value="['composed']">
        <el-collapse-item name="composed" :title="t('settings.composedPrompt')">
          <pre>{{ preview.composed_prompt }}</pre>
        </el-collapse-item>
      </el-collapse>
    </template>
    <div v-else class="template-prompt-preview__status">{{ t('common.loading') }}</div>
  </section>
</template>

<style scoped lang="scss">
.template-prompt-preview { padding: 14px 16px; margin: 0 0 16px; background: rgb(255 255 255 / 76%); border: 1px solid #eadfd4; border-radius: 9px; box-shadow: 0 2px 7px rgb(84 55 34 / 6%); }
.template-prompt-preview header, .template-prompt-preview__actions { display: flex; gap: 9px; align-items: center; }
.template-prompt-preview header { justify-content: space-between; }
.template-prompt-preview h3 { margin: 0 0 3px; }
.template-prompt-preview small { color: #897568; }
.template-prompt-preview__template { max-width: 135px; overflow: hidden; font-size: 12px; color: #876b56; text-overflow: ellipsis; white-space: nowrap; }
.template-prompt-preview__actions button { min-height: 29px; padding: 0 9px; color: #8d4c24; cursor: pointer; background: #fff8f1; border: 1px solid #e3c8b2; border-radius: 7px; }
.template-prompt-preview__actions button:disabled { color: #aa9c91; cursor: not-allowed; }
.template-prompt-preview__status { padding: 14px 4px 5px; color: #877365; }
.template-prompt-preview__status--error { color: #b34d3f; }
.template-prompt-preview__meta { margin-top: 10px; font-size: 12px; color: #9a755b; }
.template-prompt-preview__meta span { color: #b06d42; }
.template-prompt-preview__collapse { margin-top: 3px; border-top: 0; border-bottom: 0; }
.template-prompt-preview__collapse :deep(.el-collapse-item__header) { height: 34px; color: #5b4637; background: transparent; border-bottom-color: #eee1d5; }
.template-prompt-preview__collapse :deep(.el-collapse-item__wrap) { background: transparent; border-bottom-color: #eee1d5; }
.template-prompt-preview pre { max-height: 295px; padding: 10px; margin: 4px 0 9px; overflow: auto; font: 12px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; color: #54483f; background: #fffdfb; border: 1px solid #eee0d4; border-radius: 7px; }
</style>
