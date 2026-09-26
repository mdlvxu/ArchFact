<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import {
  getExtractionSystemPrompt,
  replaceExtractionSystemPrompt,
} from '@/api/modules/extraction'
import { useI18n } from '@/i18n'

const emit = defineEmits<{
  saved: []
}>()

const { t } = useI18n()
const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const content = ref('')
const savedContent = ref('')
const defaultContent = ref('')

const isCustomized = computed(
  () => Boolean(savedContent.value) && savedContent.value.trim() !== defaultContent.value.trim(),
)

function estimatedTokens(value: string) {
  return Math.max(1, Math.ceil(value.length * 1.15))
}

async function loadPrompt() {
  loading.value = true
  try {
    const prompt = await getExtractionSystemPrompt()
    content.value = prompt.content
    savedContent.value = prompt.content
    defaultContent.value = prompt.default_content
  } catch {
    ElMessage.warning(t('systemPrompt.loadFailed'))
  } finally {
    loading.value = false
  }
}

function beginEditing() {
  content.value = savedContent.value
  editing.value = true
}

function cancelEditing() {
  content.value = savedContent.value
  editing.value = false
}

function restoreDefault() {
  content.value = defaultContent.value
}

async function savePrompt() {
  const normalized = content.value.trim()
  if (!normalized || saving.value) return
  saving.value = true
  try {
    const prompt = await replaceExtractionSystemPrompt(normalized)
    content.value = prompt.content
    savedContent.value = prompt.content
    defaultContent.value = prompt.default_content
    editing.value = false
    emit('saved')
    ElMessage.success(t('systemPrompt.saved'))
  } catch {
    ElMessage.error(t('systemPrompt.saveFailed'))
  } finally {
    saving.value = false
  }
}

onMounted(() => void loadPrompt())
</script>

<template>
  <section class="system-prompt-editor setting-card">
    <header>
      <div>
        <h3>{{ t('systemPrompt.title') }}</h3>
        <small>{{ t('systemPrompt.hint') }}</small>
      </div>
      <div class="system-prompt-editor__actions">
        <span :class="{ 'system-prompt-editor__status--custom': isCustomized }">
          {{ isCustomized ? t('systemPrompt.customized') : t('systemPrompt.default') }}
        </span>
        <button v-if="!editing" type="button" :disabled="loading" @click="beginEditing">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 14.8 1.1-3.7L13.7 2.5a1.8 1.8 0 0 1 2.5 2.5l-8.6 8.6L4 14.8Z" /><path d="m11.9 4.3 2.5 2.5M4 14.8l3.6-1.2" /></svg>
          {{ t('common.edit') }}
        </button>
      </div>
    </header>

    <div v-if="loading" class="system-prompt-editor__loading">{{ t('common.loading') }}</div>
    <template v-else>
      <textarea
        v-if="editing"
        v-model="content"
        class="system-prompt-editor__input"
        maxlength="8000"
        :aria-label="t('systemPrompt.title')"
      />
      <pre v-else class="system-prompt-editor__preview">{{ savedContent }}</pre>

      <div class="system-prompt-editor__meta">
        {{ t('systemPrompt.length', { count: content.length, max: 8000, tokens: estimatedTokens(content) }) }}
      </div>

      <footer v-if="editing">
        <button type="button" :disabled="!defaultContent" @click="restoreDefault">{{ t('constraint.defaultPrompt') }}</button>
        <div>
          <button type="button" :disabled="saving" @click="cancelEditing">{{ t('common.cancel') }}</button>
          <button type="button" class="system-prompt-editor__save" :disabled="!content.trim() || saving" @click="savePrompt">
            {{ saving ? t('systemPrompt.saving') : t('systemPrompt.save') }}
          </button>
        </div>
      </footer>
    </template>
  </section>
</template>

<style scoped lang="scss">
.system-prompt-editor { padding: 14px 16px; margin: 0 0 16px; background: rgb(255 255 255 / 76%); border: 1px solid #eadfd4; border-radius: 9px; box-shadow: 0 2px 7px rgb(84 55 34 / 6%); }
.system-prompt-editor header, .system-prompt-editor__actions, .system-prompt-editor footer, .system-prompt-editor footer > div { display: flex; gap: 8px; align-items: center; }
.system-prompt-editor header, .system-prompt-editor footer { justify-content: space-between; }
.system-prompt-editor h3 { margin: 0 0 3px; }
.system-prompt-editor small { color: #897568; }
.system-prompt-editor__actions > span { padding: 3px 7px; font-size: 11px; color: #8b765f; background: #f5efe8; border-radius: 99px; }
.system-prompt-editor__actions > .system-prompt-editor__status--custom { color: #955225; background: #ffead5; }
.system-prompt-editor__actions button, .system-prompt-editor footer button { min-height: 29px; padding: 0 9px; font-size: 12px; color: #8d4c24; cursor: pointer; background: #fff8f1; border: 1px solid #e3c8b2; border-radius: 7px; }
.system-prompt-editor__actions button { display: inline-flex; gap: 4px; align-items: center; }
.system-prompt-editor__actions button:disabled, .system-prompt-editor footer button:disabled { color: #b7aaa0; cursor: not-allowed; background: #f5f2ef; border-color: #e5dfda; }
.system-prompt-editor__actions svg { width: 13px; height: 13px; fill: none; stroke: currentcolor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.45; }
.system-prompt-editor__preview, .system-prompt-editor__input { box-sizing: border-box; width: 100%; min-height: 126px; max-height: 210px; padding: 10px; margin: 11px 0 0; overflow: auto; font: 12px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace; color: #54483f; white-space: pre-wrap; background: #fffdfb; border: 1px solid #eee0d4; border-radius: 7px; }
.system-prompt-editor__input { max-height: none; resize: vertical; outline: none; }
.system-prompt-editor__input:focus { border-color: #bd6535; box-shadow: 0 0 0 3px rgb(189 101 53 / 10%); }
.system-prompt-editor__meta { margin-top: 6px; font-size: 11px; color: #967d6a; text-align: right; }
.system-prompt-editor footer { margin-top: 10px; }
.system-prompt-editor__save { color: #fff !important; background: #a9501a !important; border-color: #a9501a !important; }
.system-prompt-editor__loading { padding: 18px 3px 4px; color: #877365; }
</style>
