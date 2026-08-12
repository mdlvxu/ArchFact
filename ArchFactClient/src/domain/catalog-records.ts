import type { ExtractedField, ExtractionRecord } from '@/types/extraction'

const bodyFieldWeights: Record<string, number> = {
  artifact_id: 3,
  category: 4,
  material: 6,
  surface_color: 6,
  texture: 6,
  surface_treatment: 6,
  measurements: 10,
  morphological_description: 12,
  figure_caption: 1,
}

function fieldText(record: ExtractionRecord, keys: string[]) {
  for (const key of keys) {
    const field = record.fields[key]
    const value = field?.value ?? field?.raw_value
    if (value === null || value === undefined || value === '') continue
    if (typeof value === 'string') return value.trim()
    if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  }
  return ''
}

/**
 * When semantic extraction only kept the artifact ID, the fused OCR paragraph
 * still lives on ``text_evidence``. Derive a short card blurb from those quotes.
 */
export function catalogTextEvidenceSummary(record: ExtractionRecord) {
  const quotes = (record.text_evidence ?? [])
    .map((evidence) => evidence.quote?.trim())
    .filter((quote): quote is string => Boolean(quote))
  if (!quotes.length) return ''

  let text = quotes.join('')
  const artifactId = fieldText(record, ['artifact_id', 'context_id'])
  if (artifactId) {
    const escaped = artifactId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/:/g, '[:：]')
    text = text.replace(new RegExp(`^\\s*${escaped}\\s*[，,、:：.。]?\\s*`), '')
  }
  text = text.replace(/[（(][^)）]*(?:图|彩版|图版|fig(?:ure)?|plate)[^)）]*[）)]\s*$/i, '')
  text = text.replace(
    /(?:最大径|直径|残高|通高|全高|口径|底径|腹径|高|长|宽|厚|径)\s*约?\s*\d+(?:[.,]\d+)?\s*(?:厘米|毫米|米|cm|mm|m)?/gi,
    '',
  )
  return text.replace(/[、,，；;\s]{2,}/g, '，').replace(/^[，,、:：;；。．.\s]+|[，,、:：;；。．.\s]+$/g, '')
}

const texturePhrasePattern =
  /((?:泥质|夹砂|夹炭|细泥|粗泥|硬陶)[\u4e00-\u9fff]{0,6}陶|[\u4e00-\u9fff]{0,4}(?:闪玉|玉|石|青铜|铜|铁))/

function morphologyFromTextEvidence(record: ExtractionRecord) {
  let summary = catalogTextEvidenceSummary(record)
  const category = catalogCategoryText(record)
  if (category && summary.startsWith(category)) {
    summary = summary.slice(category.length).replace(/^[。．.；;，,、\s]+/, '')
  }
  const texture = summary.match(texturePhrasePattern)?.[1]?.trim() ?? ''
  if (texture) {
    summary = summary.replace(texture, '')
  }
  return summary.replace(/[、,，；;\s]{2,}/g, '，').replace(/^[，,、:：;；。．.\s]+|[，,、:：;；。．.\s]+$/g, '')
}

export function catalogMorphologyText(record: ExtractionRecord) {
  const explicit = fieldText(record, ['morphological_description'])
  const fromEvidence = morphologyFromTextEvidence(record)
  if (!explicit) return fromEvidence
  // Upgrade truncated stored values such as ``片状`` when OCR evidence is richer.
  if (
    fromEvidence &&
    fromEvidence.length >= Math.max(12, explicit.length + 8) &&
    (fromEvidence.includes(explicit) || explicit.length <= 8)
  ) {
    return fromEvidence
  }
  return explicit
}

export function catalogCategoryText(record: ExtractionRecord) {
  const explicit = fieldText(record, ['category', 'type', 'subtype'])
  if (explicit) return explicit
  const summary = catalogTextEvidenceSummary(record)
  const match = summary.match(/^([\u4e00-\u9fffA-Za-z0-9ⅣⅢⅡⅠ]{1,12})(?=[。．.；;，,、]|$)/)
  const category = match?.[1]?.trim() ?? ''
  if (!category || texturePhrasePattern.test(category)) return ''
  return category
}

