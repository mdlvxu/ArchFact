import { describe, expect, it } from 'vitest'
import { buildPreviewAnnotations } from '@/domain/preview-annotations'
import type { ExtractionRecord, PageAnnotations } from '@/types/extraction'

const record: ExtractionRecord = {
  id: 'record-1',
  job_id: 'job-1',
  record_type: 'artifact',
  source_pages: [7],
  fields: {
    figure_caption: {
      raw_value: '图七：1',
      value: '图七：1',
      status: 'valid',
      evidence: [
        {
          page: 7,
          quote: '图七：1',
          bbox: null,
          region_id: 'region-figure',
          kind: 'line_drawing',
          relation_ids: [],
          source: 'yolo',
        },
      ],
    },
    texture: {
      raw_value: '泥质灰陶',
      value: '泥质灰陶',
      status: 'valid',
      evidence: [{ page: 7, quote: '泥质灰陶', bbox: null, kind: 'text' }],
    },
  },
  warnings: [],
  review_status: 'unreviewed',
  reviewed_at: null,
  created_at: '2026-07-16T00:00:00Z',
}

const annotationData: PageAnnotations = {
  page: 7,
  regions: [
    {
      id: 'region-figure',
      job_id: 'job-1',
      document_id: 'document-1',
      page: 7,
      kind: 'line_drawing',
      bbox: [0.2, 0.3, 0.5, 0.6],
      bbox_px: null,
      text: '',
      confidence: 0.94,
      source: 'yolo',
      model_run_id: 'run-yolo',
      image_id: null,
      crop_object_key: null,
    },
  ],
  relations: [
    {
      id: 'relation-1',
      job_id: 'job-1',
      source_region_id: 'region-figure',
      target_region_id: 'region-caption',
      relation_type: 'caption_of',
      score: 0.91,
      method: 'hungarian',
      version: '1',
      model_run_id: null,
      review_status: 'unreviewed',
    },
  ],
  records: [record],
}

