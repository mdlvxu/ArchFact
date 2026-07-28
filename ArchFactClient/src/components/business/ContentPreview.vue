<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from '@/i18n'
import type {
  PreviewAnnotation,
  PreviewAnnotationKind,
  RegionRelation,
  SourceRegion,
} from '@/types/extraction'

interface Props {
  page: number
  previewUrl: string
  fileName: string
  loading: boolean
  interactive?: boolean
  annotations?: PreviewAnnotation[]
  relatedAnnotations?: PreviewAnnotation[]
  regions?: SourceRegion[]
  relations?: RegionRelation[]
  pagePreviewUrls?: Record<number, string>
  activeAnnotationId?: string
  relationSaving?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  interactive: false,
  annotations: () => [],
  relatedAnnotations: () => [],
  regions: () => [],
  relations: () => [],
  pagePreviewUrls: () => ({}),
  activeAnnotationId: '',
  relationSaving: false,
})
const { t } = useI18n()

const emit = defineEmits<{
  selectAnnotation: [annotationId: string]
  reviewRelation: [relationId: string, status: 'accepted' | 'rejected']
  rebindRelation: [payload: {
    relationId: string
    sourceRegionId: string
    targetRegionId: string
    relationType: string
  }]
}>()

const zoom = ref(1)
const linksVisible = ref(true)
const rebindMode = ref(false)
const failedTargetImages = ref<Partial<Record<TargetCardKind, boolean>>>({})
const previewStageRef = ref<HTMLElement>()
const zoomContentRef = ref<HTMLElement>()
const previewAspectRatio = ref(0.735)
const previewStageSize = ref({ width: 1000, height: 800 })
const canvasAspectRatio = 1.25
let zoomAnimation: Animation | undefined
let previewResizeObserver: ResizeObserver | undefined
const zoomText = computed(() => `${Math.round(zoom.value * 100)}%`)
function isSequenceAnnotation(annotation: PreviewAnnotation) {
  return annotation.regionKind === 'number'
}
const visiblePageAnnotations = computed(() =>
  props.annotations.filter((annotation) => !isSequenceAnnotation(annotation)),
)
const activeAnnotation = computed(
  () =>
    visiblePageAnnotations.value.find(
      (annotation) => annotation.id === props.activeAnnotationId,
    ) ?? visiblePageAnnotations.value[0],
)
const allAnnotations = computed(() => {
  const byId = new Map<string, PreviewAnnotation>()
  for (const annotation of [...props.annotations, ...props.relatedAnnotations]) {
    if (isSequenceAnnotation(annotation)) continue
    byId.set(annotation.id, annotation)
  }
  return [...byId.values()]
})
const regionById = computed(() => new Map(props.regions.map((region) => [region.id, region])))
const structuralRelationTypes = new Set(['contains', 'caption_of_group'])

function relationPriority(relation: RegionRelation) {
  const typePriority: Record<string, number> = {
    caption_of: 60,
    evidence_for: 55,
    number_of: 50,
    drawing_of: 45,
    color_plate_of: 40,
    image_of: 40,
  }
  const reviewPriority =
    relation.review_status === 'accepted' ? 2 : relation.review_status === 'unreviewed' ? 1 : 0
  return [reviewPriority, typePriority[relation.relation_type] ?? 30, relation.score ?? -1]
}

function compareRelations(left: RegionRelation, right: RegionRelation) {
  const leftPriority = relationPriority(left)
  const rightPriority = relationPriority(right)
  for (let index = 0; index < leftPriority.length; index += 1) {
    const difference = rightPriority[index]! - leftPriority[index]!
    if (difference) return difference
  }
  return left.id.localeCompare(right.id)
}

const semanticRelations = computed(() =>
  props.relations.filter(
    (relation) => {
      if (structuralRelationTypes.has(relation.relation_type)) return false
      if (relation.relation_type.toLowerCase().includes('number')) return false
      return (
        regionById.value.get(relation.source_region_id)?.kind !== 'number' &&
        regionById.value.get(relation.target_region_id)?.kind !== 'number'
      )
    },
  ),
)
const activeRelations = computed(() => {
  const regionId = activeAnnotation.value?.regionId
  if (!regionId) return []
  const relationIds = new Set(activeAnnotation.value?.relationIds ?? [])
  return semanticRelations.value
    .filter(
      (relation) =>
        relationIds.has(relation.id) ||
        relation.source_region_id === regionId ||
        relation.target_region_id === regionId,
    )
    .sort(compareRelations)
})
const activeRelation = computed(() => activeRelations.value[0])

type TargetCardKind = 'line_drawing' | 'artifact_crop' | 'color_plate'

interface PreviewTargetCard {
  kind: TargetCardKind
  title: string
  description: string
  annotation: PreviewAnnotation
}

function annotationForKind(kind: PreviewAnnotationKind) {
  const candidates = allAnnotations.value.filter((annotation) => annotation.kind === kind)
  return (kind === 'line_drawing'
    ? candidates.find((annotation) => annotation.primaryArtifact)
    : undefined) ??
    candidates.find((annotation) => annotation.id === props.activeAnnotationId) ??
    candidates.find((annotation) => annotation.page !== props.page) ??
    candidates[0]
}

const artifactCropAnnotation = computed(() => {
  const candidates = allAnnotations.value.filter(
    (annotation) =>
      Boolean(annotation.cropUrl) &&
      (annotation.kind === 'line_drawing' ||
        annotation.regionKind === 'artifact' ||
        annotation.regionKind === 'grave_drawing'),
  )
  if (!candidates.length) return undefined
  const primaryCandidates = candidates.filter((annotation) => annotation.primaryArtifact)
  if (primaryCandidates.length) {
    return primaryCandidates.find((annotation) => annotation.cropUrl) ?? primaryCandidates[0]
  }
  const lineDrawing = annotationForKind('line_drawing')
  const selectedRecordId = activeAnnotation.value?.recordId
  const relationTargetRegionId = activeRelation.value?.target_region_id
  return [...candidates].sort((left, right) => {
    const score = (annotation: PreviewAnnotation) =>
      (annotation.primaryArtifact ? 240 : 0) +
      (annotation.id === lineDrawing?.id ? 100 : 0) +
      (annotation.regionId === relationTargetRegionId ? 80 : 0) +
      (selectedRecordId && annotation.recordId === selectedRecordId ? 40 : 0) +
      (annotation.regionKind === 'artifact' ? 20 : 0)
    return score(right) - score(left)
  })[0]
})

const colorPlateAnnotation = computed(() => {
  const selectedRecordId = activeAnnotation.value?.recordId
  const artifactRegionId = artifactCropAnnotation.value?.regionId
  const candidates = allAnnotations.value.filter(
    (annotation) =>
      annotation.kind === 'color_plate' &&
      (!selectedRecordId || annotation.recordId === selectedRecordId),
  )
  return [...candidates].sort((left, right) => {
    const score = (annotation: PreviewAnnotation) => {
      const related = props.relations.some(
        (relation) =>
          ['color_plate_of', 'image_of'].includes(relation.relation_type) &&
          (relation.source_region_id === annotation.regionId ||
            relation.target_region_id === annotation.regionId) &&
          (!artifactRegionId ||
            relation.source_region_id === artifactRegionId ||
            relation.target_region_id === artifactRegionId),
      )
      return (related ? 100 : 0) +
        (selectedRecordId && annotation.recordId === selectedRecordId ? 40 : 0) +
        (annotation.cropUrl ? 10 : 0)
    }
    return score(right) - score(left)
  })[0]
})

const canvasVirtualWidth = computed(() => colorPlateAnnotation.value ? 1400 : 1000)

