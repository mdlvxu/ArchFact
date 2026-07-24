import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import ArchaeologicalCatalogs from '@/components/business/ArchaeologicalCatalogs.vue'
import ContentPreview from '@/components/business/ContentPreview.vue'
import type {
  ExtractionRecord,
  PreviewAnnotation,
  RegionRelation,
  SourceRegion,
} from '@/types/extraction'

const annotations: PreviewAnnotation[] = [
  {
    id: 'annotation-red',
    regionId: 'region-red',
    recordId: 'record-1',
    fieldKey: 'figure_caption',
    page: 8,
    kind: 'line_drawing',
    label: 'Figure Caption',
    quote: 'Figure 8:12',
    bbox: [0.2, 0.25, 0.42, 0.48],
    approximate: false,
  },
  {
    id: 'annotation-green',
    regionId: 'region-green',
    recordId: 'record-1',
    fieldKey: 'texture',
    page: 8,
    kind: 'text',
    label: 'Texture',
    quote: 'Fine-paste gray pottery',
    bbox: [0.12, 0.7, 0.8, 0.78],
    approximate: false,
  },
]

function region(
  id: string,
  kind: SourceRegion['kind'],
  bbox: SourceRegion['bbox'],
): SourceRegion {
  return {
    id,
    job_id: 'job-1',
    document_id: 'document-1',
    page: 8,
    kind,
    bbox,
    bbox_px: null,
    text: '',
    confidence: 0.9,
    source: 'test',
    image_id: null,
    crop_object_key: null,
  }
}

const regions: SourceRegion[] = [
  region('region-red', 'line_drawing', [0.2, 0.25, 0.42, 0.48]),
  region('region-red-target', 'artifact', [0.62, 0.2, 0.88, 0.45]),
  region('region-green', 'text', [0.12, 0.7, 0.8, 0.78]),
  region('region-green-target', 'artifact', [0.65, 0.5, 0.9, 0.66]),
]

const relations: RegionRelation[] = [
  {
    id: 'relation-red',
    job_id: 'job-1',
    source_region_id: 'region-red',
    target_region_id: 'region-red-target',
    relation_type: 'drawing_of',
    score: 0.9,
    method: 'test',
    version: '1',
    model_run_id: null,
    review_status: 'unreviewed',
  },
  {
    id: 'relation-green',
    job_id: 'job-1',
    source_region_id: 'region-green',
    target_region_id: 'region-green-target',
    relation_type: 'evidence_for',
    score: 0.8,
    method: 'test',
    version: '1',
    model_run_id: null,
    review_status: 'unreviewed',
  },
]

const record: ExtractionRecord = {
  id: 'record-1',
  job_id: 'job-1',
  record_type: 'artifact',
  source_pages: [8],
  fields: {
    artifact_id: {
      raw_value: 'H125:1',
      value: 'H125:1',
      status: 'valid',
      evidence: [{ page: 8, quote: 'H125:1', bbox: [0.2, 0.2, 0.35, 0.24] }],
    },
    texture: {
      raw_value: 'Fine-paste gray pottery',
      value: 'Fine-paste gray pottery',
      status: 'valid',
      evidence: [{ page: 8, quote: 'Fine-paste gray pottery', bbox: [0.12, 0.7, 0.8, 0.78] }],
    },
  },
  warnings: [],
  review_status: 'unreviewed',
  reviewed_at: null,
  created_at: '2026-07-16T10:00:00Z',
}