describe('buildPreviewAnnotations', () => {
  it('uses backend region geometry, kind and relations', () => {
    const annotations = buildPreviewAnnotations([record], annotationData, 7)
    expect(annotations).toHaveLength(1)
    expect(annotations[0]).toMatchObject({
      regionId: 'region-figure',
      kind: 'line_drawing',
      bbox: [0.2, 0.3, 0.5, 0.6],
      relationIds: ['relation-1'],
      source: 'yolo',
    })
  })

  it('prefers the resolved OCR region geometry over a stale model evidence box', () => {
    const recordWithStaleBox: ExtractionRecord = {
      ...record,
      fields: {
        category: {
          raw_value: '玉锥形饰',
          value: '玉锥形饰',
          status: 'valid',
          evidence: [
            {
              page: 7,
              quote: 'M13:20 玉锥形饰',
              bbox: [0.1, 0.7, 0.8, 0.75],
              region_id: 'region-m13-20',
              kind: 'text',
            },
          ],
        },
      },
    }
    const data: PageAnnotations = {
      page: 7,
      regions: [
        {
          ...annotationData.regions[0]!,
          id: 'region-m13-20',
          kind: 'text',
          bbox: [0.15, 0.86, 0.92, 0.9],
          text: 'M13:20 玉锥形饰',
        },
      ],
      relations: [],
      records: [recordWithStaleBox],
    }

    const annotations = buildPreviewAnnotations([recordWithStaleBox], data, 7)

    expect(annotations).toHaveLength(1)
    expect(annotations[0]?.bbox).toEqual([0.15, 0.86, 0.92, 0.9])
  })

  it('does not invent a fallback box when evidence has no geometry', () => {
    expect(buildPreviewAnnotations([record], null, 7)).toHaveLength(0)
  })

  it('keeps sequence matching in backend data without exposing a sequence annotation', () => {
    const data: PageAnnotations = {
      page: 7,
      regions: [
        {
          ...annotationData.regions[0]!,
          id: 'region-artifact',
          kind: 'artifact',
          crop_object_key: 'documents/document-1/pages/0007/crops/artifact/region-artifact.png',
        },
        {
          ...annotationData.regions[0]!,
          id: 'region-group',
          kind: 'group',
          bbox: [0.1, 0.1, 0.9, 0.9],
        },
        {
          ...annotationData.regions[0]!,
          id: 'region-number',
          kind: 'number',
          bbox: [0.2, 0.62, 0.25, 0.66],
          ocr_raw_text: 'M3:18',
        },
        {
          ...annotationData.regions[0]!,
          id: 'region-caption',
          kind: 'caption',
          bbox: [0.1, 0.72, 0.5, 0.78],
          ocr_raw_text: '图六一',
        },
        {
          ...annotationData.regions[0]!,
          id: 'region-text',
          kind: 'text',
          bbox: [0.1, 0.8, 0.8, 0.9],
        },
      ],
      relations: [
        {
          ...annotationData.relations[0]!,
          id: 'caption-number',
          source_region_id: 'region-caption',
          target_region_id: 'region-number',
          relation_type: 'caption_to_number',
        },
      ],
      records: [],
    }

    const annotations = buildPreviewAnnotations(
      [],
      data,
      7,
      (regionId) => `/api/v1/extraction-jobs/job-1/regions/${regionId}/crop`,
    )

    expect(annotations).toHaveLength(2)
    expect(annotations.map((annotation) => annotation.label)).toEqual([
      'qiwu',
      'tuzhu',
    ])
    expect(annotations.some((annotation) => annotation.regionId === 'region-number')).toBe(false)
  })

  it('does not turn an unrelated entity-context OCR line into record evidence', () => {
    const data: PageAnnotations = {
      page: 7,
      regions: [
        {
          ...annotationData.regions[0]!,
          id: 'region-artifact',
          kind: 'artifact',
          crop_object_key: 'documents/document-1/pages/0007/crops/artifact/region-artifact.png',
        },
        {
          ...annotationData.regions[0]!,
          id: 'unrelated-text',
          kind: 'text',
          bbox: [0.1, 0.7, 0.9, 0.8],
          text: 'M2:14 ... M2:15 ...',
        },
      ],
      relations: [],
      records: [],
    }
    const recordWithEntityRegions: ExtractionRecord = {
      ...record,
      fields: {},
      region_ids: ['region-artifact', 'unrelated-text'],
    }

    const annotations = buildPreviewAnnotations([recordWithEntityRegions], data, 7)

    expect(annotations.map((annotation) => annotation.regionId)).toEqual(['region-artifact'])
    expect(annotations.some((annotation) => annotation.kind === 'text')).toBe(false)
  })

  it('marks only the backend-selected primary artifact annotation', () => {
    const primaryRecord: ExtractionRecord = {
      ...record,
      fields: {},
      region_ids: ['artifact-m3-4', 'artifact-m3-11'],
      primary_artifact_region_id: 'artifact-m3-4',
    }
    const data: PageAnnotations = {
      page: 145,
      regions: [
        {
          ...annotationData.regions[0]!,
          id: 'artifact-m3-4',
          page: 145,
          kind: 'artifact',
          crop_object_key: 'pages/145/m3-4.png',
        },
        {
          ...annotationData.regions[0]!,
          id: 'artifact-m3-11',
          page: 145,
          kind: 'artifact',
          crop_object_key: 'pages/145/m3-11.png',
        },
      ],
      relations: [],
      records: [primaryRecord],
    }

    const annotations = buildPreviewAnnotations([primaryRecord], data, 145)

    expect(annotations.find((item) => item.regionId === 'artifact-m3-4')?.primaryArtifact).toBe(true)
    expect(annotations.find((item) => item.regionId === 'artifact-m3-11')?.primaryArtifact).toBe(false)
  })

  it('derives the primary artifact from the exact sequence edge for an older response', () => {
    const legacyRecord: ExtractionRecord = {
      ...record,
      fields: {
        artifact_id: {
          raw_value: 'M3：7',
          value: 'M3：7',
          status: 'valid',
          evidence: [],
        },
      },
      region_ids: ['number-m3-7', 'artifact-m3-7', 'number-m3-5', 'artifact-m3-5'],
      primary_artifact_region_id: undefined,
      primary_relation_id: undefined,
    }
    const data: PageAnnotations = {
      page: 145,
      regions: [
        { ...annotationData.regions[0]!, id: 'number-m3-7', page: 145, kind: 'number', text: 'M3:7' },
        { ...annotationData.regions[0]!, id: 'artifact-m3-7', page: 145, kind: 'artifact' },
        { ...annotationData.regions[0]!, id: 'number-m3-5', page: 145, kind: 'number', text: 'M3:5' },
        { ...annotationData.regions[0]!, id: 'artifact-m3-5', page: 145, kind: 'artifact' },
      ],
      relations: [
        {
          ...annotationData.relations[0]!,
          id: 'number-of-m3-7',
          source_region_id: 'number-m3-7',
          target_region_id: 'artifact-m3-7',
          relation_type: 'number_of',
          score: 0.92,
        },
        {
          ...annotationData.relations[0]!,
          id: 'number-of-m3-5',
          source_region_id: 'number-m3-5',
          target_region_id: 'artifact-m3-5',
          relation_type: 'number_of',
          score: 0.99,
        },
      ],
      records: [legacyRecord],
    }

    const annotations = buildPreviewAnnotations([legacyRecord], data, 145)

    expect(annotations.find((item) => item.regionId === 'artifact-m3-7')?.primaryArtifact).toBe(true)
    expect(annotations.find((item) => item.regionId === 'artifact-m3-5')?.primaryArtifact).toBe(false)
  })
})
