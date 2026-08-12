<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type {
  PreviewAnnotation,
  PreviewAnnotationKind,
  RegionRelation,
} from '@/types/extraction'
import type { PdfPageItem } from '@/types/pdf'

interface Props {
  pages: PdfPageItem[]
  activePage: number
  total: number
  annotations?: PreviewAnnotation[]
  relations?: RegionRelation[]
  activeAnnotationId?: string
  selectedRecordId?: string
}

const props = withDefaults(defineProps<Props>(), {
  annotations: () => [],
  relations: () => [],
  activeAnnotationId: '',
  selectedRecordId: '',
})
const { t } = useI18n()

const emit = defineEmits<{
  select: [page: number]
  selectAnnotation: [page: number, annotationId: string]
  thumbnailNeeded: [page: number]
}>()

const overviewRef = ref<HTMLElement>()
const overviewHeight = ref(0)
let thumbnailObserver: IntersectionObserver | undefined
let overviewResizeObserver: ResizeObserver | undefined

type OverviewMarkerKind = PreviewAnnotationKind | 'artifact_crop'

interface OverviewMarker {
  kind: OverviewMarkerKind
  annotation: PreviewAnnotation
}

const markerKinds: PreviewAnnotationKind[] = ['line_drawing', 'text', 'color_plate']

function connectsToAnchor(annotation: PreviewAnnotation, anchorRegionId: string) {
  if (!annotation.regionId || !anchorRegionId) return undefined
  return props.relations.find(
    (relation) =>
      relation.review_status !== 'rejected' &&
      ((relation.source_region_id === anchorRegionId &&
        relation.target_region_id === annotation.regionId) ||
        (relation.target_region_id === anchorRegionId &&
          relation.source_region_id === annotation.regionId)),
  )
}

function markerCandidateScore(
  annotation: PreviewAnnotation,
  kind: PreviewAnnotationKind,
  anchorRegionId: string,
) {
  const relation = connectsToAnchor(annotation, anchorRegionId)
  const directRelationScore = relation
    ? 300 +
      Number(relation.review_status === 'accepted') * 50 +
      Number(
        kind === 'text'
          ? ['caption_of', 'evidence_for'].includes(relation.relation_type)
          : kind === 'color_plate'
            ? ['color_plate_of', 'image_of'].includes(relation.relation_type)
            : false,
      ) * 80
    : 0
  const [left, top, right, bottom] = annotation.bbox
  const area = (right - left) * (bottom - top)
  return (
    Number(annotation.primaryArtifact) * 1000 +
    directRelationScore +
    Number(annotation.id === props.activeAnnotationId) * 20 +
    Number(annotation.regionKind === 'artifact') * 15 +
    Math.min(annotation.quote.length, 120) / 20 +
    area
  )
}

/** The minimap is a relationship summary: one location per visual category. */
const selectedMarkers = computed(() => {
  if (!props.selectedRecordId) return []
  const candidates = props.annotations.filter(
    (annotation) => annotation.recordId === props.selectedRecordId,
  )
  const anchor =
    candidates.find((annotation) => annotation.primaryArtifact) ??
    candidates.find(
      (annotation) =>
        annotation.kind === 'line_drawing' && annotation.regionKind === 'artifact',
    ) ??
    candidates.find((annotation) => annotation.kind === 'line_drawing')
  const anchorRegionId = anchor?.regionId ?? ''

  const markers: OverviewMarker[] = markerKinds.flatMap((kind) => {
    const kindCandidates = candidates.filter((annotation) => annotation.kind === kind)
    if (!kindCandidates.length) return []
    if (kind === 'line_drawing' && anchor) {
      return [{ kind, annotation: anchor }]
    }

    const directlyRelated = anchorRegionId
      ? kindCandidates.filter((annotation) => connectsToAnchor(annotation, anchorRegionId))
      : []
    const eligible = directlyRelated.length ? directlyRelated : kindCandidates
    return [
      {
        kind,
        annotation: [...eligible].sort(
          (left, right) =>
            markerCandidateScore(right, kind, anchorRegionId) -
            markerCandidateScore(left, kind, anchorRegionId),
        )[0]!,
      },
    ]
  })

  const cropCandidates = candidates.filter(
    (annotation) =>
      Boolean(annotation.cropUrl) &&
      (annotation.regionKind === 'artifact' ||
        annotation.regionKind === 'grave_drawing' ||
        annotation.kind === 'line_drawing'),
  )
  if (cropCandidates.length) {
    const crop =
      cropCandidates.find((annotation) => annotation.primaryArtifact) ??
      cropCandidates.find((annotation) => annotation.id === anchor?.id) ??
      cropCandidates[0]!
    markers.push({ kind: 'artifact_crop', annotation: crop })
  }

  return markers
})

const overviewTrackPadding = 2
const overviewPageHeight = computed(() =>
  props.pages.length
    ? Math.max(0, (overviewHeight.value - overviewTrackPadding * 2) / props.pages.length)
    : 0,
)

