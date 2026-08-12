import { describe, expect, it } from 'vitest'

import {
  catalogCategoryText,
  catalogMorphologyText,
  catalogTextureText,
  groupCatalogRecordsByEntity,
} from '@/domain/catalog-records'
import type { ExtractionRecord } from '@/types/extraction'

function record(
  id: string,
  page: number,
  entityId: string | null,
  fields: ExtractionRecord['fields'],
) {
  return {
    id,
    entity_id: entityId,
    source_pages: [page],
    fields,
  } as ExtractionRecord
}

function field(value: unknown, page: number) {
  return {
    raw_value: value,
    value,
    status: 'valid' as const,
    warnings: [],
    evidence: [
      {
        page,
        quote: String(value),
        bbox: [0.1, 0.1, 0.8, 0.2] as [number, number, number, number],
        kind: 'text' as const,
      },
    ],
  }
}

describe('groupCatalogRecordsByEntity', () => {
  it('shows one representative body-text record for a cross-page artifact entity', () => {
    const records = [
      record('color-record', 26, 'entity-m1-19', {
        artifact_id: field('M1:19', 26),
        figure_caption: field('彩版九', 26),
      }),
      record('line-record', 126, 'entity-m1-19', {
        artifact_id: field('M1:19', 126),
        figure_caption: field('图3-2C', 126),
      }),
      record('text-record', 132, 'entity-m1-19', {
        artifact_id: field('M1:19', 132),
        measurements: field({ height_cm: 22 }, 132),
        morphological_description: field('高领，折沿，折肩斜弧腹。', 132),
      }),
      record('unlinked-record', 140, null, {
        artifact_id: field('M2:1', 140),
      }),
    ]

    expect(groupCatalogRecordsByEntity(records).map((item) => item.id)).toEqual([
      'text-record',
      'unlinked-record',
    ])
  })

  it('hides empty color-plate caption cards such as 仲M4:3', () => {
    const records = [
      record('zhong-color', 104, 'entity-zhong', {
        artifact_id: field('仲M4:3', 104),
        category: field('玉锥形饰', 104),
        figure_caption: field('4.玉锥形饰（仲M4：3）', 104),
      }),
      record('body-m4-3', 146, 'entity-m4-3', {
        artifact_id: field('M4:3', 146),
        measurements: field({ length_cm: 8.1 }, 146),
        morphological_description: field('锥形，断面近圆形。', 146),
      }),
    ]

    expect(groupCatalogRecordsByEntity(records).map((item) => item.id)).toEqual([
      'body-m4-3',
    ])
  })

  it('does not merge unlinked records merely because their field values match', () => {
    const records = [
      record('first', 10, null, { artifact_id: field('M1:1', 10) }),
      record('second', 20, null, { artifact_id: field('M1:1', 20) }),
    ]

    expect(groupCatalogRecordsByEntity(records)).toHaveLength(2)
  })
})

describe('catalog descriptive fallbacks from text evidence', () => {
  it('surfaces category, texture, and morphology when only the artifact ID was extracted', () => {
    const sparse = {
      ...record('sparse', 88, null, {
        artifact_id: field('M1:37', 88),
      }),
      text_evidence: [
        {
          page: 88,
          quote: 'M1：37，陶尊。泥质黑皮陶。高领，折沿，圜底，喇叭形圈足较高。',
          bbox: [0.1, 0.2, 0.9, 0.22] as [number, number, number, number],
          kind: 'text' as const,
        },
        {
          page: 88,
          quote: '口径13.4、高22厘米。（图3-2C）',
          bbox: [0.08, 0.23, 0.6, 0.25] as [number, number, number, number],
          kind: 'text' as const,
        },
      ],
    } as ExtractionRecord

    expect(catalogCategoryText(sparse)).toBe('陶尊')
    expect(catalogTextureText(sparse)).toBe('泥质黑皮陶')
    expect(catalogMorphologyText(sparse)).toContain('高领')
    expect(catalogMorphologyText(sparse)).toContain('喇叭形圈足较高')
    expect(catalogMorphologyText(sparse)).not.toContain('M1:37')
  })

  it('upgrades a truncated stored morphology when text evidence is richer', () => {
    const truncated = {
      ...record('truncated', 137, null, {
        artifact_id: field('M1:37', 137),
        category: field('玉梳背', 137),
        texture: field('南瓜黄闪玉', 137),
        morphological_description: field('片状', 137),
      }),
      text_evidence: [
        {
          page: 137,
          quote: 'M1：37、玉梳背。南瓜黄闪玉，强风化，大部分受沁变白，局部尚存黄褐色原质。片状，',
          bbox: [0.1, 0.1, 0.9, 0.12] as [number, number, number, number],
          kind: 'text' as const,
        },
        {
          page: 137,
          quote: '倒梯形、一面有线切割凹痕。顶端中央凹缺，缺口内作“弓”字形凸起。',
          bbox: [0.1, 0.13, 0.9, 0.15] as [number, number, number, number],
          kind: 'text' as const,
        },
      ],
    } as ExtractionRecord

    const morphology = catalogMorphologyText(truncated)
    expect(morphology).not.toBe('片状')
    expect(morphology).toContain('片状')
    expect(morphology).toContain('倒梯形')
    expect(morphology).toContain('弓')
  })
})
