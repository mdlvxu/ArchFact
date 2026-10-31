import { catalogCategoryText } from '@/domain/catalog-records'
import type { ExtractionRecord } from '@/types/extraction'

export interface InsightGroup {
  label: string
  count: number
}

export interface PageInsight {
  page: number
  count: number
}

const fieldKeys = [
  'artifact_id',
  'category',
  'material',
  'measurements',
  'morphological_description',
] as const

export type InsightFieldKey = (typeof fieldKeys)[number]

export interface FieldInsight {
  key: InsightFieldKey
  count: number
  total: number
}

function hasFieldValue(record: ExtractionRecord, key: string) {
  const value = record.fields[key]?.value ?? record.fields[key]?.raw_value
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return String(value).trim().length > 0
}

export function insightCategory(record: ExtractionRecord) {
  const category = catalogCategoryText(record).trim()
  return category && category.length <= 12 ? category : '未归类'
}

/** A coarse material view derived only from the stored material/texture fields. */
export function insightMaterial(record: ExtractionRecord) {
  const material = record.fields.material?.value ?? record.fields.material?.raw_value
  const texture = record.fields.texture?.value ?? record.fields.texture?.raw_value
  const raw = String(material || texture || '').trim()
  if (!raw) return '未记录'
  if (/玉|翡翠|玛瑙/.test(raw)) return '玉石'
  if (/陶|瓷/.test(raw)) return '陶瓷'
  if (/泥质|夹砂|夹炭|细泥|粗泥/.test(raw)) return '泥质 / 夹砂'
  if (/铜|青铜|金|银|铁/.test(raw)) return '金属'
  if (/骨|牙|角|贝/.test(raw)) return '骨牙贝'
  if (/石|岩|矿|燧/.test(raw)) return '石质'
  if (/木|竹|漆/.test(raw)) return '木竹漆'
  return '其他材质'
}

export function buildDataInsights(records: ExtractionRecord[], requestedPages: number[] = []) {
  const categories = new Map<string, number>()
  const materials = new Map<string, number>()
  const pages = new Map<number, number>()
  const fieldCounts = new Map<InsightFieldKey, number>(fieldKeys.map((key) => [key, 0]))
  let withText = 0
  let withDrawing = 0
  let crossPage = 0

  for (const record of records) {
    const category = insightCategory(record)
    categories.set(category, (categories.get(category) ?? 0) + 1)
    const material = insightMaterial(record)
    materials.set(material, (materials.get(material) ?? 0) + 1)

    for (const page of new Set(record.source_pages.filter((value) => Number.isInteger(value) && value > 0))) {
      pages.set(page, (pages.get(page) ?? 0) + 1)
    }

    for (const key of fieldKeys) {
      if (hasFieldValue(record, key) || (key === 'material' && hasFieldValue(record, 'texture'))) {
        fieldCounts.set(key, (fieldCounts.get(key) ?? 0) + 1)
      }
    }

    if (record.text_evidence?.some((item) => item.quote?.trim()) ||
      Object.values(record.fields).some((field) => field.evidence.some((item) => item.quote?.trim()))) {
      withText++
    }
    if (record.primary_artifact_region_id || record.thumbnail_region_id) withDrawing++
    if (
      record.relation_ids?.length &&
      new Set([...(record.source_pages ?? []), ...(record.associated_pages ?? [])]).size > 1
    ) crossPage++
  }

  const categoryGroups: InsightGroup[] = [...categories].map(([label, count]) => ({ label, count }))
    .sort((left, right) => Number(left.label === '未归类') - Number(right.label === '未归类') ||
      right.count - left.count || left.label.localeCompare(right.label, 'zh-CN'))
  const materialGroups: InsightGroup[] = [...materials].map(([label, count]) => ({ label, count }))
    .sort((left, right) => Number(left.label === '未记录') - Number(right.label === '未记录') ||
      right.count - left.count || left.label.localeCompare(right.label, 'zh-CN'))
  const allPages = [...new Set([...requestedPages, ...pages.keys()])]
    .filter((page) => Number.isInteger(page) && page > 0)
    .sort((left, right) => left - right)
  const pageGroups: PageInsight[] = allPages.map((page) => ({ page, count: pages.get(page) ?? 0 }))
  const fields: FieldInsight[] = fieldKeys.map((key) => ({
    key,
    count: fieldCounts.get(key) ?? 0,
    total: records.length,
  }))

  return { categoryGroups, materialGroups, pageGroups, fields, withText, withDrawing, crossPage }
}
