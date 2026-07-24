<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from '@/i18n'
import type {
  PreviewAnnotation,
} from '@/types/extraction'
import type { PdfPageItem } from '@/types/pdf'

interface Props {
  pages: PdfPageItem[]
  activePage: number | null
  annotations?: PreviewAnnotation[]
  activeAnnotationId?: string
}

const props = withDefaults(defineProps<Props>(), {
  annotations: () => [],
  activeAnnotationId: '',
})
const { t } = useI18n()

const emit = defineEmits<{
  select: [page: number]
  thumbnailNeeded: [page: number]
  selectAnnotation: [annotationId: string]
}>()

type RelatedCardKind = 'line_drawing' | 'text' | 'artifact_crop' | 'color_plate'

const relationKinds = computed<Array<{ kind: RelatedCardKind; label: string }>>(() => [
  { kind: 'line_drawing', label: t('preview.lineDrawing') },
  { kind: 'text', label: t('preview.textEvidence') },
  { kind: 'artifact_crop', label: t('preview.artifactCrop') },
  { kind: 'color_plate', label: t('preview.colorPlate') },
])

const artifactCropAnnotation = computed(() => {
  const active = props.annotations.find(
    (annotation) => annotation.id === props.activeAnnotationId,
  )
  const lineDrawing = props.annotations.find(
    (annotation) =>
      annotation.kind === 'line_drawing' &&
      (!active?.recordId || annotation.recordId === active.recordId),
  )
  const candidates = props.annotations.filter(
    (annotation) =>
      Boolean(annotation.cropUrl) &&
      (annotation.kind === 'line_drawing' ||
        annotation.regionKind === 'artifact' ||
        annotation.regionKind === 'grave_drawing'),
  )
  return [...candidates].sort((left, right) => {
    const score = (annotation: PreviewAnnotation) =>
      (annotation.id === lineDrawing?.id ? 100 : 0) +
      (active?.recordId && annotation.recordId === active.recordId ? 40 : 0) +
      (annotation.regionKind === 'artifact' ? 20 : 0)
    return score(right) - score(left)
  })[0]
})

const colorPlateAnnotation = computed(() => {
  const active = props.annotations.find(
    (annotation) => annotation.id === props.activeAnnotationId,
  )
  return props.annotations.find(
    (annotation) =>
      annotation.kind === 'color_plate' &&
      (!active?.recordId || annotation.recordId === active.recordId),
  )
})

const relationCards = computed(() =>
  relationKinds.value.map((item) => {
    const annotation = item.kind === 'artifact_crop'
      ? artifactCropAnnotation.value
      : item.kind === 'color_plate'
        ? colorPlateAnnotation.value
        : props.annotations.find((candidate) => candidate.kind === item.kind)
    const page = props.pages.find((candidate) => candidate.page === annotation?.page)
    return {
      ...item,
      annotation,
      page,
      imageUrl: item.kind === 'artifact_crop'
        ? annotation?.cropUrl
        : item.kind === 'color_plate'
          ? page?.thumbnailUrl || annotation?.cropUrl
        : item.kind === 'line_drawing'
          ? page?.thumbnailUrl
          : undefined,
    }
  }),
)

watch(
  () => props.annotations.map((annotation) => annotation.page).join(','),
  () => {
    const pages = [...new Set(props.annotations.map((annotation) => annotation.page))]
    pages.forEach((pageNumber) => {
      const item = props.pages.find((page) => page.page === pageNumber)
      if (item && !item.thumbnailUrl && !item.loading) emit('thumbnailNeeded', pageNumber)
    })
  },
  { immediate: true },
)
</script>