const visibleTargetCards = computed<PreviewTargetCard[]>(() => {
  const cards: PreviewTargetCard[] = []
  const lineDrawing = annotationForKind('line_drawing')
  if (lineDrawing && lineDrawing.page !== props.page) {
    cards.push({
      kind: 'line_drawing',
      title: t('preview.lineDrawing'),
      description: t('preview.lineDescription'),
      annotation: lineDrawing,
    })
  }
  if (artifactCropAnnotation.value?.cropUrl) {
    cards.push({
      kind: 'artifact_crop',
      title: t('preview.artifactCrop'),
      description: t('preview.artifactCropDescription'),
      annotation: artifactCropAnnotation.value,
    })
  }
  if (colorPlateAnnotation.value) {
    cards.push({
      kind: 'color_plate',
      title: t('preview.colorPlate'),
      description: t('preview.colorDescription'),
      annotation: colorPlateAnnotation.value,
    })
  }
  return cards
})

const hasStaggeredVisualTargets = computed(() => {
  const kinds = new Set(visibleTargetCards.value.map((card) => card.kind))
  return kinds.has('line_drawing') && kinds.has('artifact_crop')
})

function targetAnnotation(kind: TargetCardKind) {
  if (kind === 'artifact_crop') return artifactCropAnnotation.value
  if (kind === 'color_plate') return colorPlateAnnotation.value
  return annotationForKind('line_drawing')
}

function targetPreviewUrl(kind: TargetCardKind) {
  const annotation = targetAnnotation(kind)
  if (kind === 'artifact_crop') return annotation?.cropUrl ?? ''
  return (annotation && props.pagePreviewUrls[annotation.page]) || props.previewUrl
}

interface TargetFocusViewport {
  left: number
  top: number
  width: number
  height: number
}

function clampedViewportStart(center: number, size: number) {
  return Math.min(Math.max(0, center - size / 2), Math.max(0, 1 - size))
}

/** Build a context-preserving viewport centered on a small artifact box. */
function targetFocusViewport(kind: TargetCardKind): TargetFocusViewport {
  const annotation = targetAnnotation(kind)
  if (!annotation) return { left: 0, top: 0, width: 1, height: 1 }
  const [left, top, right, bottom] = annotation.bbox
  const pageRatio = Math.max(0.1, previewAspectRatio.value)
  const regionPhysicalWidth = Math.max(0.001, (right - left) * pageRatio)
  const regionPhysicalHeight = Math.max(0.001, bottom - top)
  const maximumExtent = Math.min(pageRatio, 1) * 0.92
  const focusExtent = Math.min(
    maximumExtent,
    Math.max(0.09, regionPhysicalWidth * 2.6, regionPhysicalHeight * 2.6),
  )
  const width = Math.min(1, focusExtent / pageRatio)
  const height = Math.min(1, focusExtent)
  const centerX = (left + right) / 2
  const centerY = (top + bottom) / 2
  return {
    left: clampedViewportStart(centerX, width),
    top: clampedViewportStart(centerY, height),
    width,
    height,
  }
}

function targetPageStyle(kind: TargetCardKind) {
  const viewport = targetFocusViewport(kind)
  return {
    left: `${(-viewport.left / viewport.width) * 100}%`,
    top: `${(-viewport.top / viewport.height) * 100}%`,
    width: `${100 / viewport.width}%`,
    height: `${100 / viewport.height}%`,
  }
}

const documentSurface = {
  left: 40,
  top: 32,
  width: 540,
  height: 736,
}
const documentImageFrame = computed(() => {
  const surfaceRatio = documentSurface.width / documentSurface.height
  const pageRatio = Math.max(0.1, previewAspectRatio.value)
  if (pageRatio >= surfaceRatio) {
    const height = documentSurface.width / pageRatio
    return {
      left: documentSurface.left,
      top: documentSurface.top + (documentSurface.height - height) / 2,
      width: documentSurface.width,
      height,
    }
  }
  const width = documentSurface.height * pageRatio
  return {
    left: documentSurface.left + (documentSurface.width - width) / 2,
    top: documentSurface.top,
    width,
    height: documentSurface.height,
  }
})

const annotationCanvasStyle = computed(() => {
  const availableWidth = Math.max(1, previewStageSize.value.width)
  const availableHeight = Math.max(1, previewStageSize.value.height)
  const baseWidth = Math.min(availableWidth, availableHeight * canvasAspectRatio)
  const baseHeight = baseWidth / canvasAspectRatio
  return {
    width: `${baseHeight * (canvasVirtualWidth.value / 800) * zoom.value}px`,
    height: `${baseHeight * zoom.value}px`,
  }
})

const documentSurfaceStyle = computed(() => ({
  left: `${(documentSurface.left / canvasVirtualWidth.value) * 100}%`,
  width: `${(documentSurface.width / canvasVirtualWidth.value) * 100}%`,
}))

type AnnotationBox = PreviewAnnotation['bbox']

function unionAnnotationBoxes(boxes: AnnotationBox[]): AnnotationBox {
  return [
    Math.min(...boxes.map((box) => box[0])),
    Math.min(...boxes.map((box) => box[1])),
    Math.max(...boxes.map((box) => box[2])),
    Math.max(...boxes.map((box) => box[3])),
  ]
}

function clusterTextEvidence(annotations: PreviewAnnotation[]) {
  const clusters: Array<{ annotations: PreviewAnnotation[]; bbox: AnnotationBox }> = []
  const sorted = [...annotations].sort(
    (left, right) => left.bbox[1] - right.bbox[1] || left.bbox[0] - right.bbox[0],
  )
  for (const annotation of sorted) {
    const last = clusters.at(-1)
    if (!last) {
      clusters.push({ annotations: [annotation], bbox: annotation.bbox })
      continue
    }
    const [left, top, right, bottom] = annotation.bbox
    const [clusterLeft, clusterTop, clusterRight, clusterBottom] = last.bbox
    const verticalGap = top - clusterBottom
    const horizontalGap = Math.max(0, Math.max(clusterLeft, left) - Math.min(clusterRight, right))
    const lineHeight = Math.max(bottom - top, clusterBottom - clusterTop)
    if (verticalGap <= Math.max(0.014, lineHeight * 1.4) && horizontalGap <= 0.18) {
      last.annotations.push(annotation)
      last.bbox = unionAnnotationBoxes([last.bbox, annotation.bbox])
    } else {
      clusters.push({ annotations: [annotation], bbox: annotation.bbox })
    }
  }
  return clusters
}

/**
 * Consolidate duplicate field evidence on each OCR block while keeping every
 * distinct text block that belongs to the selected artifact. A caption block
 * must never hide the artifact-description line that contains its identifier.
 */
