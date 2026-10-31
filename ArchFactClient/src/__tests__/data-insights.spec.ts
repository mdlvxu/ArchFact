import { describe, expect, it } from 'vitest'
import { buildDataInsights, insightMaterial } from '@/domain/data-insights'
import type { ExtractionRecord } from '@/types/extraction'

function record(id: string, overrides: Partial<ExtractionRecord> = {}): ExtractionRecord {
  return {
    id, job_id: 'job-1', record_type: 'artifact', source_pages: [12],
    fields: {
      artifact_id: { raw_value: id, value: id, status: 'valid', evidence: [] },
      category: { raw_value: '石钺', value: '石钺', status: 'valid', evidence: [] },
    },
    warnings: [], review_status: 'unreviewed', reviewed_at: null, created_at: '',
    ...overrides,
  }
}

describe('data insights', () => {
  it('counts the supplied artifact cards and preserves empty requested pages', () => {
    const result = buildDataInsights([
      record('M1:1', { source_pages: [12, 13], relation_ids: ['relation-1'] }),
      record('M1:2', { source_pages: [12], fields: {} }),
    ], [11, 12, 13])

    expect(result.categoryGroups).toEqual([
      { label: '石钺', count: 1 },
      { label: '未归类', count: 1 },
    ])
    expect(result.pageGroups).toEqual([
      { page: 11, count: 0 }, { page: 12, count: 2 }, { page: 13, count: 1 },
    ])
    expect(result.crossPage).toBe(1)
    expect(result.fields.find((field) => field.key === 'material')).toEqual({
      key: 'material', count: 0, total: 2,
    })
  })

  it('does not call multi-page presence a linked relation without a saved relation', () => {
    expect(buildDataInsights([record('M1:3', { source_pages: [12, 13] })]).crossPage).toBe(0)
  })

  it('groups known materials without inventing missing artifact types', () => {
    const stone = record('M1:4', {
      fields: {
        material: { raw_value: '青绿色晶屑凝灰岩', value: '青绿色晶屑凝灰岩', status: 'valid', evidence: [] },
      },
    })
    const pottery = record('M1:5', {
      fields: {
        texture: { raw_value: '夹砂灰陶', value: '夹砂灰陶', status: 'valid', evidence: [] },
      },
    })
    expect(insightMaterial(stone)).toBe('石质')
    expect(insightMaterial(pottery)).toBe('陶瓷')
    expect(buildDataInsights([stone, pottery]).categoryGroups).toEqual([{ label: '未归类', count: 2 }])
    expect(buildDataInsights([stone, pottery]).materialGroups).toEqual([
      { label: '石质', count: 1 }, { label: '陶瓷', count: 1 },
    ])
  })
})
