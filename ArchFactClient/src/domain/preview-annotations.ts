import type {
  ExtractionRecord,
  PageAnnotations,
  PreviewAnnotation,
  PreviewAnnotationKind,
  SourceRegionKind,
} from '@/types/extraction'

function previewKind(kind: SourceRegionKind | undefined): PreviewAnnotationKind {
  if (kind === 'line_drawing' || kind === 'color_plate') return kind
  if (kind === 'artifact' || kind === 'grave_drawing') return 'line_drawing'
  return 'text'
}

function normalizedEvidenceBox(bbox: number[] | null | undefined) {
  if (
    bbox?.length === 4 &&
    bbox.every((value) => Number.isFinite(value) && value >= 0 && value <= 1) &&
    bbox[0]! < bbox[2]! &&
    bbox[1]! < bbox[3]!
  ) {
    return [bbox[0]!, bbox[1]!, bbox[2]!, bbox[3]!] as [number, number, number, number]
  }
  return null
}

const detectorFallbackLabels: Partial<Record<SourceRegionKind, string>> = {
  artifact: 'qiwu',
  caption: 'tuzhu',
}

/**
 * Sequence-number regions remain available to the backend matching pipeline,
 * but they are implementation evidence rather than a user-facing preview item.
 */
function isSequenceRegion(kind: SourceRegionKind | undefined) {
  return kind === 'number'
}

function normalizeArtifactIdentifier(value: unknown) {
  return String(value ?? '')
    .normalize('NFKC')
    .replaceAll('：', ':')
    .replaceAll('﹕', ':')
    .replace(/\s+/g, '')
    .toUpperCase()
}

/**
 * Resolve the single artifact region selected by the sequence-number chain.
 *
 * The persisted primary field is authoritative. The relation-based fallback keeps
 * older API responses and an already-open preview safe as well: an exact M3:7
 * sequence can only traverse its own `number_of` edge, never a neighbouring M3:5.
 */
function resolvePrimaryArtifactRegionId(
  record: ExtractionRecord,
  annotationData: PageAnnotations | null,
) {
  if (record.primary_artifact_region_id) return record.primary_artifact_region_id
  if (!annotationData) return undefined

  const regionById = new Map(annotationData.regions.map((region) => [region.id, region]))
  const persistedRelation = annotationData.relations.find(
    (relation) => relation.id === record.primary_relation_id,
  )
  if (
    persistedRelation?.relation_type === 'number_of' &&
    regionById.get(persistedRelation.target_region_id)?.kind === 'artifact'
  ) {
    return persistedRelation.target_region_id
  }

  const artifactIdentifier = normalizeArtifactIdentifier(record.fields.artifact_id?.value)
  if (!artifactIdentifier) return undefined
  const exactNumberIds = new Set(
    annotationData.regions
      .filter(
        (region) =>
          region.kind === 'number' &&
          normalizeArtifactIdentifier(region.text || region.ocr_raw_text) === artifactIdentifier,
      )
      .map((region) => region.id),
  )
  if (!exactNumberIds.size) return undefined

  return [...annotationData.relations]
    .filter(
      (relation) =>
        relation.relation_type === 'number_of' &&
        relation.review_status !== 'rejected' &&
        exactNumberIds.has(relation.source_region_id) &&
        regionById.get(relation.target_region_id)?.kind === 'artifact',
    )
    .sort((left, right) => {
      const reviewDifference =
        Number(right.review_status === 'accepted') - Number(left.review_status === 'accepted')
      return reviewDifference || (right.score ?? 0) - (left.score ?? 0)
    })[0]?.target_region_id
}