const displayAnnotations = computed<PreviewAnnotation[]>(() => {
  const selectedRecordId =
    activeAnnotation.value?.recordId ?? props.annotations.find((item) => item.recordId)?.recordId
  const relevant = selectedRecordId
    ? visiblePageAnnotations.value.filter((annotation) => annotation.recordId === selectedRecordId)
    : visiblePageAnnotations.value
  const semanticText = relevant.filter(
    (annotation) =>
      annotation.kind === 'text' &&
      (annotation.regionKind === undefined || annotation.regionKind === 'text'),
  )
  if (!semanticText.length) return relevant

  const clusters = clusterTextEvidence(semanticText)
  const activeText = semanticText.find((annotation) => annotation.id === props.activeAnnotationId)
  const groupedText = clusters.map((cluster) => {
    const base =
      cluster.annotations.find((annotation) => annotation === activeText) ??
      cluster.annotations.find((annotation) => annotation.fieldKey === 'artifact_id') ??
      cluster.annotations.find(
        (annotation) => annotation.regionId === activeRelation.value?.source_region_id,
      ) ??
      cluster.annotations[0]!
    if (cluster.annotations.length === 1) return base

    const uniqueQuotes = [...new Set(
      cluster.annotations.map((annotation) => annotation.quote.trim()).filter(Boolean),
    )]
    const [left, top, right, bottom] = cluster.bbox
    return {
      ...base,
      fieldKey: 'text_evidence',
      label: t('preview.textEvidence'),
      quote: uniqueQuotes.join('；'),
      bbox: [
        Math.max(0, left - 0.004),
        Math.max(0, top - 0.003),
        Math.min(1, right + 0.004),
        Math.min(1, bottom + 0.003),
      ] as AnnotationBox,
      relationIds: [...new Set(
        cluster.annotations.flatMap((annotation) => annotation.relationIds ?? []),
      )],
      grouped: true,
      groupedRegionIds: [...new Set(
        cluster.annotations.flatMap(
          (annotation) => annotation.regionId ? [annotation.regionId] : [],
        ),
      )],
    }
  })

  return [
    ...relevant.filter((annotation) => !semanticText.includes(annotation)),
    ...groupedText,
  ]
})

function compactText(value: string) {
  return Array.from(value).filter((character) => !/\s/u.test(character)).join('')
}

function glyphUnits(value: string) {
  return Array.from(value).reduce((total, character) => {
    if (/\p{Script=Han}/u.test(character)) return total + 1
    if (/[A-Z0-9]/u.test(character)) return total + 0.66
    if (/[a-z]/u.test(character)) return total + 0.54
    return total + 0.42
  }, 0)
}

/**
 * OCR regions often describe a complete text line while field evidence quotes only
 * occupy part of that line. Keep the accurate vertical bounds, then locate the quote
 * inside the OCR text to remove only the excessive horizontal whitespace.
 */
function visualAnnotationBbox(annotation: PreviewAnnotation) {
  const original = annotation.bbox
  if (
    annotation.kind !== 'text' ||
    (annotation.regionKind !== undefined && annotation.regionKind !== 'text') ||
    !annotation.regionId
  ) return original

  if (annotation.grouped) return original

  const region = regionById.value.get(annotation.regionId)
  const sourceText = compactText(region?.text || region?.ocr_raw_text || '')
  const quote = compactText(annotation.quote)
  const [left, top, right, bottom] = original
  const boxHeight = bottom - top
  const regionBox = region?.kind === 'text' ? region.bbox : undefined
  const contextLeft = regionBox?.[0] ?? left
  const contextRight = regionBox?.[2] ?? right
  const contextWidth = contextRight - contextLeft
  const minimumWidth = Math.min(
    contextWidth,
    Math.max(boxHeight * 2.8, quote.length <= 4 ? 0.14 : 0.1),
  )
  const withMinimumWidth = (candidateLeft: number, candidateRight: number) => {
    if (candidateRight - candidateLeft >= minimumWidth) {
      return [candidateLeft, top, candidateRight, bottom] as const
    }
    const center = (candidateLeft + candidateRight) / 2
    const expandedLeft = Math.max(contextLeft, center - minimumWidth / 2)
    const expandedRight = Math.min(contextRight, expandedLeft + minimumWidth)
    return [
      Math.max(contextLeft, expandedRight - minimumWidth),
      top,
      expandedRight,
      bottom,
    ] as const
  }

  if (!sourceText || !quote) return withMinimumWidth(left, right)
  if (sourceText === quote) return withMinimumWidth(contextLeft, contextRight)

  const quoteStart = sourceText.indexOf(quote)
  if (quoteStart < 0) return withMinimumWidth(left, right)

  const totalUnits = glyphUnits(sourceText)
  if (totalUnits <= 0) return withMinimumWidth(left, right)

  const prefixUnits = glyphUnits(sourceText.slice(0, quoteStart))
  const quoteUnits = glyphUnits(quote)
  const horizontalPadding = Math.min(contextWidth * 0.025, boxHeight * 0.3)
  const refinedLeft = Math.max(
    contextLeft,
    contextLeft + contextWidth * (prefixUnits / totalUnits) - horizontalPadding,
  )
  const refinedRight = Math.min(
    contextRight,
    contextLeft + contextWidth * ((prefixUnits + quoteUnits) / totalUnits) + horizontalPadding,
  )
  if (refinedRight - refinedLeft >= contextWidth * 0.96) {
    return withMinimumWidth(contextLeft, contextRight)
  }
  return withMinimumWidth(refinedLeft, refinedRight)
}

function annotationStyle(annotation: PreviewAnnotation) {
  const [left, top, right, bottom] = visualAnnotationBbox(annotation)
  const frame = documentImageFrame.value
  return {
    left: `${((frame.left - documentSurface.left + left * frame.width) / documentSurface.width) * 100}%`,
    top: `${((frame.top - documentSurface.top + top * frame.height) / documentSurface.height) * 100}%`,
    // Keep the rendered box identical to the backend/YOLO geometry. A separate
    // pseudo-element provides the larger click target without distorting the box.
    width: `${((right - left) * frame.width / documentSurface.width) * 100}%`,
    height: `${((bottom - top) * frame.height / documentSurface.height) * 100}%`,
  }
}

function relationRect(region: SourceRegion) {
  const matchingAnnotation =
    displayAnnotations.value.find(
      (annotation) =>
        annotation.id === props.activeAnnotationId &&
        (annotation.regionId === region.id || annotation.groupedRegionIds?.includes(region.id)) &&
        annotation.page === props.page,
    ) ??
    displayAnnotations.value.find(
      (annotation) =>
        (annotation.regionId === region.id || annotation.groupedRegionIds?.includes(region.id)) &&
        annotation.page === props.page,
    ) ??
    allAnnotations.value.find(
      (annotation) => annotation.regionId === region.id && annotation.page === props.page,
    )
  const [left, top, right, bottom] = matchingAnnotation
    ? visualAnnotationBbox(matchingAnnotation)
    : region.bbox
  const frame = documentImageFrame.value
  return {
    left: frame.left + left * frame.width,
    top: frame.top + top * frame.height,
    right: frame.left + right * frame.width,
    bottom: frame.top + bottom * frame.height,
  }
}

type RelationRect = ReturnType<typeof relationRect>

interface MeasuredTargetRegionRect {
  annotationId: string
  rect: RelationRect
}

const targetRegionRects = ref<
  Partial<Record<TargetCardKind, MeasuredTargetRegionRect>>
>({})

function rectCenter(rect: RelationRect) {
  return {
    x: (rect.left + rect.right) / 2,
    y: (rect.top + rect.bottom) / 2,
  }
}

function boundaryPoint(rect: RelationRect, toward: { x: number; y: number }) {
  const center = rectCenter(rect)
  const dx = toward.x - center.x
  const dy = toward.y - center.y
  const halfWidth = Math.max(1, (rect.right - rect.left) / 2)
  const halfHeight = Math.max(1, (rect.bottom - rect.top) / 2)
  const scale = 1 / Math.max(Math.abs(dx) / halfWidth, Math.abs(dy) / halfHeight, 1)
  return {
    x: center.x + dx * scale,
    y: center.y + dy * scale,
  }
}

function relationGeometry(
  key: string,
  sourceRegionId: string,
  targetRegionId: string,
  derived = false,
) {
  const sourceRegion = regionById.value.get(sourceRegionId)
  const targetRegion = regionById.value.get(targetRegionId)
  if (!sourceRegion || !targetRegion) return undefined
  return geometryBetweenRects(
    key,
    relationRect(sourceRegion),
    relationRect(targetRegion),
    derived,
  )
}

