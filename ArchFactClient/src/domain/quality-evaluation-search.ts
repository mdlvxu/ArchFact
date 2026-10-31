import type { QualityEvaluationItem } from '@/types/quality-evaluation'

export type QualityEvaluationSearchField = 'all' | 'source_pages' | string

function searchableText(value: unknown) {
  return value == null ? '' : String(value).trim().toLocaleLowerCase()
}

export function matchesQualityEvaluationItem(
  item: QualityEvaluationItem,
  query: string,
  fieldKey: QualityEvaluationSearchField,
) {
  const normalizedQuery = searchableText(query)
  if (!normalizedQuery) return true

  const includesQuery = (value: unknown) => searchableText(value).includes(normalizedQuery)
  const normalizedId = searchableText(item.artifact_id).replace(/[\s:：,，]/g, '')
  const normalizedIdQuery = normalizedQuery.replace(/[\s:：,，]/g, '')
  const matchesArtifactId = includesQuery(item.artifact_id) || normalizedId.includes(normalizedIdQuery)
  if (fieldKey === 'source_pages') return item.source_pages.some(includesQuery)
  if (fieldKey === 'artifact_id') return matchesArtifactId
  if (fieldKey === 'all') {
    return matchesArtifactId || item.source_pages.some(includesQuery) ||
      item.field_results.some((field) => includesQuery(field.actual) || includesQuery(field.expected))
  }

  return item.field_results.some((field) =>
    field.key === fieldKey && (includesQuery(field.actual) || includesQuery(field.expected)),
  )
}
