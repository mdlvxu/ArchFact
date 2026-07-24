<script setup lang="ts">
import { nextTick, ref } from 'vue'
import VerificationRuleDialog from '@/components/business/VerificationRuleDialog.vue'
import { useI18n } from '@/i18n'
import type { VerificationRule } from '@/types/verification'

interface Props {
  rules: VerificationRule[]
  running: boolean
}

const props = defineProps<Props>()
const { localize, t } = useI18n()

const emit = defineEmits<{
  'update:rules': [rules: VerificationRule[]]
  execute: []
}>()

const ruleDialogVisible = ref(false)
const editingRule = ref<VerificationRule>()
const rulesScrollRef = ref<HTMLElement>()

function updateRule(ruleId: number, changes: Partial<VerificationRule>) {
  emit(
    'update:rules',
    props.rules.map((rule) => (rule.id === ruleId ? { ...rule, ...changes } : rule)),
  )
}

function toggleRule(rule: VerificationRule) {
  updateRule(rule.id, { enabled: !rule.enabled })
}

function removeRule(ruleId: number) {
  emit(
    'update:rules',
    props.rules.filter((rule) => rule.id !== ruleId),
  )
}

function openAddDialog() {
  editingRule.value = undefined
  ruleDialogVisible.value = true
}

function openEditDialog(rule: VerificationRule) {
  editingRule.value = { ...rule }
  ruleDialogVisible.value = true
}

async function saveRule(draft: Pick<VerificationRule, 'title' | 'description'>) {
  if (editingRule.value) {
    updateRule(editingRule.value.id, {
      ...draft,
      updated: true,
    })
    return
  }

  const nextId = Math.max(0, ...props.rules.map((rule) => rule.id)) + 1
  const newRule: VerificationRule = {
    id: nextId,
    ...draft,
    enabled: true,
    updated: true,
  }
  emit('update:rules', [newRule, ...props.rules])
  await nextTick()
  if (rulesScrollRef.value) rulesScrollRef.value.scrollTop = 0
}
</script>

<template>
  <section class="assertion-rules panel">
    <div class="assertion-header">
      <div>
        <h2>{{ t('verification.assertions') }}</h2>
        <p>{{ t('verification.activeRules', { count: rules.filter((rule) => rule.enabled).length }) }}</p>
      </div>
      <button
        type="button"
        class="execute-button"
        :disabled="running || !rules.some((rule) => rule.enabled)"
        @click="emit('execute')"
      >
        <span v-if="running" class="execute-spinner" />
        {{ running ? t('verification.executing') : t('verification.execute') }}
      </button>
    </div>

    <div
      ref="rulesScrollRef"
      class="rules-scroll"
      :aria-label="t('verification.ruleList')"
    >
      <article
        v-for="rule in rules"
        :key="rule.id"
        class="rule-card"
        :class="{ 'rule-card--disabled': !rule.enabled }"
      >
        <button
          type="button"
          class="rule-toggle"
          :class="{ 'rule-toggle--enabled': rule.enabled }"
          :aria-pressed="rule.enabled"
          :aria-label="`${rule.enabled ? t('common.disable') : t('common.enable')} ${localize(rule.title)}`"
          @click="toggleRule(rule)"
        >
          <span />
        </button>

        <div class="rule-content">
          <div class="rule-title">
            <strong>{{ localize(rule.title) }}</strong>
            <span v-if="rule.updated">{{ t('verification.updated') }}</span>
          </div>
          <p>{{ localize(rule.description) }}</p>
        </div>

        <div class="rule-actions">
          <button
            type="button"
            :aria-label="`${t('common.edit')} ${localize(rule.title)}`"
            @click="openEditDialog(rule)"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="m4 14-1 4 4-1L17 7l-3-3zM12.5 5.5l3 3" />
            </svg>
          </button>
          <button
            type="button"
            :aria-label="`${t('common.delete')} ${localize(rule.title)}`"
            @click="removeRule(rule.id)"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="M4 6h12M8 3h4l1 3H7zM6 6l1 11h6l1-11M9 9v5M11 9v5" />
            </svg>
          </button>
        </div>
      </article>
    </div>

    <button
      type="button"
      class="add-rule-button"
      @click="openAddDialog"
    >
      <b>+</b>
      {{ t('verification.addRule') }}
    </button>
  </section>

  <VerificationRuleDialog
    v-model="ruleDialogVisible"
    :rule="editingRule"
    @save="saveRule"
  />
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.assertion-rules {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 12px 14px;
  overflow: hidden;
}