function geometryBetweenRects(
  key: string,
  sourceRect: RelationRect,
  targetRect: RelationRect,
  derived = false,
) {
  const sourceCenter = rectCenter(sourceRect)
  const targetCenter = rectCenter(targetRect)
  const source = boundaryPoint(sourceRect, targetCenter)
  const target = boundaryPoint(targetRect, sourceCenter)
  const direction = target.x >= source.x ? 1 : -1
  const controlOffset = Math.max(55, Math.abs(target.x - source.x) * 0.45)
  return {
    key,
    derived,
    source,
    target,
    path: `M ${source.x} ${source.y} C ${source.x + controlOffset * direction} ${source.y}, ${target.x - controlOffset * direction} ${target.y}, ${target.x} ${target.y}`,
  }
}

const activeRelationGeometries = computed(() => {
  const relation = activeRelation.value
  if (!relation || relation.review_status === 'rejected') return []
  const sourceRegion = regionById.value.get(relation.source_region_id)
  const targetRegion = regionById.value.get(relation.target_region_id)
  if (sourceRegion?.page !== props.page || targetRegion?.page !== props.page) return []
  const sourceRect = relationRect(sourceRegion)
  const targetRect = relationRect(targetRegion)
  const overlapWidth = Math.min(sourceRect.right, targetRect.right) - Math.max(sourceRect.left, targetRect.left)
  const overlapHeight = Math.min(sourceRect.bottom, targetRect.bottom) - Math.max(sourceRect.top, targetRect.top)
  // Overlapping backend regions produce a short blue dash inside one visible
  // evidence box. The relation remains reviewable, but drawing it adds no
  // spatial information and makes the text harder to read.
  if (overlapWidth > 0 && overlapHeight > 0) return []
  const geometry = relationGeometry(
    relation.id,
    relation.source_region_id,
    relation.target_region_id,
  )
  return geometry ? [geometry] : []
})

function targetCardRect(kind: TargetCardKind): RelationRect {
  if (kind === 'artifact_crop') {
    return hasStaggeredVisualTargets.value
      ? { left: 612, top: 515, right: 998, bottom: 796 }
      : { left: 600, top: 511, right: 990, bottom: 772 }
  }
  if (kind === 'color_plate') return { left: 1010, top: 120, right: 1390, bottom: 680 }
  return hasStaggeredVisualTargets.value
    ? { left: 600, top: 4, right: 960, bottom: 480 }
    : { left: 600, top: 4, right: 990, bottom: 480 }
}

/**
 * Estimate the highlighted region before the browser has measured it. The
 * measured DOM rectangle replaces this fallback immediately after rendering.
 */
function fallbackTargetRegionRect(kind: TargetCardKind): RelationRect {
  const annotation = targetAnnotation(kind)
  if (!annotation) return targetCardRect(kind)
  const card = targetCardRect(kind)
  const frame = {
    left: card.left + 8,
    top: card.top + 25,
    right: card.right - 8,
    bottom: card.bottom - 27,
  }
  if (kind === 'artifact_crop') return frame
  const viewport = targetFocusViewport(kind)
  const [left, top, right, bottom] = annotation.bbox
  const frameWidth = frame.right - frame.left
  const frameHeight = frame.bottom - frame.top
  const boxLeft = frame.left + ((left - viewport.left) / viewport.width) * frameWidth
  const boxTop = frame.top + ((top - viewport.top) / viewport.height) * frameHeight
  const boxRight = frame.left + ((right - viewport.left) / viewport.width) * frameWidth
  const boxBottom = frame.top + ((bottom - viewport.top) / viewport.height) * frameHeight
  return {
    left: boxLeft,
    top: boxTop,
    right: Math.max(boxLeft + 4, boxRight),
    bottom: Math.max(boxTop + 4, boxBottom),
  }
}

function targetRegionRect(kind: TargetCardKind): RelationRect {
  const annotation = targetAnnotation(kind)
  const measured = targetRegionRects.value[kind]
  return annotation && measured?.annotationId === annotation.id
    ? measured.rect
    : fallbackTargetRegionRect(kind)
}

