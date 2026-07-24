<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '@/i18n'
import type { VerificationReport } from '@/types/verification'

interface Props {
  report: VerificationReport
  running: boolean
}

const props = defineProps<Props>()
const { localize, t } = useI18n()

const maximumFieldCount = computed(() => Math.max(1, ...props.report.fields.map((field) => field.count)))

function fieldWidth(count: number) {
  return `${Math.max(8, Math.round((count / maximumFieldCount.value) * 100))}%`
}
</script>

<template>
  <section class="verification-summary">
    <div
      v-if="running"
      class="verification-overlay"
    >
      <span />
      <strong>{{ t('summary.running') }}</strong>
      <p>{{ t('summary.applying') }}</p>
    </div>

    <div
      class="summary-scroll"
      :class="{ 'summary-scroll--running': running }"
    >
      <section class="summary-section alignment-section">
        <div class="section-heading">
          <h3>{{ t('summary.sample') }}</h3>
          <span>{{ t('summary.samples', { count: report.sampleCount }) }}</span>
        </div>

        <dl class="alignment-metrics">
          <div>
            <dt>{{ t('summary.errorCoverage') }}</dt>
            <dd>{{ report.errorCoverage }}%</dd>
          </div>
          <div>
            <dt>{{ t('summary.precision') }}</dt>
            <dd>{{ report.precision }}%</dd>
          </div>
          <div>
            <dt>{{ t('summary.alignment') }}</dt>
            <dd>{{ report.alignment }}%</dd>
          </div>
        </dl>
      </section>

      <section class="summary-section full-verification">
        <div class="section-heading">
          <h3>{{ t('summary.full') }}</h3>
          <span>{{ t('summary.artifacts', { count: report.totalArtifacts }) }}</span>
        </div>

        <div class="result-totals">
          <div class="result-total result-total--pass">
            <strong>{{ report.passed }}</strong>
            <span>{{ t('catalog.pass') }}</span>
          </div>
          <div class="result-total result-total--error">
            <strong>{{ report.errors }}</strong>
            <span>{{ t('summary.error') }}</span>
          </div>
        </div>
        <dl class="verification-alerts">
          <div :class="{ 'verification-alert--active': report.relationChanged > 0 }">
            <dt>{{ t('summary.relationChanged') }}</dt>
            <dd>{{ report.relationChanged }}</dd>
          </div>
          <div :class="{ 'verification-alert--danger': report.stale > 0 }">
            <dt>{{ t('summary.stale') }}</dt>
            <dd>{{ report.stale }}</dd>
          </div>
        </dl>
      </section>

      <section class="summary-section distribution-section">
        <h3>{{ t('summary.distribution') }}</h3>
        <div class="distribution-list">
          <div
            v-for="field in report.fields"
            :key="field.label"
            class="distribution-row"
          >
            <span>{{ localize(field.label) }}</span>
            <div>
              <i :style="{ width: fieldWidth(field.count) }" />
            </div>
            <b>{{ field.count }}</b>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped lang="scss">
.verification-summary {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: rgb(255 255 255 / 82%);
  border: 1px solid #ebe4dd;
  border-radius: 10px;
  box-shadow: 0 2px 6px rgb(75 54 37 / 7%);
}

.summary-scroll {
  height: 100%;
  padding: 16px 18px;
  overflow-y: auto;
  scrollbar-width: none;
  transition: filter 0.2s;
}

.summary-scroll::-webkit-scrollbar {
  display: none;
}

.summary-scroll--running {
  filter: blur(1px);
  opacity: 0.5;
}

.summary-section + .summary-section {
  margin-top: 28px;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 15px;
}

.section-heading h3,
.distribution-section h3 {
  font-size: 22px;
  font-weight: 600;
  color: #24211e;
}

.section-heading > span {
  font-size: 20px;
  color: #9b968f;
}

.alignment-metrics {
  display: grid;
  gap: 12px;
}

.alignment-metrics > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.alignment-metrics dt,
.alignment-metrics dd {
  font-size: 20px;
  color: #9b968f;
}

.result-totals {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  padding: 34px 10px 18px;
}

.result-total {
  display: grid;
  place-items: center;
}

.result-total strong {
  font-size: 44px;
  line-height: 1;
}

.result-total span {
  margin-top: 5px;
  font-size: 21px;
  color: #74706c;
}

.result-total--pass strong {
  color: #20c566;
}

.result-total--error strong {
  color: #b42c31;
}

.verification-alerts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.verification-alerts > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  padding: 8px 10px;
  font-size: 12px;
  color: #766d65;
  background: #f7f3ef;
  border-radius: 7px;
}

.verification-alerts dd {
  font-weight: 700;
}

.verification-alerts .verification-alert--active {
  color: #865b1f;
  background: #fff2d8;
}

.verification-alerts .verification-alert--danger {
  color: #a33e39;
  background: #fde8e6;
}

.distribution-section h3 {
  margin-bottom: 22px;
}

.distribution-list {
  display: grid;
  gap: 14px;
}

.distribution-row {
  display: grid;
  grid-template-columns: minmax(140px, 0.85fr) minmax(130px, 1.15fr) 35px;
  gap: 12px;
  align-items: center;
}

.distribution-row > span {
  font-size: 18px;
  line-height: 1.1;
  color: #8e8983;
}

.distribution-row > div {
  height: 15px;
  overflow: hidden;
  background: #f5ece3;
  border-radius: 8px;
}

.distribution-row i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #e64248, #ed5357);
  border-radius: inherit;
  transition: width 0.4s ease;
}

.distribution-row b {
  font-size: 18px;
  font-weight: 400;
  color: #8e8983;
  text-align: right;
}

.verification-overlay {
  position: absolute;
  z-index: 2;
  inset: 0;
  display: grid;
  place-items: center;
  align-content: center;
  color: #6c625a;
  text-align: center;
  background: rgb(255 252 248 / 72%);
  backdrop-filter: blur(2px);
}

.verification-overlay > span {
  width: 38px;
  height: 38px;
  margin-bottom: 12px;
  border: 4px solid #ead1bd;
  border-top-color: #ad5b20;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.verification-overlay strong {
  margin-bottom: 4px;
  font-size: 16px;
}

.verification-overlay p {
  font-size: 12px;
  color: #958a80;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1280px) {
  .section-heading h3,
  .distribution-section h3 {
    font-size: 19px;
  }

  .section-heading > span,
  .alignment-metrics dt,
  .alignment-metrics dd {
    font-size: 16px;
  }

  .distribution-row {
    grid-template-columns: minmax(110px, 0.8fr) minmax(100px, 1.2fr) 30px;
  }

  .distribution-row > span,
  .distribution-row b {
    font-size: 15px;
  }
}
</style>