.assertion-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 12px;
}

.assertion-header h2 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
  color: var(--af-heading);
}

.assertion-header p {
  margin-top: 2px;
  font-size: 11px;
  color: var(--af-muted);
}

.execute-button {
  display: flex;
  gap: 7px;
  align-items: center;
  justify-content: center;
  min-width: 125px;
  height: 34px;
  padding: 0 14px;
  font-size: var(--af-font-body);
  color: #fff;
  cursor: pointer;
  background: linear-gradient(135deg, #9f4b11, #dc6d15);
  border: 0;
  border-radius: 8px;
  box-shadow: 0 3px 7px rgb(142 66 14 / 20%);
}

.execute-button:disabled {
  cursor: not-allowed;
  filter: grayscale(0.35);
  opacity: 0.65;
}

.execute-spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgb(255 255 255 / 45%);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.rules-scroll {
  display: grid;
  flex: 1;
  gap: 10px;
  align-content: start;
  min-height: 0;
  padding: 1px 2px 8px;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.rules-scroll::-webkit-scrollbar {
  display: none;
}

.rule-card {
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) 58px;
  gap: 8px;
  min-height: 128px;
  padding: 15px 12px;
  background: rgb(255 255 255 / 84%);
  border: 1px solid #e8e0d8;
  border-radius: 8px;
  transition:
    opacity 0.2s,
    border-color 0.2s;
}

.rule-card:hover {
  border-color: #d8b28c;
}

.rule-card--disabled {
  opacity: 0.54;
}

.rule-toggle {
  position: relative;
  width: 31px;
  height: 18px;
  margin-top: 3px;
  cursor: pointer;
  background: #bbb3ab;
  border: 0;
  border-radius: 10px;
  transition: background 0.2s;
}

.rule-toggle span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 12px;
  height: 12px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 3px rgb(0 0 0 / 22%);
  transition: transform 0.2s;
}

.rule-toggle--enabled {
  background: #a45117;
}

.rule-toggle--enabled span {
  transform: translateX(13px);
}

.rule-content {
  min-width: 0;
}

.rule-title {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 9px;
}

.rule-title strong {
  font-size: 15px;
  font-weight: 600;
  color: #856b4c;
}

.rule-title span {
  padding: 2px 6px;
  font-size: 10px;
  color: #4b9b3e;
  border: 1px solid #5bb44e;
  border-radius: 8px;
}

.rule-content p {
  font-size: 13px;
  line-height: 1.55;
  color: #657082;
}

.rule-actions {
  display: flex;
  gap: 5px;
  align-items: flex-start;
  justify-content: flex-end;
}

.rule-actions button {
  display: grid;
  place-items: center;
  width: 25px;
  height: 25px;
  color: #737c89;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 5px;
}

.rule-actions button:hover {
  color: #a45117;
  background: #f7eee6;
}

.rule-actions svg {
  width: 17px;
  height: 17px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.7;
}

.add-rule-button {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  justify-content: center;
  height: 38px;
  margin: 4px 2px 0;
  font-size: 14px;
  color: #a25a20;
  cursor: pointer;
  background: #fffdfa;
  border: 1px dashed #bb7440;
  border-radius: 6px;
}

.add-rule-button:hover {
  background: #faf0e7;
}

.add-rule-button b {
  font-size: 22px;
  line-height: 1;
}
</style>
