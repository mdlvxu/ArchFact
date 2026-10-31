<script setup lang="ts">
import LanguageToggle from '@/components/business/LanguageToggle.vue'
import { useI18n } from '@/i18n'

type WorkspaceTab =
  | 'Data Extraction'
  | 'Data Preview'
  | 'Machine Verification'
  | 'Cross-report Evaluation'
  | 'Data Insights'

defineProps<{
  activeTab: WorkspaceTab
}>()

const emit = defineEmits<{
  navigate: [tab: WorkspaceTab]
}>()

const { t } = useI18n()
const tabs: Array<{ id: WorkspaceTab; label: string }> = [
  { id: 'Data Extraction', label: 'nav.extraction' },
  { id: 'Data Preview', label: 'nav.preview' },
  { id: 'Machine Verification', label: 'nav.verification' },
  { id: 'Cross-report Evaluation', label: 'nav.evaluation' },
  { id: 'Data Insights', label: 'nav.insights' },
]
</script>

<template>
  <div class="workspace">
    <header class="app-header">
      <h1>ArchFact</h1>
    </header>

    <nav
      class="top-nav"
      :aria-label="t('nav.workflow')"
    >
      <div class="top-nav__tabs">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          :class="{ 'top-nav__tab--active': activeTab === tab.id }"
          :aria-current="activeTab === tab.id ? 'page' : undefined"
          @click="emit('navigate', tab.id)"
        >
          {{ t(tab.label) }}
        </button>
      </div>
      <div class="top-nav__actions">
        <LanguageToggle />
        <slot name="actions" />
      </div>
    </nav>

    <slot />
  </div>
</template>

<style scoped lang="scss">
.workspace {
  --af-heading: #37322e;
  --af-muted: #928b83;
  --af-border: #e0d8cf;
  --af-shadow: 0 2px 7px rgb(74 54 36 / 14%);

  display: flex;
  flex-direction: column;
  min-width: 320px;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  font-size: var(--af-font-body);
  color: var(--af-heading);
  background:
    radial-gradient(circle at 50% 15%, rgb(255 255 255 / 78%), transparent 38%),
    #f8f1ea;
}

.app-header {
  display: grid;
  flex: 0 0 47px;
  place-items: center;
  height: 47px;
  background: rgb(255 255 255 / 85%);
  border-bottom: 1px solid #eee8e2;
}

.app-header h1 {
  font-size: var(--af-font-page-title);
  font-weight: 600;
  letter-spacing: 0.01em;
}

.top-nav {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  justify-content: space-between;
  min-height: 55px;
  padding: 10px 19px;
}

.top-nav__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 27px;
  align-items: center;
}

.top-nav__actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  min-width: max-content;
  min-height: 32px;
}

.top-nav__tabs button {
  min-width: 108px;
  height: 31px;
  padding: 0 14px;
  font-size: var(--af-font-body);
  color: #4f4a45;
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
}

.top-nav__tabs button:hover,
.top-nav__tab--active {
  background: rgb(255 255 255 / 72%) !important;
  border-color: #e5ded6 !important;
  box-shadow: 0 1px 4px rgb(70 51 36 / 8%);
}

@media (max-width: 980px) {
  .top-nav__tabs {
    width: 100%;
    gap: 5px;
  }

  .top-nav__tabs button {
    min-width: auto;
    min-height: 34px;
    height: auto;
    padding: 6px 9px;
    white-space: nowrap;
  }
}

@media (max-width: 700px) {
  .top-nav {
    align-items: flex-start;
    height: auto;
    padding-block: 10px;
  }

  .top-nav__actions {
    flex: 0 0 auto;
    flex-wrap: wrap;
    min-width: 0;
    max-width: 100%;
  }
}
</style>