const targetCardGeometries = computed(() => {
  const geometries: Array<ReturnType<typeof geometryBetweenRects>> = []
  const selectedRecordId = activeAnnotation.value?.recordId
  const sourceAnnotation =
    (activeAnnotation.value?.page === props.page ? activeAnnotation.value : undefined) ??
    visiblePageAnnotations.value.find(
      (annotation) =>
        annotation.kind === 'text' &&
        (!selectedRecordId || annotation.recordId === selectedRecordId),
    ) ??
    visiblePageAnnotations.value[0]
  const sourceRegion = sourceAnnotation?.regionId
    ? regionById.value.get(sourceAnnotation.regionId)
    : undefined
  const lineCard = visibleTargetCards.value.find((card) => card.kind === 'line_drawing')
  const cropCard = visibleTargetCards.value.find((card) => card.kind === 'artifact_crop')
  const colorCard = visibleTargetCards.value.find((card) => card.kind === 'color_plate')
  const lineAnnotation = annotationForKind('line_drawing')
  const lineRegion = lineAnnotation?.regionId
    ? regionById.value.get(lineAnnotation.regionId)
    : undefined

  if (lineCard?.annotation.regionId && sourceRegion) {
    geometries.push(
      geometryBetweenRects(
        `card:line_drawing:${lineCard.annotation.regionId}`,
        relationRect(sourceRegion),
        targetRegionRect('line_drawing'),
        true,
      ),
    )
  }

  if (cropCard?.annotation.regionId) {
    const cropSourceRect = lineCard
      ? targetRegionRect('line_drawing')
      : lineRegion?.page === props.page
        ? relationRect(lineRegion)
        : sourceRegion
          ? relationRect(sourceRegion)
          : undefined
    if (cropSourceRect) {
      geometries.push(
        geometryBetweenRects(
          `card:artifact_crop:${cropCard.annotation.regionId}`,
          cropSourceRect,
          targetRegionRect('artifact_crop'),
          true,
        ),
      )
    }
    if (sourceRegion) {
      geometries.push(
        geometryBetweenRects(
          `card:text_to_artifact_crop:${cropCard.annotation.regionId}`,
          relationRect(sourceRegion),
          targetRegionRect('artifact_crop'),
          true,
        ),
      )
    }
  }

  if (colorCard?.annotation.regionId) {
    const colorTargetRect = targetRegionRect('color_plate')
    const lineSourceRect = lineCard
      ? targetRegionRect('line_drawing')
      : lineRegion?.page === props.page
        ? relationRect(lineRegion)
        : undefined
    if (lineSourceRect) {
      geometries.push(
        geometryBetweenRects(
          `card:line_to_color_plate:${colorCard.annotation.regionId}`,
          lineSourceRect,
          colorTargetRect,
          true,
        ),
      )
    }
    if (cropCard) {
      geometries.push(
        geometryBetweenRects(
          `card:crop_to_color_plate:${colorCard.annotation.regionId}`,
          targetRegionRect('artifact_crop'),
          colorTargetRect,
          true,
        ),
      )
    }
  }

  return geometries
})
const fallbackEvidenceGeometries = computed(() => {
  // Backend relations are authoritative. The fallback is only for legacy or
  // manually assembled evidence that has no stored relation yet.
  if (activeRelation.value) return []
  const line = annotationForKind('line_drawing')
  const texts = visiblePageAnnotations.value.filter((annotation) => annotation.kind === 'text')
  const text =
    texts.find((annotation) => line?.recordId && annotation.recordId === line.recordId) ?? texts[0]
  const color = annotationForKind('color_plate')
  const nodes = [line, text, color].filter(
    (annotation): annotation is PreviewAnnotation => Boolean(annotation?.regionId),
  )
  const pairs: Array<[PreviewAnnotation, PreviewAnnotation]> = []
  if (line && text) pairs.push([line, text])
  if (color && line) pairs.push([line, color])
  if (color && text) pairs.push([text, color])
  if (!pairs.length && nodes.length === 2) pairs.push([nodes[0]!, nodes[1]!])

  return pairs.flatMap(([sourceAnnotation, targetAnnotation]) => {
    if (
      sourceAnnotation.page !== props.page ||
      targetAnnotation.page !== props.page
    ) return []
    const sourceRegion = regionById.value.get(sourceAnnotation.regionId!)
    const targetRegion = regionById.value.get(targetAnnotation.regionId!)
    if (!sourceRegion || !targetRegion) return []
    return [
      geometryBetweenRects(
        `fallback:${sourceRegion.id}:${targetRegion.id}`,
        relationRect(sourceRegion),
        relationRect(targetRegion),
        true,
      ),
    ]
  })
})
const displayedRelationGeometries = computed(() => {
  const geometries = [
    ...activeRelationGeometries.value,
    ...fallbackEvidenceGeometries.value,
    ...targetCardGeometries.value,
  ]
  const seen = new Set<string>()
  return geometries.filter((geometry) => {
    const endpoints = [geometry.source, geometry.target]
      .map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`)
      .sort()
      .join(':')
    if (seen.has(endpoints)) return false
    seen.add(endpoints)
    return true
  })
})
const activeRelationScore = computed(() =>
  activeRelation.value?.score === null || activeRelation.value?.score === undefined
    ? t('preview.manualRelation')
    : `${Math.round(activeRelation.value.score * 100)}%`,
)

async function centerPreview(behavior: ScrollBehavior = 'smooth') {
  await nextTick()
  const stage = previewStageRef.value
  if (!stage) return

  const left = Math.max(0, (stage.scrollWidth - stage.clientWidth) / 2)
  const top = Math.max(0, (stage.scrollHeight - stage.clientHeight) / 2)
  if (typeof stage.scrollTo === 'function') {
    stage.scrollTo({ left, top, behavior })
  } else {
    stage.scrollLeft = left
    stage.scrollTop = top
  }
}

function finishZoomAnimation() {
  const animation = zoomAnimation
  if (!animation) return
  animation.onfinish = null
  animation.finish()
  animation.cancel()
  zoomAnimation = undefined
}

function prefersReducedMotion() {
  return globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

async function setZoom(nextValue: number) {
  const previousZoom = zoom.value
  const nextZoom = Math.round(Math.min(2.5, Math.max(0.5, nextValue)) * 100) / 100
  if (nextZoom === previousZoom) {
    await centerPreview()
    return
  }

  finishZoomAnimation()
  zoom.value = nextZoom
  await nextTick()

  const content = zoomContentRef.value
  if (content && typeof content.animate === 'function' && !prefersReducedMotion()) {
    const inverseScale = previousZoom / nextZoom
    const animation = content.animate(
      [
        { transform: `scale3d(${inverseScale}, ${inverseScale}, 1)` },
        { transform: 'scale3d(1, 1, 1)' },
      ],
      {
        duration: 260,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        fill: 'both',
      },
    )
    zoomAnimation = animation
    animation.onfinish = () => {
      if (zoomAnimation !== animation) return
      animation.cancel()
      zoomAnimation = undefined
    }
  }

  await centerPreview()
}

function changeZoom(step: number) {
  return setZoom(zoom.value + step)
}

function resetZoom() {
  return setZoom(1)
}

function handlePreviewImageLoad(event: Event) {
  const image = event.currentTarget as HTMLImageElement
  if (image.naturalWidth > 0 && image.naturalHeight > 0) {
    previewAspectRatio.value = image.naturalWidth / image.naturalHeight
  }
  void centerPreview('auto')
}

function syncPreviewStageSize(element = previewStageRef.value) {
  if (!element) return
  const width = element.clientWidth
  const height = element.clientHeight
  if (width > 0 && height > 0) previewStageSize.value = { width, height }
}

function selectAnnotation(annotationId: string) {
  linksVisible.value = true
  if (rebindMode.value && activeRelation.value) {
    const annotation = props.annotations.find((item) => item.id === annotationId)
    if (!annotation?.regionId || annotation.regionId === activeRelation.value.source_region_id) return
    emit('rebindRelation', {
      relationId: activeRelation.value.id,
      sourceRegionId: activeRelation.value.source_region_id,
      targetRegionId: annotation.regionId,
      relationType: activeRelation.value.relation_type,
    })
    rebindMode.value = false
    return
  }
  emit('selectAnnotation', annotationId)
}

function reviewActiveRelation(status: 'accepted' | 'rejected') {
  if (activeRelation.value) emit('reviewRelation', activeRelation.value.id, status)
}

function toggleRebindMode() {
  if (!activeRelation.value) return
  linksVisible.value = true
  rebindMode.value = !rebindMode.value
}

function selectTarget(kind: TargetCardKind) {
  if (kind === 'artifact_crop') {
    const annotation = artifactCropAnnotation.value
    if (annotation) selectAnnotation(annotation.id)
    return
  }
  const relationTargetId = activeRelation.value?.target_region_id
  const relationTarget = allAnnotations.value.find(
    (annotation) => annotation.regionId === relationTargetId && annotation.kind === kind,
  )
  if (relationTarget) {
    selectAnnotation(relationTarget.id)
    return
  }
  const annotation = annotationForKind(kind)
  if (annotation) selectAnnotation(annotation.id)
}

function targetAnnotationStyle(kind: TargetCardKind) {
  const annotation = targetAnnotation(kind)
  if (!annotation) return {}
  const viewport = targetFocusViewport(kind)
  const [left, top, right, bottom] = annotation.bbox
  return {
    left: `${((left - viewport.left) / viewport.width) * 100}%`,
    top: `${((top - viewport.top) / viewport.height) * 100}%`,
    width: `${((right - left) / viewport.width) * 100}%`,
    height: `${((bottom - top) / viewport.height) * 100}%`,
  }
}

function handleTargetImageLoad(kind: TargetCardKind) {
  failedTargetImages.value = { ...failedTargetImages.value, [kind]: false }
  void syncTargetRegionRects()
}

function handleTargetImageError(kind: TargetCardKind) {
  failedTargetImages.value = { ...failedTargetImages.value, [kind]: true }
}

/**
 * Convert each rendered target annotation box into the SVG's dynamic virtual
 * coordinate system. This makes relationships terminate on the red/blue box
 * itself instead of the surrounding page card.
 */
async function syncTargetRegionRects() {
  await nextTick()
  const canvas = zoomContentRef.value
  if (!canvas || !visibleTargetCards.value.length) {
    targetRegionRects.value = {}
    return
  }

  const canvasRect = canvas.getBoundingClientRect()
  if (canvasRect.width <= 0 || canvasRect.height <= 0) return
    const scaleX = canvasVirtualWidth.value / canvasRect.width
  const scaleY = 800 / canvasRect.height
  const nextRects: Partial<Record<TargetCardKind, MeasuredTargetRegionRect>> = {}

  for (const card of visibleTargetCards.value) {
    const annotation = card.annotation
    const box = canvas.querySelector<HTMLElement>(
      card.kind === 'artifact_crop'
        ? '.evidence-target--artifact_crop .evidence-target__image-frame'
        : `.evidence-target--${card.kind} .evidence-target__region-box`,
    )
    if (!annotation || !box) continue
    const boxRect = box.getBoundingClientRect()
    if (boxRect.width <= 0 || boxRect.height <= 0) continue
    nextRects[card.kind] = {
      annotationId: annotation.id,
      rect: {
        left: (boxRect.left - canvasRect.left) * scaleX,
        top: (boxRect.top - canvasRect.top) * scaleY,
        right: (boxRect.right - canvasRect.left) * scaleX,
        bottom: (boxRect.bottom - canvasRect.top) * scaleY,
      },
    }
  }

  targetRegionRects.value = nextRects
}

watch(
  () => props.fileName,
  () => {
    finishZoomAnimation()
    zoom.value = 1
    rebindMode.value = false
    void centerPreview('auto')
  },
)

watch(
  () => props.previewUrl,
  () => {
    previewAspectRatio.value = 0.735
  },
)

watch(
  () => visibleTargetCards.value
    .map((card) => `${card.kind}:${targetPreviewUrl(card.kind)}`)
    .join('|'),
  () => {
    failedTargetImages.value = {}
  },
)

watch(
  () => [
    zoom.value,
    previewAspectRatio.value,
    previewStageSize.value.width,
    previewStageSize.value.height,
    props.activeAnnotationId,
    ...visibleTargetCards.value.map((card) => {
      const annotation = card.annotation
      return `${card.kind}:${annotation?.id ?? ''}:${annotation?.bbox.join(',') ?? ''}`
    }),
  ],
  () => void syncTargetRegionRects(),
  { immediate: true, flush: 'post' },
)

watch(previewStageRef, (element) => {
  previewResizeObserver?.disconnect()
  previewResizeObserver = undefined
  if (!element) return
  syncPreviewStageSize(element)
  if (typeof ResizeObserver === 'undefined') return
  previewResizeObserver = new ResizeObserver(() => {
    syncPreviewStageSize(element)
    void centerPreview('auto')
    void syncTargetRegionRects()
  })
  previewResizeObserver.observe(element)
})

onBeforeUnmount(() => {
  previewResizeObserver?.disconnect()
  finishZoomAnimation()
})
</script>

<template>
  <section
    class="content-preview panel"
    :class="{ 'content-preview--interactive': interactive }"
  >
    <div class="preview-header">
      <h2 class="panel-title">
        {{ t('preview.title') }}
      </h2>

      <div
        v-if="interactive"
        class="preview-tools"
        :aria-label="t('preview.tools')"
      >
        <button
          type="button"
          :disabled="zoom >= 2.5"
          :aria-label="t('preview.zoomIn')"
          @click="changeZoom(0.25)"
        >
          ⊕ <span>{{ t('preview.zoomIn') }}</span>
        </button>
        <button
          type="button"
          :disabled="zoom <= 0.5"
          :aria-label="t('preview.zoomOut')"
          @click="changeZoom(-0.25)"
        >
          ⊖ <span>{{ t('preview.zoomOut') }}</span>
        </button>
        <button
          type="button"
          disabled
          :title="t('preview.drawLater')"
        >
          ✎ <span>{{ t('preview.draw') }}</span>
        </button>
        <button
          type="button"
          disabled
          :title="t('preview.textLater')"
        >
          ✐ <span>{{ t('preview.textTool') }}</span>
        </button>
        <button
          type="button"
          :class="{ 'preview-tools__active': linksVisible }"
          :disabled="!annotations.length"
          @click="linksVisible = !linksVisible"
        >
          ↗ <span>{{ t('preview.link') }}</span>
        </button>
      </div>

      <div
        v-else-if="previewUrl"
        class="zoom-controls"
        :aria-label="t('preview.zoomControls')"
      >
        <button
          type="button"
          :disabled="zoom <= 0.5"
          :aria-label="t('preview.zoomOut')"
          @click="changeZoom(-0.25)"
        >
          −
        </button>
        <button
          class="zoom-controls__value"
          type="button"
          :aria-label="t('preview.resetZoom')"
          @click="resetZoom"
        >
          {{ zoomText }}
        </button>
        <button
          type="button"
          :disabled="zoom >= 2.5"
          :aria-label="t('preview.zoomIn')"
          @click="changeZoom(0.25)"
        >
          +
        </button>
      </div>

      <div
        v-if="fileName && previewUrl"
        class="preview-file"
        :title="fileName"
      >
        <span class="preview-file__name">{{ fileName }}</span>
        <span class="preview-file__meta">
          {{ t('common.page', { page }) }} · {{ zoomText }}
        </span>
      </div>
    </div>

    <div
      v-if="interactive && activeRelation"
      class="relation-review"
      :class="{ 'relation-review--rebind': rebindMode }"
    >
      <span class="relation-review__type">
        {{ activeRelation.relation_type.replaceAll('_', ' ') }}
      </span>
      <span>{{ t('preview.relationScore') }} {{ activeRelationScore }}</span>
      <span class="relation-review__status">
        {{ t(`preview.relationStatus.${activeRelation.review_status}`) }}
      </span>
      <span
        v-if="rebindMode"
        class="relation-review__hint"
      >
        {{ t('preview.rebindHint') }}
      </span>
      <div class="relation-review__actions">
        <button
          type="button"
          :class="{ 'relation-review__accepted': activeRelation.review_status === 'accepted' }"
          :disabled="relationSaving"
          @click="reviewActiveRelation('accepted')"
        >
          {{ t('preview.acceptRelation') }}
        </button>
        <button
          type="button"
          :class="{ 'relation-review__rejected': activeRelation.review_status === 'rejected' }"
          :disabled="relationSaving"
          @click="reviewActiveRelation('rejected')"
        >
          {{ t('preview.rejectRelation') }}
        </button>
        <button
          type="button"
          :class="{ 'relation-review__rebinding': rebindMode }"
          :disabled="relationSaving"
          @click="toggleRebindMode"
        >
          {{ rebindMode ? t('common.cancel') : t('preview.rebindRelation') }}
        </button>
      </div>
    </div>

    <div class="preview-stage">
      <div
        v-if="loading"
        class="preview-state"
      >
        <span
          class="preview-spinner"
          aria-hidden="true"
        />
        <p>{{ t('preview.rendering', { page }) }}</p>
      </div>

      <div
        v-else-if="previewUrl"
        ref="previewStageRef"
        class="pdf-page-stage"
      >
        <div
          v-if="interactive"
          ref="zoomContentRef"
          class="annotation-canvas"
          :style="annotationCanvasStyle"
        >
          <div
            class="document-surface"
            :style="documentSurfaceStyle"
          >
            <img
              class="pdf-page"
              :src="previewUrl"
              :alt="t('preview.pageAlt', { name: fileName, page })"
              @load="handlePreviewImageLoad"
            >
            <button
              v-for="annotation in displayAnnotations"
              :key="annotation.id"
              type="button"
              class="evidence-box"
              :class="[
                `evidence-box--${annotation.kind}`,
                `evidence-box--region-${annotation.regionKind ?? annotation.kind}`,
                { 'evidence-box--active': annotation.id === activeAnnotation?.id },
              ]"
              :style="annotationStyle(annotation)"
              :title="`${annotation.label}: ${annotation.quote}${annotation.approximate ? ` (${t('preview.approximate')})` : ''}`"
              @click="selectAnnotation(annotation.id)"
            >
              <span>{{ annotation.label }}</span>
            </button>
          </div>

          <svg
            v-if="linksVisible && displayedRelationGeometries.length"
            class="relation-lines"
            :viewBox="`0 0 ${canvasVirtualWidth} 800`"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <g
              v-for="geometry in displayedRelationGeometries"
              :key="geometry.key"
              :data-derived="geometry.derived ? 'true' : 'false'"
              :data-relation-key="geometry.key"
            >
              <path :d="geometry.path" />
              <circle
                :cx="geometry.source.x"
                :cy="geometry.source.y"
                r="4"
              />
              <circle
                :cx="geometry.target.x"
                :cy="geometry.target.y"
                r="4"
              />
            </g>
          </svg>

          <div
            v-if="visibleTargetCards.length"
            class="evidence-targets"
            :class="{
              'evidence-targets--single': visibleTargetCards.length === 1,
              'evidence-targets--single-line':
                visibleTargetCards.length === 1 && visibleTargetCards[0]?.kind === 'line_drawing',
              'evidence-targets--single-crop':
                visibleTargetCards.length === 1 && visibleTargetCards[0]?.kind === 'artifact_crop',
              'evidence-targets--staggered': hasStaggeredVisualTargets,
              'evidence-targets--has-color':
                visibleTargetCards.some((card) => card.kind === 'color_plate'),
            }"
          >
            <button
              v-for="card in visibleTargetCards"
              :key="card.kind"
              type="button"
              class="evidence-target"
              :class="[
                `evidence-target--${card.kind}`,
                { 'evidence-target--active': activeAnnotation?.id === card.annotation.id },
              ]"
              @click="selectTarget(card.kind)"
            >
              <span class="evidence-target__tag">{{ card.title }}</span>
              <span class="evidence-target__image-frame">
                <img
                  v-if="!failedTargetImages[card.kind]"
                  :src="targetPreviewUrl(card.kind)"
                  :alt="`${card.title}, ${t('common.page', { page: card.annotation.page })}`"
                  class="evidence-target__page-image"
                  :class="{
                    'evidence-target__crop-image': card.kind === 'artifact_crop',
                    'evidence-target__color-image': card.kind === 'color_plate',
                  }"
                  :style="card.kind === 'line_drawing' ? targetPageStyle(card.kind) : undefined"
                  @load="handleTargetImageLoad(card.kind)"
                  @error="handleTargetImageError(card.kind)"
                >
                <span
                  v-else
                  class="evidence-target__image-error"
                >{{ t('preview.imageUnavailable') }}</span>
                <i
                  v-if="card.kind !== 'artifact_crop'"
                  class="evidence-target__region-box"
                  :class="`evidence-target__region-box--${card.kind}`"
                  :style="targetAnnotationStyle(card.kind)"
                />
              </span>
              <small>
                {{ card.description }} · {{ t('common.page', { page: card.annotation.page }) }}
              </small>
            </button>
          </div>
        </div>

        <div
          v-else
          ref="zoomContentRef"
          class="pdf-page-frame"
          :style="{
            width: `${zoom * 100}%`,
            height: `${zoom * 100}%`,
          }"
        >
          <img
            class="pdf-page"
            :src="previewUrl"
            :alt="t('preview.pageAlt', { name: fileName, page })"
            @load="handlePreviewImageLoad"
          >
        </div>
      </div>

      <div
        v-else
        class="preview-state preview-state--empty"
      >
        <span
          class="preview-state__pointer"
          aria-hidden="true"
        >↖</span>
        <h3>{{ t('preview.selectTitle') }}</h3>
        <p>{{ t('preview.selectHint') }}</p>
      </div>
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

.content-preview {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  padding: 12px;
  overflow: hidden;
}

.preview-header {
  display: flex;
  gap: 10px;
  align-items: center;
  min-height: 34px;
  margin-bottom: 8px;
}

.preview-header .panel-title {
  flex: 0 0 auto;
  white-space: nowrap;
}

.relation-review {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 32px;
  margin: -2px 0 8px;
  padding: 5px 7px;
  font-size: 11px;
  color: #655b52;
  background: #f8f5f1;
  border: 1px solid #e5ddd5;
  border-radius: 6px;
}

.relation-review--rebind {
  background: #eef5ff;
  border-color: #9bbfff;
}

.relation-review__type {
  padding: 2px 7px;
  font-weight: 600;
  color: #356eb5;
  text-transform: capitalize;
  background: #e7f0ff;
  border-radius: 9px;
}

.relation-review__status {
  color: #8a7d71;
}

.relation-review__hint {
  color: #356eb5;
}

.relation-review__actions {
  display: flex;
  gap: 5px;
  margin-left: auto;
}

.relation-review__actions button {
  height: 24px;
  padding: 0 8px;
  font: inherit;
  color: #5d554e;
  cursor: pointer;
  background: #fff;
  border: 1px solid #d8d0c8;
  border-radius: 4px;
}

.relation-review__actions button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.relation-review__actions .relation-review__accepted {
  color: #2f8a43;
  border-color: #70bb80;
}

.relation-review__actions .relation-review__rejected {
  color: #c7473e;
  border-color: #e18b84;
}

.relation-review__actions .relation-review__rebinding {
  color: #2868ba;
  border-color: #79a8e5;
  background: #edf5ff;
}

.panel-title {
  flex: 0 0 auto;
  font-size: var(--af-font-panel-title);
  font-weight: 500;
  color: var(--af-heading);
}

.preview-file {
  display: grid;
  flex: 1 1 280px;
  gap: 1px;
  min-width: 0;
  max-width: min(42%, 520px);
  margin-left: auto;
  font-size: var(--af-font-caption);
  color: var(--af-muted);
  line-height: 1.25;
  text-align: left;
}

.preview-file__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-file__meta {
  color: #9a8170;
  white-space: nowrap;
}

.zoom-controls {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  overflow: hidden;
  background: #fff;
  border: 1px solid #ddd4ca;
  border-radius: 6px;
}

.zoom-controls button {
  min-width: 34px;
  height: 30px;
  color: #5e554d;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.zoom-controls button:hover:not(:disabled) {
  color: #a85d1e;
  background: #f8eee5;
}

.zoom-controls button:disabled,
.preview-tools button:disabled {
  color: #c7c2bc;
  cursor: not-allowed;
}

.zoom-controls .zoom-controls__value {
  min-width: 62px;
  font-size: var(--af-font-body);
  border-right: 1px solid #eee7df;
  border-left: 1px solid #eee7df;
}

.preview-tools {
  display: flex;
  flex: 0 0 auto;
  gap: 6px;
  align-items: center;
}

.preview-tools button {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  height: 30px;
  padding: 0 9px;
  font: inherit;
  font-size: 11px;
  color: #45413d;
  cursor: pointer;
  background: #fff;
  border: 1px solid #d9dfe6;
  border-radius: 5px;
  flex: 0 0 auto;
  white-space: nowrap;
}

.preview-tools button:hover:not(:disabled),
.preview-tools .preview-tools__active {
  color: #8d5c34;
  border-color: #d5b08c;
  box-shadow: 0 1px 3px rgb(83 59 39 / 9%);
}

.preview-stage {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: #f0f0f0;
  border-radius: 4px;
}

.pdf-page-stage {
  width: 100%;
  height: 100%;
  overflow: auto;
  scrollbar-color: #cfc5bb transparent;
  scrollbar-width: thin;
}

.pdf-page-frame {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 1px;
  min-height: 1px;
  margin: auto;
  padding: 12px;
  transform-origin: center center;
  backface-visibility: hidden;
  will-change: transform;
}

.pdf-page {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.pdf-page-frame .pdf-page {
  filter: drop-shadow(0 2px 5px rgb(67 51 37 / 14%));
}

.annotation-canvas {
  position: relative;
  margin: auto;
  aspect-ratio: 1.25;
  transform-origin: center;
  backface-visibility: hidden;
  will-change: transform;
}

.document-surface {
  position: absolute;
  top: 4%;
  left: 4%;
  width: 54%;
  height: 92%;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 2px 8px rgb(64 50 39 / 10%);
}

.document-surface .pdf-page {
  position: absolute;
  inset: 0;
}

.evidence-box {
  position: absolute;
  z-index: 3;
  box-sizing: border-box;
  padding: 0;
  cursor: pointer;
  background: rgb(255 255 255 / 10%);
  border: 2px solid;
  border-radius: 2px;
  transition: box-shadow 0.18s, opacity 0.18s;
}

.evidence-box::after {
  position: absolute;
  inset: -6px;
  content: '';
}

.evidence-box span {
  position: absolute;
  bottom: calc(100% + 3px);
  left: -2px;
  display: none;
  padding: 2px 5px;
  font-size: 9px;
  color: #fff;
  white-space: nowrap;
  border-radius: 4px;
}

.evidence-box:hover span,
.evidence-box--active span {
  display: block;
}

.evidence-box--line_drawing {
  border-color: #ef999f;
  background: rgb(239 153 159 / 20%);
}

.evidence-box--line_drawing span,
.evidence-target--line_drawing .evidence-target__tag {
  background: #e98e94;
}

.evidence-box--text {
  border-color: #a3d37c;
  background: rgb(163 211 124 / 21%);
}

.evidence-box--text span,
.evidence-target--text .evidence-target__tag {
  background: #91c96a;
}

.evidence-box--color_plate {
  border-color: #6bc7dc;
  background: rgb(107 199 220 / 21%);
}

.evidence-box--color_plate span {
  background: #59bdd4;
}

.evidence-target--artifact_crop .evidence-target__tag {
  background: #59bdd4;
}

.evidence-target--color_plate .evidence-target__tag {
  background: #59bdd4;
}

.evidence-box--region-caption {
  border-color: #6bc7dc;
  background: rgb(107 199 220 / 21%);
}

.evidence-box--region-caption span {
  background: #59bdd4;
}

.evidence-box--active {
  z-index: 4;
  box-shadow: 0 0 0 3px rgb(91 159 255 / 25%);
}

.relation-lines {
  position: absolute;
  inset: 0;
  z-index: 2;
  width: 100%;
  height: 100%;
  overflow: visible;
  pointer-events: none;
}

.relation-lines path {
  fill: none;
  stroke: rgb(126 174 236 / 72%);
  stroke-width: 2.2;
  vector-effect: non-scaling-stroke;
}

.relation-lines circle {
  fill: rgb(112 164 232 / 82%);
}

.evidence-targets {
  position: absolute;
  top: 0.5%;
  right: 1%;
  display: flex;
  flex-direction: column;
  gap: 4%;
  width: 39%;
  height: 96%;
}

.evidence-targets--single {
  top: 14%;
  height: 72%;
}

.evidence-targets--single-line {
  top: 0.5%;
  height: 96%;
}

.evidence-targets--single-crop {
  top: 18%;
  height: 64%;
}

.evidence-targets--has-color {
  right: auto;
  left: 42.857%;
  display: grid;
  grid-template-rows: minmax(0, 1.55fr) minmax(0, 0.85fr);
  grid-template-columns: 49.37% 48.1%;
  gap: 4% 2.53%;
  width: 56.429%;
}

.evidence-targets--has-color .evidence-target--line_drawing {
  grid-row: 1;
  grid-column: 1;
}

.evidence-targets--has-color .evidence-target--artifact_crop {
  grid-row: 2;
  grid-column: 1;
}

.evidence-targets--has-color .evidence-target--color_plate {
  grid-row: 1 / 3;
  grid-column: 2;
  align-self: center;
  height: 72%;
}

/*
 * The two artifact views intentionally use different edges. This keeps the
 * evidence workspace visually organic while the measured DOM anchors ensure
 * every relation line still terminates on the real image or annotation box.
 */
.evidence-targets--staggered {
  height: 99%;
}

.evidence-targets--staggered:not(.evidence-targets--has-color) .evidence-target--line_drawing {
  align-self: flex-start;
  width: 92%;
}

.evidence-targets--staggered:not(.evidence-targets--has-color) .evidence-target--artifact_crop {
  align-self: flex-end;
  width: 98%;
  transform: translateX(1.8%);
}

.evidence-targets--staggered.evidence-targets--has-color .evidence-target--line_drawing {
  justify-self: start;
  width: 92%;
}

.evidence-targets--staggered.evidence-targets--has-color .evidence-target--artifact_crop {
  justify-self: end;
  width: 98%;
  transform: translateX(1.8%);
}

.evidence-target {
  position: relative;
  display: grid;
  flex: 1 1 0;
  grid-template-rows: minmax(0, 1fr) auto;
  place-items: center;
  min-height: 0;
  padding: 25px 10px 8px;
  overflow: hidden;
  text-align: left;
  cursor: pointer;
  background: rgb(255 255 255 / 94%);
  border: 1px solid #e2e2e2;
  border-radius: 7px;
  box-shadow: 0 2px 7px rgb(66 54 45 / 8%);
}

.evidence-target--line_drawing {
  flex-grow: 1.55;
}

.evidence-target--artifact_crop {
  flex-grow: 0.85;
}

.evidence-target:disabled {
  cursor: default;
  opacity: 0.42;
}

.evidence-target--active {
  border-color: #6ea8ff;
  box-shadow: 0 0 0 2px rgb(110 168 255 / 20%);
}

.evidence-target__tag {
  position: absolute;
  top: 6px;
  left: 7px;
  padding: 2px 8px;
  font-size: 9px;
  color: #fff;
  border-radius: 9px;
}

.evidence-target__image-frame {
  position: relative;
  display: block;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #f7f7f7;
  border-radius: 4px;
}

.evidence-target__page-image {
  position: absolute;
  max-width: none;
  filter: grayscale(0.75) contrast(1.1);
}

.evidence-target__region-box {
  position: absolute;
  z-index: 2;
  display: block;
  min-width: 4px;
  min-height: 4px;
  pointer-events: none;
  background: rgb(233 142 148 / 18%);
  border: 2px solid #e98e94;
}

.evidence-target__region-box--color_plate {
  background: rgb(89 189 212 / 18%);
  border-color: #59bdd4;
}

.evidence-target .evidence-target__crop-image {
  position: static;
  width: 100%;
  height: 100%;
  max-width: 100%;
  object-fit: contain;
  filter: none;
  background: #fff;
}

.evidence-target .evidence-target__color-image {
  filter: saturate(1.14) contrast(0.98);
}

.evidence-target__image-error {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  padding: 12px;
  font-size: 10px;
  color: #9b8d82;
  text-align: center;
}

.evidence-target blockquote {
  display: -webkit-box;
  overflow: hidden;
  font-size: 10px;
  line-height: 1.45;
  color: #596170;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 5;
}

.evidence-target small {
  width: 100%;
  margin-top: 5px;
  overflow: hidden;
  font-size: 9px;
  color: #9299a4;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-spinner {
  width: 26px;
  height: 26px;
  border: 3px solid #e4d4c6;
  border-top-color: #ae6426;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.preview-state {
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 100%;
  padding: 24px;
  color: #989087;
  text-align: center;
}

.preview-state h3 {
  margin-top: 10px;
  font-size: 15px;
  font-weight: 500;
  color: #6e6862;
}

.preview-state p {
  max-width: 330px;
  margin-top: 7px;
  font-size: 12px;
  line-height: 1.55;
}

.preview-state__pointer {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  font-size: 24px;
  color: #9c7d61;
  background: #fff;
  border: 1px solid #ddd7d0;
  border-radius: 50%;
}

@media (max-width: 980px) {
  .preview-file { flex-basis: auto; max-width: 120px; }
  .preview-file__name { display: none; }
  .preview-tools button span { display: none; }
  .preview-tools button { width: 32px; justify-content: center; padding: 0; }
}
</style>