export function catalogTextureText(record: ExtractionRecord) {
  const explicit = fieldText(record, ['texture', 'surface_color', 'material', 'page_text'])
  if (explicit) return explicit
  let summary = catalogTextEvidenceSummary(record)
  const category = catalogCategoryText(record)
  if (category && summary.startsWith(category)) {
    summary = summary.slice(category.length).replace(/^[。．.；;，,、\s]+/, '')
  }
  const match = summary.match(texturePhrasePattern)
  return match?.[1]?.trim() ?? ''
}

function hasValue(field: ExtractedField | undefined) {
  if (!field) return false
  const value = field.value ?? field.raw_value
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  return typeof value !== 'object' || Object.keys(value).length > 0
}

const captionOnlyFieldKeys = new Set(['artifact_id', 'figure_caption', 'category'])
const bodyFieldKeys = [
  'morphological_description',
  'measurements',
  'texture',
  'surface_color',
  'completeness',
] as const
const tombUnitPrefixPattern = /^[\u4e00-\u9fff]{1,2}(?=[A-Za-z])/
const plateItemCaptionPattern =
  /^\s*\d+\s*[.．、:：].{0,24}[（(][^）)]*[A-Za-z]{1,6}\s*\d+/i

/**
 * Color-plate OCR often yields empty cards that only carry an ID/caption
 * (e.g. ``4.玉锥形饰（仲M4：3）``). Those are linkage hints, not catalog entries.
 */
export function isCaptionOnlySparseCatalogRecord(record: ExtractionRecord) {
  if (bodyFieldKeys.some((key) => hasValue(record.fields[key]))) return false
  const populated = Object.keys(record.fields).filter((key) => hasValue(record.fields[key]))
  if (!populated.every((key) => captionOnlyFieldKeys.has(key))) return false

  const caption = String(
    record.fields.figure_caption?.value ?? record.fields.figure_caption?.raw_value ?? '',
  ).normalize('NFKC')
  const artifactId = String(
    record.fields.artifact_id?.raw_value ?? record.fields.artifact_id?.value ?? '',
  ).normalize('NFKC')
  return (
    hasValue(record.fields.figure_caption) ||
    tombUnitPrefixPattern.test(artifactId) ||
    plateItemCaptionPattern.test(caption)
  )
}

export function catalogRepresentativeScore(record: ExtractionRecord) {
  return Object.entries(record.fields).reduce((score, [fieldKey, field]) => {
    if (!hasValue(field)) return score
    const weight = bodyFieldWeights[fieldKey] ?? 2
    const hasOwnPageTextEvidence = field.evidence.some(
      (evidence) =>
        record.source_pages.includes(evidence.page) &&
        (evidence.kind === undefined || evidence.kind === 'text'),
    )
    return score + weight + Number(hasOwnPageTextEvidence)
  }, 0)
}

/**
 * The extraction pipeline deliberately keeps page-level records for provenance.
 * The browse catalog is entity-level, so one linked artifact is represented by
 * the record containing the richest body text.
 */
export function groupCatalogRecordsByEntity(records: ExtractionRecord[]) {
  const catalogRecords = records.filter((record) => !isCaptionOnlySparseCatalogRecord(record))
  const groups = new Map<string, { index: number; record: ExtractionRecord }>()
  catalogRecords.forEach((record, index) => {
    const key = record.entity_id ? `entity:${record.entity_id}` : `record:${record.id}`
    const current = groups.get(key)
    if (
      !current ||
      catalogRepresentativeScore(record) > catalogRepresentativeScore(current.record)
    ) {
      groups.set(key, { index: current?.index ?? index, record })
    }
  })
  return [...groups.values()]
    .sort((left, right) => left.index - right.index)
    .map((group) => group.record)
}