const overviewTrackStyle = computed(() => ({
  gridTemplateRows: `repeat(${Math.max(1, props.pages.length)}, minmax(0, 1fr))`,
}))

/** Enclose every marker belonging to the selected artifact, even across pages. */
const markerRangeStyle = computed(() => {
  const positions = selectedMarkers.value.flatMap(({ annotation }) => {
    const pageIndex = props.pages.findIndex((page) => page.page === annotation.page)
    if (pageIndex < 0) return []
    const center = (annotation.bbox[1] + annotation.bbox[3]) / 2
    return [
      overviewTrackPadding +
        pageIndex * overviewPageHeight.value +
        center * overviewPageHeight.value,
    ]
  })
  if (!positions.length) return undefined

  const trackTop = overviewTrackPadding
  const trackBottom =
    overviewTrackPadding + props.pages.length * overviewPageHeight.value
  const markerTop = Math.min(...positions)
  const markerBottom = Math.max(...positions)
  const rangePadding = 8
  const minimumHeight = 34
  let top = Math.max(trackTop, markerTop - rangePadding)
  let bottom = Math.min(trackBottom, markerBottom + rangePadding)

  if (bottom - top < minimumHeight) {
    const center = (markerTop + markerBottom) / 2
    top = Math.max(trackTop, center - minimumHeight / 2)
    bottom = Math.min(trackBottom, top + minimumHeight)
    top = Math.max(trackTop, bottom - minimumHeight)
  }

  return {
    top: `${top}px`,
    height: `${Math.max(1, bottom - top)}px`,
  }
})

const annotationsByPage = computed(() => {
  const result = new Map<number, OverviewMarker[]>()
  for (const marker of selectedMarkers.value) {
    const { annotation } = marker
    const pageAnnotations = result.get(annotation.page) ?? []
    pageAnnotations.push(marker)
    result.set(annotation.page, pageAnnotations)
  }
  return result
})

function markersForPage(page: number) {
  return annotationsByPage.value.get(page) ?? []
}

function markerStyle(marker: OverviewMarker) {
  const { annotation, kind } = marker
  const center = ((annotation.bbox[1] + annotation.bbox[3]) / 2) * 100
  const offset = kind === 'line_drawing'
    ? -4
    : kind === 'artifact_crop'
      ? -1
      : kind === 'text'
        ? 2
        : 5
  return { top: `calc(${Math.min(98, Math.max(2, center))}% + ${offset}px)` }
}

function markerLabel(marker: OverviewMarker) {
  const { annotation, kind } = marker
  const kindLabel =
    kind === 'artifact_crop'
      ? t('preview.artifactCrop')
      : t(`overview.marker.${kind}`)
  const quote = annotation.quote.trim()
  return `${t('common.page', { page: annotation.page })} · ${kindLabel}${quote ? ` · ${quote}` : ''}`
}

async function observeVisiblePages() {
  await nextTick()
  thumbnailObserver?.disconnect()

  thumbnailObserver = new globalThis.IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        const page = Number((entry.target as HTMLElement).dataset.overviewPage)
        const item = props.pages.find((candidate) => candidate.page === page)
        if (item && !item.thumbnailUrl && !item.loading) emit('thumbnailNeeded', page)
      })
    },
    {
      root: overviewRef.value,
      rootMargin: '400px 0px',
    },
  )

  overviewRef.value
    ?.querySelectorAll<HTMLElement>('[data-overview-page]')
    .forEach((element) => thumbnailObserver?.observe(element))
}

async function revealActivePage() {
  await nextTick()
  const item = props.pages.find((candidate) => candidate.page === props.activePage)
  if (item && !item.thumbnailUrl && !item.loading) emit('thumbnailNeeded', props.activePage)
}

watch(() => props.pages.length, observeVisiblePages, { immediate: true })
watch(() => props.activePage, revealActivePage, { immediate: true })
watch(() => props.selectedRecordId, revealActivePage)
watch(
  overviewRef,
  (element) => {
    overviewResizeObserver?.disconnect()
    overviewResizeObserver = undefined
    if (!element) return

    const syncHeight = () => {
      overviewHeight.value = element.clientHeight
    }
    syncHeight()
    overviewResizeObserver = new ResizeObserver(syncHeight)
    overviewResizeObserver.observe(element)
  },
  { flush: 'post' },
)

onBeforeUnmount(() => {
  thumbnailObserver?.disconnect()
  overviewResizeObserver?.disconnect()
})
</script>