describe('Data Preview redesigned interactions', () => {
  it('requires a structured failure category before emitting a failed review', async () => {
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [record],
        pages: [],
        selectedPage: 8,
        selectedRecordId: record.id,
        mode: 'verify',
      },
    })

    await wrapper.find('.review-button--fail').trigger('click')
    await wrapper.find('.failure-select summary').trigger('click')
    await wrapper.find('[data-failure-code="caption_match_error"]').trigger('click')
    await wrapper.find('.failure-editor input').setValue('图注指向了错误序号')
    await wrapper.find('.failure-editor').trigger('submit')

    expect(wrapper.emitted('review')?.[0]).toEqual([
      record,
      'failed',
      'caption_match_error',
      '图注指向了错误序号',
    ])
  })

  it('keeps the reviewed catalog item visible when its inline review panel is collapsed', () => {
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [record],
        pages: [],
        selectedPage: 8,
        selectedRecordId: record.id,
        mode: 'verify',
        detailsOpen: false,
      },
    })

    expect(wrapper.find('.verification-panel').exists()).toBe(false)
    expect(wrapper.find('.catalog-item').exists()).toBe(true)
    expect(wrapper.find('.catalog-item').classes()).toContain('catalog-item--active')
  })

  it('shows matching-version changes and keeps stale fixed samples visible', () => {
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [record],
        pages: [],
        selectedPage: 8,
        selectedRecordId: record.id,
        mode: 'verify',
        matchingVersionId: 'rematch-2',
        reviewItems: {
          [record.id]: {
            record_id: record.id,
            verdict: 'unreviewed',
            failure_reason: '',
            relation_changed: true,
            reviewed_at: null,
          },
        },
        staleItems: [{
          record_id: 'record-stale',
          verdict: 'stale',
          failure_reason: 'missing',
          stale: true,
          reviewed_at: null,
        }],
      },
    })

    expect(wrapper.find('.verification-context').text()).toContain('rematch-2')
    expect(wrapper.find('.verification-context--changed').exists()).toBe(true)
    expect(wrapper.find('.stale-sample').text()).toContain('record-stale')
  })

  it('switches the active blue relation line when another annotation is selected', async () => {
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,preview',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations,
        regions,
        relations,
        activeAnnotationId: 'annotation-red',
      },
    })

    const firstPath = wrapper.find('.relation-lines path').attributes('d')
    await wrapper.findAll('.evidence-box')[1]?.trigger('click')
    expect(wrapper.emitted('selectAnnotation')?.[0]).toEqual(['annotation-green'])

    await wrapper.setProps({ activeAnnotationId: 'annotation-green' })
    expect(wrapper.find('.relation-lines path').attributes('d')).not.toBe(firstPath)
    expect(wrapper.findAll('.evidence-box')[1]?.classes()).toContain('evidence-box--active')
    expect(wrapper.find('.evidence-target--text').exists()).toBe(false)

    await wrapper.find('.relation-review__actions button').trigger('click')
    expect(wrapper.emitted('reviewRelation')?.[0]).toEqual(['relation-green', 'accepted'])

    await wrapper.findAll('.relation-review__actions button')[2]?.trigger('click')
    await wrapper.findAll('.evidence-box')[0]?.trigger('click')
    expect(wrapper.emitted('rebindRelation')?.[0]).toEqual([{
      relationId: 'relation-green',
      sourceRegionId: 'region-green',
      targetRegionId: 'region-red',
      relationType: 'evidence_for',
    }])
  })

  it('keeps overlapping relation controls without drawing a short line inside the evidence box', () => {
    const sharedRegions: SourceRegion[] = [
      region('line-region', 'line_drawing', [0.15, 0.2, 0.4, 0.45]),
      region('text-region', 'caption', [0.12, 0.7, 0.75, 0.78]),
      region('artifact-anchor', 'artifact', [0.2, 0.25, 0.42, 0.48]),
      region('group-region', 'group', [0.05, 0.1, 0.9, 0.9]),
    ]
    const sharedAnnotations: PreviewAnnotation[] = [
      { ...annotations[0]!, id: 'line-annotation', regionId: 'line-region' },
      { ...annotations[1]!, id: 'text-annotation', regionId: 'text-region' },
    ]
    const sharedRelations: RegionRelation[] = [
      {
        ...relations[0]!,
        id: 'contains-structural',
        source_region_id: 'group-region',
        target_region_id: 'line-region',
        relation_type: 'contains',
        score: 1,
      },
      {
        ...relations[0]!,
        id: 'drawing-to-artifact',
        source_region_id: 'line-region',
        target_region_id: 'artifact-anchor',
        relation_type: 'drawing_of',
      },
      {
        ...relations[1]!,
        id: 'caption-to-artifact',
        source_region_id: 'text-region',
        target_region_id: 'artifact-anchor',
        relation_type: 'caption_of',
      },
    ]

    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,preview',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: sharedAnnotations,
        regions: sharedRegions,
        relations: sharedRelations,
        activeAnnotationId: 'line-annotation',
      },
    })

    expect(wrapper.find('.evidence-target--color_plate').exists()).toBe(false)
    expect(wrapper.findAll('.relation-lines path')).toHaveLength(0)
    expect(wrapper.find('.relation-review__type').text()).toBe('drawing of')
  })

  it('hides sequence-number boxes and sequence-only relations from the preview canvas', () => {
    const numberAnnotation: PreviewAnnotation = {
      ...annotations[1]!,
      id: 'number-annotation',
      regionId: 'number-region',
      fieldKey: 'number',
      regionKind: 'number',
      label: 'xuhao',
      quote: '8',
      bbox: [0.72, 0.55, 0.76, 0.59],
    }
    const numberRegion = region('number-region', 'number', numberAnnotation.bbox)
    const numberRelation: RegionRelation = {
      ...relations[0]!,
      id: 'number-to-artifact',
      source_region_id: 'number-region',
      target_region_id: 'region-red-target',
      relation_type: 'number_of',
    }
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,preview',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: [annotations[1]!, numberAnnotation],
        regions: [regions[2]!, numberRegion, regions[1]!],
        relations: [numberRelation],
        activeAnnotationId: numberAnnotation.id,
      },
    })

    expect(wrapper.findAll('.evidence-box')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('xuhao')
    expect(wrapper.find('[data-relation-key="number-to-artifact"]').exists()).toBe(false)
    expect(wrapper.find('.relation-review').exists()).toBe(false)
  })

  it('renders a tiny detector box at its exact size while keeping it selectable', () => {
    const tinyAnnotation: PreviewAnnotation = {
      ...annotations[0]!,
      id: 'tiny-annotation',
      bbox: [0.2, 0.25, 0.205, 0.255],
    }
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,preview',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: [tinyAnnotation],
        regions: [],
        relations: [],
        activeAnnotationId: tinyAnnotation.id,
      },
    })

    const style = wrapper.find('.evidence-box').attributes('style')
    expect(Number.parseFloat(style.match(/width:\s*([\d.]+)%/)?.[1] ?? '99')).toBeLessThan(1)
    expect(Number.parseFloat(style.match(/height:\s*([\d.]+)%/)?.[1] ?? '99')).toBeLessThan(1)
  })

  it('keeps the interactive workspace ratio stable and tightens only the OCR line horizontally', () => {
    const textRegion = region('ocr-line', 'text', [0.1, 0.7, 0.9, 0.75])
    textRegion.text = '该器物表面颜色为红土，器形完整'
    const textAnnotation: PreviewAnnotation = {
      ...annotations[1]!,
      id: 'surface-color-evidence',
      regionId: textRegion.id,
      fieldKey: 'surface_color',
      regionKind: 'text',
      label: 'Surface Color',
      quote: '红土',
      bbox: textRegion.bbox,
    }
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,current-page',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: [textAnnotation],
        regions: [textRegion],
        relations: [],
        activeAnnotationId: textAnnotation.id,
      },
    })

    const canvasStyle = wrapper.find('.annotation-canvas').attributes('style')
    expect(canvasStyle).toContain('width: 1000px')
    expect(canvasStyle).toContain('height: 800px')

    const boxStyle = wrapper.find('.evidence-box').attributes('style')
    const renderedWidth = Number.parseFloat(boxStyle.match(/width:\s*([\d.]+)%/)?.[1] ?? '100')
    expect(renderedWidth).toBeLessThan(30)
    expect(renderedWidth).toBeGreaterThan(10)
    expect(Number.parseFloat(boxStyle.match(/height:\s*([\d.]+)%/)?.[1] ?? '0')).toBeGreaterThan(4)
  })

  it('merges adjacent field evidence lines into one concise text block', () => {
    const textRegions = [
      region('text-line-1', 'text', [0.1, 0.6, 0.82, 0.63]),
      region('text-line-2', 'text', [0.1, 0.635, 0.8, 0.665]),
      region('text-line-3', 'text', [0.1, 0.67, 0.76, 0.7]),
    ]
    const textAnnotations: PreviewAnnotation[] = textRegions.map((item, index) => ({
      id: `field-evidence-${index}`,
      regionId: item.id,
      recordId: 'record-1',
      fieldKey: ['surface_color', 'texture', 'measurements'][index]!,
      page: 8,
      kind: 'text',
      regionKind: 'text',
      label: `Field ${index + 1}`,
      quote: `Evidence line ${index + 1}`,
      bbox: item.bbox,
      approximate: false,
    }))
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,current-page',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: textAnnotations,
        regions: textRegions,
        relations: [],
        activeAnnotationId: textAnnotations[0]!.id,
      },
    })

    const textBoxes = wrapper.findAll('.evidence-box--text')
    expect(textBoxes).toHaveLength(1)
    expect(textBoxes[0]!.text()).toContain('Text Evidence')
    const style = textBoxes[0]!.attributes('style')
    expect(Number.parseFloat(style.match(/width:\s*([\d.]+)%/)?.[1] ?? '0'))
      .toBeGreaterThan(60)
  })

  it('focuses a high-resolution source page around a small related artifact box', () => {
    const visualAnnotations: PreviewAnnotation[] = [
      {
        ...annotations[1]!,
        id: 'annotation-text',
        regionId: 'region-text',
      },
      {
        ...annotations[0]!,
        id: 'annotation-line',
        regionId: 'region-line',
        page: 9,
        bbox: [0.55, 0.5, 0.59, 0.54],
        cropUrl: '/api/v1/extraction-jobs/job-1/regions/region-line/crop',
      },
    ]
    const lineRegion = region('region-line', 'artifact', [0.55, 0.5, 0.59, 0.54])
    lineRegion.page = 9
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,current-page',
        pagePreviewUrls: {
          9: 'data:image/png;base64,line-page',
        },
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: visualAnnotations,
        regions: [
          region('region-text', 'text', annotations[1]!.bbox),
          lineRegion,
        ],
        relations: [],
        activeAnnotationId: 'annotation-text',
      },
    })

    const focusedImage = wrapper.find('.evidence-target--line_drawing img')
    expect(focusedImage.attributes('src'))
      .toBe('data:image/png;base64,line-page')
    const imageStyle = focusedImage.attributes('style')
    expect(Number.parseFloat(imageStyle.match(/width:\s*([\d.]+)%/)?.[1] ?? '0'))
      .toBeGreaterThan(300)
    const regionStyle = wrapper
      .find('.evidence-target--line_drawing .evidence-target__region-box')
      .attributes('style')
    expect(Number.parseFloat(regionStyle.match(/width:\s*([\d.]+)%/)?.[1] ?? '0'))
      .toBeGreaterThan(10)
    expect(wrapper.find('.evidence-target--artifact_crop img').attributes('src'))
      .toBe('/api/v1/extraction-jobs/job-1/regions/region-line/crop')
    expect(wrapper.find('.evidence-targets').classes()).toContain('evidence-targets--staggered')
    expect(wrapper.findAll('.relation-lines path')).toHaveLength(3)
    expect(
      wrapper.find('.relation-lines g[data-relation-key^="card:text_to_artifact_crop:"]').exists(),
    ).toBe(true)
  })

  it('adds a centered third color-plate column without shrinking the first two columns', () => {
    const textAnnotation: PreviewAnnotation = {
      ...annotations[1]!,
      id: 'color-layout-text',
      regionId: 'color-layout-text-region',
    }
    const lineAnnotation: PreviewAnnotation = {
      ...annotations[0]!,
      id: 'color-layout-line',
      regionId: 'color-layout-line-region',
      page: 9,
      bbox: [0.48, 0.42, 0.56, 0.55],
      cropUrl: '/api/v1/extraction-jobs/job-1/regions/color-layout-line-region/crop',
    }
    const colorAnnotation: PreviewAnnotation = {
      ...annotations[0]!,
      id: 'color-layout-plate',
      regionId: 'color-layout-plate-region',
      page: 10,
      kind: 'color_plate',
      bbox: [0.32, 0.28, 0.56, 0.62],
      cropUrl: '/api/v1/extraction-jobs/job-1/regions/color-layout-plate-region/crop',
    }
    const lineRegion = region('color-layout-line-region', 'artifact', lineAnnotation.bbox)
    lineRegion.page = 9
    const colorRegion = region('color-layout-plate-region', 'color_plate', colorAnnotation.bbox)
    colorRegion.page = 10
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,current-page',
        pagePreviewUrls: {
          9: 'data:image/png;base64,line-page',
          10: 'data:image/png;base64,color-page',
        },
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: [textAnnotation, lineAnnotation, colorAnnotation],
        regions: [
          region('color-layout-text-region', 'text', textAnnotation.bbox),
          lineRegion,
          colorRegion,
        ],
        relations: [],
        activeAnnotationId: textAnnotation.id,
      },
    })

    expect(wrapper.find('.annotation-canvas').attributes('style')).toContain('width: 1400px')
    expect(wrapper.find('.evidence-targets').classes()).toContain('evidence-targets--has-color')
    expect(wrapper.find('.evidence-targets').classes()).toContain('evidence-targets--staggered')
    expect(wrapper.find('.evidence-target--color_plate img').attributes('src'))
      .toBe('data:image/png;base64,color-page')
    expect(wrapper.findAll('.relation-lines path')).toHaveLength(5)
    expect(
      wrapper.find('.relation-lines g[data-relation-key^="card:line_to_color_plate:"]').exists(),
    ).toBe(true)
    expect(
      wrapper.find('.relation-lines g[data-relation-key^="card:crop_to_color_plate:"]').exists(),
    ).toBe(true)
  })

  it('terminates a cross-page line on the highlighted artifact box, not the page card', async () => {
    const textAnnotation: PreviewAnnotation = {
      ...annotations[1]!,
      id: 'current-text',
      regionId: 'current-text-region',
    }
    const lineAnnotation: PreviewAnnotation = {
      ...annotations[0]!,
      id: 'related-line',
      regionId: 'related-line-region',
      page: 9,
      bbox: [0.55, 0.5, 0.59, 0.54],
    }
    const targetRegion = region('related-line-region', 'line_drawing', lineAnnotation.bbox)
    targetRegion.page = 9
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,current-page',
        pagePreviewUrls: { 9: 'data:image/png;base64,line-page' },
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations: [textAnnotation, lineAnnotation],
        regions: [
          region('current-text-region', 'text', textAnnotation.bbox),
          targetRegion,
        ],
        relations: [],
        activeAnnotationId: textAnnotation.id,
      },
    })

    const canvas = wrapper.find('.annotation-canvas').element as HTMLElement
    const targetBox = wrapper.find('.evidence-target__region-box').element as HTMLElement
    canvas.getBoundingClientRect = () => ({
      x: 0, y: 0, left: 0, top: 0, right: 1000, bottom: 800,
      width: 1000, height: 800, toJSON: () => ({}),
    })
    targetBox.getBoundingClientRect = () => ({
      x: 830, y: 140, left: 830, top: 140, right: 900, bottom: 200,
      width: 70, height: 60, toJSON: () => ({}),
    })
    await wrapper.find('.evidence-target__page-image').trigger('load')
    await nextTick()

    const path = wrapper.find('.relation-lines path').attributes('d')
    const coordinates = path.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? []
    const targetX = coordinates.at(-2) ?? 0
    expect(wrapper.find('.evidence-target--line_drawing').exists()).toBe(true)
    expect(targetX).toBe(830)
  })

  it('connects existing line and text boxes even before a backend relation is available', () => {
    const wrapper = mount(ContentPreview, {
      props: {
        page: 8,
        previewUrl: 'data:image/png;base64,current-page',
        fileName: 'catalog.pdf',
        loading: false,
        interactive: true,
        annotations,
        regions: [regions[0]!, regions[2]!],
        relations: [],
        activeAnnotationId: 'annotation-red',
      },
    })

    expect(wrapper.find('.evidence-target--color_plate').exists()).toBe(false)
    expect(wrapper.findAll('.relation-lines path')).toHaveLength(1)
    expect(wrapper.find('.relation-lines g[data-derived="true"]').exists()).toBe(true)
  })

  it('keeps browse mode read-only and only exposes PASS/FAIL in verification mode', async () => {
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [record],
        pages: [{ page: 8, thumbnailUrl: '', loading: false }],
        selectedPage: null,
      },
    })

    expect(wrapper.find('.verification-panel').exists()).toBe(false)
    expect(wrapper.findAll('.catalog-item')).toHaveLength(1)
    await wrapper.find('.catalog-item').trigger('click')
    expect(wrapper.emitted('selectRecord')?.[0]).toEqual([record])

    await wrapper.setProps({ selectedPage: 8, selectedRecordId: record.id })
    expect(wrapper.find('.verification-panel').exists()).toBe(true)
    expect(wrapper.find('.review-button--pass').exists()).toBe(false)

    await wrapper.setProps({ mode: 'verify' })
    await wrapper.find('.review-button--pass').trigger('click')
    expect(wrapper.emitted('review')?.[0]).toEqual([record, 'passed'])
  })

  it('filters catalog search by archaeological field in browse mode', async () => {
    const field = (value: string) => ({
      raw_value: value,
      value,
      status: 'valid' as const,
      evidence: [],
    })
    const potteryRecord: ExtractionRecord = {
      ...record,
      fields: { ...record.fields, category: field('陶器') },
    }
    const jadeRecord: ExtractionRecord = {
      ...record,
      id: 'record-jade',
      source_pages: [9],
      fields: {
        ...record.fields,
        artifact_id: field('J1'),
        category: field('玉器'),
      },
    }
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [potteryRecord, jadeRecord],
        pages: [],
        selectedPage: null,
        mode: 'browse',
      },
    })

    expect(wrapper.find('.filter-menu').exists()).toBe(true)
    await wrapper.find('[data-filter-value="category"]').trigger('click')
    await wrapper.find('.search-box input').setValue('玉器')
    expect(wrapper.findAll('.catalog-item')).toHaveLength(1)
    expect(wrapper.find('.catalog-item').text()).toContain('J1')

    await wrapper.find('[data-filter-value="page"]').trigger('click')
    await wrapper.find('.search-box input').setValue('8')
    expect(wrapper.findAll('.catalog-item')).toHaveLength(1)
    expect(wrapper.find('.catalog-item').text()).toContain('H125:1')
  })

  it('inserts artifact details directly above the selected catalog item', () => {
    const secondRecord: ExtractionRecord = {
      ...record,
      id: 'record-2',
      fields: {
        ...record.fields,
        artifact_id: {
          ...record.fields.artifact_id!,
          raw_value: 'H125:2',
          value: 'H125:2',
        },
      },
    }
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [record, secondRecord],
        pages: [{ page: 8, thumbnailUrl: '', loading: false }],
        selectedPage: 8,
        selectedRecordId: secondRecord.id,
      },
    })

    const entries = wrapper.findAll('.catalog-entry')
    expect(entries[0]?.find('.verification-panel').exists()).toBe(false)
    expect(entries[1]?.find('.verification-panel').exists()).toBe(true)
    expect(entries[1]?.element.firstElementChild?.classList.contains('verification-panel')).toBe(true)
    expect(entries[1]?.element.lastElementChild?.classList.contains('catalog-item')).toBe(true)
  })

  it('expands only the first catalog item details below its item', () => {
    const secondRecord: ExtractionRecord = {
      ...record,
      id: 'record-2',
    }
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [record, secondRecord],
        pages: [{ page: 8, thumbnailUrl: '', loading: false }],
        selectedPage: 8,
        selectedRecordId: record.id,
      },
    })

    const firstEntry = wrapper.findAll('.catalog-entry')[0]!
    expect(firstEntry.classes()).toContain('catalog-entry--first-selected')
    expect(firstEntry.find('.catalog-item').exists()).toBe(true)
    expect(firstEntry.find('.verification-panel').exists()).toBe(true)
  })

  it('uses the matched region crop in both figure details and catalog cards', () => {
    const recordWithFigure: ExtractionRecord = {
      ...record,
      thumbnail_region_id: 'drawing-region-6',
      region_ids: ['drawing-region-6'],
      fields: {
        ...record.fields,
        figure_caption: {
          raw_value: '图6',
          value: '图6',
          status: 'valid',
          evidence: [{ page: 8, quote: '图六', bbox: [0.1, 0.7, 0.3, 0.75] }],
        },
      },
    }
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [recordWithFigure],
        pages: [{ page: 8, thumbnailUrl: 'data:image/png;base64,full-page', loading: false }],
        selectedPage: 8,
        selectedRecordId: recordWithFigure.id,
        jobId: 'job-1',
      },
    })

    const expectedCrop = '/api/v1/extraction-jobs/job-1/regions/drawing-region-6/crop'
    expect(wrapper.find('.verification-figure img').attributes('src')).toBe(expectedCrop)
    expect(wrapper.find('.catalog-item__visual img').attributes('src')).toBe(expectedCrop)
    expect(wrapper.html()).not.toContain('data:image/png;base64,full-page')
  })

  it('lays out category and completeness together with full-width description and figure rows', () => {
    const field = (value: string) => ({
      raw_value: value,
      value,
      status: 'valid' as const,
      evidence: [],
    })
    const recordWithDetails: ExtractionRecord = {
      ...record,
      thumbnail_region_id: 'drawing-region-6',
      fields: {
        artifact_id: field('T5'),
        surface_color: field('—'),
        texture: field('—'),
        measurements: field('长20、3布'),
        morphological_description: field('宽，为遗迹，探间的郡清除发掘过程中全面揭露'),
        category: field('良渚文化遗迹'),
        figure_caption: field('图6'),
        completeness: field('—'),
      },
    }
    const wrapper = mount(ArchaeologicalCatalogs, {
      props: {
        records: [recordWithDetails],
        pages: [{ page: 8, thumbnailUrl: '', loading: false }],
        selectedPage: 8,
        selectedRecordId: recordWithDetails.id,
        jobId: 'job-1',
      },
    })

    const detailRows = wrapper.findAll('.verification-fields > div')
    const categoryRow = wrapper.find('.verification-field--category')
    const completenessRow = wrapper.find('.verification-field--completeness')
    const descriptionRow = wrapper.find('.verification-field--description')
    const figureRow = wrapper.find('.verification-field--figure')

    const detailElements = detailRows.map((row) => row.element)
    expect(detailElements.indexOf(categoryRow.element)).toBeLessThan(detailElements.indexOf(completenessRow.element))
    expect(detailElements.indexOf(completenessRow.element)).toBeLessThan(detailElements.indexOf(descriptionRow.element))
    expect(detailElements.indexOf(descriptionRow.element)).toBeLessThan(detailElements.indexOf(figureRow.element))
    expect(categoryRow.classes()).toContain('verification-field--category')
    expect(completenessRow.classes()).toContain('verification-field--completeness')
    expect(descriptionRow.classes()).toContain('verification-field--wide')
    expect(figureRow.classes()).toContain('verification-field--wide')
    expect(wrapper.find('.verification-figure img').exists()).toBe(true)
    expect(wrapper.find('.verification-figure span').text()).toBe('图6')
  })
})
