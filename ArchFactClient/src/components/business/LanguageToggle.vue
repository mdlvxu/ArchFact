<script setup lang="ts">
import { useI18n } from '@/i18n'

const { isChinese, t, toggleLocale } = useI18n()
</script>

<template>
  <button
    type="button"
    class="language-toggle"
    :aria-label="t('language.toggle')"
    :title="t('language.current')"
    @click="toggleLocale"
  >
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M3.5 12h17M12 3c2.2 2.5 3.3 5.5 3.3 9S14.2 18.5 12 21M12 3C9.8 5.5 8.7 8.5 8.7 12s1.1 6.5 3.3 9" />
    </svg>
    <span :class="{ 'language-toggle__active': isChinese }">中</span>
    <i aria-hidden="true" />
    <span :class="{ 'language-toggle__active': !isChinese }">EN</span>
  </button>
</template>

<style scoped lang="scss">
.language-toggle {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  justify-content: center;
  height: 32px;
  padding: 0 10px;
  font: inherit;
  font-size: 12px;
  color: #948b83;
  cursor: pointer;
  background: rgb(255 255 255 / 76%);
  border: 1px solid #e5ddd5;
  border-radius: 6px;
  box-shadow: 0 1px 4px rgb(70 51 36 / 6%);
  transition: border-color 0.18s, box-shadow 0.18s, color 0.18s;
}

.language-toggle:hover {
  color: #8e5a2e;
  border-color: #d7b590;
  box-shadow: 0 2px 6px rgb(89 60 35 / 10%);
}

.language-toggle svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentcolor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.45;
}

.language-toggle span {
  line-height: 1;
}

.language-toggle i {
  width: 1px;
  height: 12px;
  background: #ddd4cb;
}

.language-toggle__active {
  font-weight: 700;
  color: #9f571c;
}
</style>