<template>
  <aside class="pdf-overview panel">
    <div
      v-if="pages.length"
      ref="overviewRef"
      class="overview-scroll"
      :aria-label="t('overview.title')"
    >
      <div
        class="overview-track"
        :style="overviewTrackStyle"
      >
        <span
          v-if="markerRangeStyle"
          class="marker-range-indicator"
          :style="markerRangeStyle"
          aria-hidden="true"
        />
        <div
          v-for="item in pages"
          :key="item.page"
          class="overview-page"
          :class="{ 'overview-page--active': item.page === activePage }"
          :data-overview-page="item.page"
          :aria-label="t('overview.open', { page: item.page })"
          role="button"
          tabindex="0"
          @click="emit('select', item.page)"
          @keydown.enter="emit('select', item.page)"
          @keydown.space.prevent="emit('select', item.page)"
        >
          <img
            v-if="item.thumbnailUrl"
            :src="item.thumbnailUrl"
            :alt="t('overview.thumbnail', { page: item.page })"
          >
          <span
            v-else
            class="overview-placeholder"
            aria-hidden="true"
          >
            <i
              v-for="line in 5"
              :key="line"
            />
          </span>
          <span
            v-if="item.page === activePage && !markerRangeStyle"
            class="current-page-indicator"
            aria-hidden="true"
          />
          <button
            v-for="marker in markersForPage(item.page)"
            :key="`${marker.kind}:${marker.annotation.id}`"
            type="button"
            class="overview-marker"
            :class="[
              `overview-marker--${marker.kind}`,
              { 'overview-marker--active': marker.annotation.id === activeAnnotationId },
            ]"
            :style="markerStyle(marker)"
            :title="markerLabel(marker)"
            :aria-label="markerLabel(marker)"
            @click.stop="
              emit('selectAnnotation', marker.annotation.page, marker.annotation.id)
            "
          />
        </div>
      </div>
    </div>

    <div
      v-else
      class="overview-empty"
    >
      <span>PDF</span>
      <p>{{ t('overview.empty') }}</p>
    </div>
  </aside>
</template>

<style scoped lang="scss">
.panel {
  background: rgb(255 255 255 / 88%);
  border: 1px solid var(--af-border);
  border-radius: 10px;
  box-shadow: var(--af-shadow);
}

.pdf-overview {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}

.overview-scroll {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: #f3f0ec;
  border: 0;
  border-radius: 9px;
  scrollbar-width: none;
}

.overview-scroll::-webkit-scrollbar {
  display: none;
}

.overview-track {
  position: relative;
  display: grid;
  width: 100%;
  height: 100%;
  gap: 0;
  padding: 2px 5px;
}

.marker-range-indicator {
  position: absolute;
  right: 2px;
  left: 2px;
  z-index: 2;
  pointer-events: none;
  background: rgb(103 190 218 / 10%);
  border: 1px solid #72b7ce;
  border-radius: 2px;
  box-shadow: 0 0 0 1px rgb(255 255 255 / 38%);
  transition:
    top 180ms ease,
    height 180ms ease;
}

.overview-page {
  position: relative;
  width: 100%;
  min-height: 0;
  padding: 0;
  overflow: visible;
  cursor: pointer;
  background: #fafafa;
  border: 0;
  border-radius: 0;
}

.overview-page img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: fill;
  filter: grayscale(0.82) contrast(0.72) brightness(1.12);
  opacity: 0.72;
}

.overview-placeholder {
  display: grid;
  gap: 4px;
  align-content: center;
  width: 100%;
  height: 100%;
  padding: 5px 7px;
  background: #faf9f7;
}

.overview-placeholder i {
  display: block;
  height: 1px;
  background: #d9d4ce;
  border-radius: 2px;
}

.overview-placeholder i:nth-child(2n) {
  width: 72%;
}

.current-page-indicator {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  border: 1px solid rgb(84 116 132 / 72%);
  background: rgb(86 166 185 / 8%);
}

.overview-marker {
  position: absolute;
  left: -1px;
  z-index: 3;
  width: calc(100% + 2px);
  height: 3px;
  min-height: 3px;
  padding: 0;
  cursor: pointer;
  border: 0;
  border-radius: 1px;
  box-shadow: 0 0 0 1px rgb(255 255 255 / 45%);
  transform: translateY(-50%);
  transition: height 120ms ease, filter 120ms ease;
}

.overview-marker:hover,
.overview-marker--active {
  z-index: 4;
  height: 5px;
  filter: saturate(1.2) brightness(0.95);
}

.overview-marker--line_drawing {
  background: #e98e94;
}

.overview-marker--text {
  background: #91c96a;
}

.overview-marker--artifact_crop {
  background: #59bdd4;
}

.overview-marker--color_plate {
  background: #c97916;
}

.overview-empty {
  display: grid;
  flex: 1;
  place-items: center;
  align-content: center;
  color: #9b938a;
  text-align: center;
}

.overview-empty span {
  display: grid;
  place-items: center;
  width: 42px;
  height: 54px;
  margin-bottom: 9px;
  font-size: 12px;
  color: #b26a29;
  background: #fff;
  border: 1px solid #dfd7ce;
}

.overview-empty p {
  font-size: 12px;
  line-height: 1.5;
}
</style>