/** Converts backend-owned evidence geometry into UI annotations without synthetic boxes. */
export function buildPreviewAnnotations(
  records: ExtractionRecord[],
  annotationData: PageAnnotations | null,
  page: number,
  regionCropUrl?: (regionId: string) => string,
): PreviewAnnotation[] {
  const annotations: PreviewAnnotation[] = []
  const regions = new Map(annotationData?.regions.map((region) => [region.id, region]) ?? [])
  const primaryArtifactIds = new Map(
    records.map((record) => [record.id, resolvePrimaryArtifactRegionId(record, annotationData)]),
  )

  records.forEach((record) => {
    const primaryArtifactRegionId = primaryArtifactIds.get(record.id)
    Object.entries(record.fields).forEach(([fieldKey, field]) => {
      if (field.value === null || field.value === undefined || field.value === '') return
      field.evidence
        .filter((evidence) => evidence.page === page)
        .forEach((evidence, evidenceIndex) => {
          const region = evidence.region_id ? regions.get(evidence.region_id) : undefined
          if (isSequenceRegion(evidence.kind ?? region?.kind)) return
          // A resolved OCR region is the authoritative geometry. LLM-provided
          // evidence bboxes can be stale or refer to a nearby line even when
          // region_id and quote are correct (for example M13:20 being drawn over
          // M13:15-17). Only fall back to the evidence bbox when no region exists.
          const bbox = normalizedEvidenceBox(region?.bbox) ?? normalizedEvidenceBox(evidence.bbox)
          if (!bbox) return

          const relationIds = new Set(evidence.relation_ids ?? [])
          annotationData?.relations.forEach((relation) => {
            if (
              region &&
              (relation.source_region_id === region.id || relation.target_region_id === region.id)
            ) {
              relationIds.add(relation.id)
            }
          })
          annotations.push({
            id: `${record.id}:${fieldKey}:${evidenceIndex}:${evidence.region_id ?? 'direct'}`,
            regionId: evidence.region_id ?? undefined,
            recordId: record.id,
            fieldKey,
            page,
            kind: previewKind(evidence.kind ?? region?.kind),
            regionKind: evidence.kind ?? region?.kind,
            label: fieldKey
              .split('_')
              .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
              .join(' '),
            quote: evidence.quote || region?.text || '',
            bbox,
            approximate: false,
            relationIds: [...relationIds],
            source: evidence.source ?? region?.source,
            confidence: evidence.confidence ?? region?.confidence,
            cropUrl:
              region?.crop_object_key && regionCropUrl ? regionCropUrl(region.id) : undefined,
            primaryArtifact: evidence.region_id === primaryArtifactRegionId,
          })
        })
    })
  })

  // Semantic extraction and visual detection are independent. If the LLM fails,
  // keep the artifact and caption detector results visible. YOLO sequence-number
  // regions stay in backend data for matching, but are intentionally hidden here.
  if (!records.length) {
    annotationData?.regions.forEach((region) => {
      const label = detectorFallbackLabels[region.kind]
      if (region.page !== page || !label) return
      const bbox = normalizedEvidenceBox(region.bbox)
      if (!bbox) return
      const relationIds = annotationData.relations
        .filter(
          (relation) =>
            relation.source_region_id === region.id || relation.target_region_id === region.id,
        )
        .map((relation) => relation.id)
      annotations.push({
        id: `detector:${region.id}`,
        regionId: region.id,
        fieldKey: region.kind,
        page: region.page,
        kind: previewKind(region.kind),
        regionKind: region.kind,
        label,
        quote: region.text || region.ocr_raw_text || '',
        bbox,
        approximate: false,
        relationIds,
        source: region.source,
        confidence: region.confidence,
        cropUrl:
          region.crop_object_key && regionCropUrl ? regionCropUrl(region.id) : undefined,
      })
    })
    return annotations
  }

  const annotatedRegionIds = new Set(
    annotations.flatMap((annotation) => (annotation.regionId ? [annotation.regionId] : [])),
  )
  annotationData?.regions.forEach((region) => {
    if (region.page !== page) return
    if (annotatedRegionIds.has(region.id)) return
    // Groups and sequence numbers are matching evidence, not selectable content boxes.
    // An unreferenced OCR text region is not semantic evidence for this record either:
    // entity context may contain several pages, and falling back to an arbitrary text
    // line can draw M2:14 as the evidence for M3:4. Text is rendered only when a field
    // explicitly cites it through evidence.region_id/bbox.
    if (region.kind === 'group' || region.kind === 'text' || isSequenceRegion(region.kind)) return

    const relationIds = annotationData.relations
      .filter(
        (relation) =>
          relation.source_region_id === region.id || relation.target_region_id === region.id,
      )
      .map((relation) => relation.id)
    const record = records.find((item) => item.region_ids?.includes(region.id))
    if (!record) return

    const bbox = normalizedEvidenceBox(region.bbox)
    if (!bbox) return
    annotations.push({
      id: `region:${region.id}`,
      regionId: region.id,
      recordId: record?.id,
      fieldKey: region.kind,
      page: region.page,
      kind: previewKind(region.kind),
      regionKind: region.kind,
      label: region.kind.replaceAll('_', ' '),
      quote: region.text,
      bbox,
      approximate: false,
      relationIds,
      source: region.source,
      confidence: region.confidence,
      cropUrl: region.crop_object_key && regionCropUrl ? regionCropUrl(region.id) : undefined,
      primaryArtifact: region.id === primaryArtifactIds.get(record.id),
    })
  })
  return annotations
}
