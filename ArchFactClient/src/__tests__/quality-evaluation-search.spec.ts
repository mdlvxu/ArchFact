import { describe, expect, it } from 'vitest'
import { matchesQualityEvaluationItem } from '@/domain/quality-evaluation-search'
import type { QualityEvaluationItem } from '@/types/quality-evaluation'

const item: QualityEvaluationItem = {
  id: 'item-1',
  evaluation_id: 'evaluation-1',
  job_id: 'job-1',
  record_id: 'record-1',
  crop_region_id: null,
  color_plate_region_id: null,
  artifact_id: 'M1,19',
  gold_record_id: 'gold-1',
  match_status: 'matched',
  source_pages: [18, 19],
  field_results: [
    {
      key: 'measurements',
      label: '尺寸',
      verdict: 'matched',
      score: 0.9,
      actual: '口径13.3厘米',
      expected: '口径13.3 cm',
    },
    {
      key: 'morphological_description',
      label: '形态描述',
      verdict: 'mismatched',
      score: 0.6,
      actual: '弧腹',
      expected: '斜弧腹',
    },
  ],
}

describe('quality evaluation record search', () => {
  it('searches the selected comparison field on either human or machine value', () => {
    expect(matchesQualityEvaluationItem(item, '口径13.3 cm', 'measurements')).toBe(true)
    expect(matchesQualityEvaluationItem(item, '陶器', 'measurements')).toBe(false)
  })

  it('searches the full artifact ID and source pages in all-fields mode', () => {
    expect(matchesQualityEvaluationItem(item, 'M1:19', 'all')).toBe(true)
    expect(matchesQualityEvaluationItem(item, '斜弧腹', 'all')).toBe(true)
    expect(matchesQualityEvaluationItem(item, '18', 'all')).toBe(true)
  })

  it('supports source-page-only search', () => {
    expect(matchesQualityEvaluationItem(item, '19', 'source_pages')).toBe(true)
    expect(matchesQualityEvaluationItem(item, '20', 'source_pages')).toBe(false)
  })
})