<template>
  <section class="related-pages panel">
    <div class="related-header">
      <h2>{{ t('related.title') }}</h2>
      <span v-if="activePage !== null">{{ t('common.page', { page: activePage }) }}</span>
    </div>

    <div
      v-if="activePage !== null && annotations.length"
      class="related-list"
    >
      <button
        v-for="card in relationCards"
        :key="card.kind"
        type="button"
        class="related-item"
        :class="{
          'related-item--active': card.annotation?.id === activeAnnotationId,
          'related-item--disabled': !card.annotation,
        }"
        :disabled="!card.annotation"
        @click="card.annotation && emit('selectAnnotation', card.annotation.id)"
      >
        <span
          class="relation-tag"
          :data-kind="card.kind"
        >{{ card.label }}</span>
        <blockquote v-if="card.kind === 'text'">
          {{ card.annotation?.quote || t('preview.noText') }}
        </blockquote>
        <img
          v-else-if="card.imageUrl"
          :src="card.imageUrl"
          :alt="`${card.label}, ${t('common.page', { page: card.annotation?.page })}`"
          :class="{
            'related-image--page': card.kind === 'line_drawing',
            'related-image--crop': card.kind === 'artifact_crop',
            'related-image--color': card.kind === 'color_plate',
          }"
        >
        <span
          v-else
          class="related-placeholder"
        >
          <span v-if="card.kind === 'color_plate' && !card.annotation">
            {{ t('preview.noColorPlate') }}
          </span>
          <template v-else>
            <i
              v-for="line in 7"
              :key="line"
            />
          </template>
        </span>
        <b>
          <template v-if="card.annotation">
            {{ t('common.page', { page: card.annotation.page }) }} ·
          </template>
          {{ relationKinds.findIndex((item) => item.kind === card.kind) + 1 }}/{{ relationKinds.length }}
        </b>
      </button>
    </div>

    <div
      v-else
      class="related-empty"
    >
      {{ t('related.empty') }}
    </div>
  </section>
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.related-pages {
  min-height: 0;
  padding: 11px 15px;
  overflow: hidden;
}

.related-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.related-header h2 {
  font-size: var(--af-font-panel-title);
  font-weight: 500;
  color: var(--af-heading);
}

.related-header span {
  font-size: 11px;
  color: #93653f;
}

.related-list {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  height: calc(100% - 31px);
}

.related-item {
  position: relative;
  min-width: 0;
  min-height: 0;
  padding: 21px 7px 19px;
  overflow: hidden;
  cursor: pointer;
  background: #fff;
  border: 1px solid #eee7df;
  border-radius: 6px;
}

.related-item:hover:not(:disabled),
.related-item--active {
  border-color: #73aefa;
  box-shadow: 0 0 0 2px rgb(110 168 255 / 16%);
}

.related-item--disabled {
  cursor: default;
  opacity: 0.45;
}

.related-item img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #fff;
}

.related-item .related-image--page {
  filter: grayscale(0.75) contrast(1.05);
}

.related-item .related-image--crop {
  padding: 4px;
  filter: none;
}

.related-item .related-image--color {
  filter: saturate(1.14) contrast(0.98);
}

.related-item blockquote {
  display: -webkit-box;
  padding: 8px;
  overflow: hidden;
  font-size: 10px;
  line-height: 1.45;
  color: #586170;
  text-align: left;
  background: #fafcf7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 7;
}

.related-placeholder {
  display: grid;
  gap: 7px;
  align-content: center;
  width: 100%;
  height: 100%;
  padding: 12px;
  background: #fff;
}

.related-placeholder i {
  height: 2px;
  background: #ddd8d2;
  border-radius: 2px;
}

.related-placeholder i:nth-child(2n) { width: 76%; }

.related-placeholder span {
  font-size: 10px;
  color: #9a9188;
  text-align: center;
}

.relation-tag {
  position: absolute;
  top: 4px;
  left: 7px;
  z-index: 1;
  padding: 2px 8px;
  font-size: 9px;
  color: #fff;
  background: #6fbfd6;
  border-radius: 8px;
}

.relation-tag[data-kind='line_drawing'],
.relation-tag[data-relation='Previous'] { background: #e99a9f; }
.relation-tag[data-kind='text'],
.relation-tag[data-relation='Current'] { background: #9acb72; }
.relation-tag[data-kind='artifact_crop'] { background: #62bed4; }
.relation-tag[data-kind='color_plate'] { background: #62bed4; }

.related-item b {
  position: absolute;
  right: 8px;
  bottom: 3px;
  font-size: 11px;
  font-weight: 500;
  color: #4b4540;
}

.related-empty {
  display: grid;
  place-items: center;
  height: calc(100% - 31px);
  min-height: 90px;
  padding: 20px;
  font-size: 12px;
  color: var(--af-muted);
  text-align: center;
  background: #fafafa;
  border-radius: 5px;
}

@media (max-width: 980px) {
  .related-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
