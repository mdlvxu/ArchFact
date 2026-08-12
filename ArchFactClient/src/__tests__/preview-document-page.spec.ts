import { describe, expect, it } from 'vitest'
import { resolvePreviewDocumentPage } from '@/domain/preview-document-page'
import type { ExtractionRecord, SourceRegion } from '@/types/extraction'

function region(id: string, page: number, kind: SourceRegion['kind']): SourceRegion {
  return {
    id,
    job_id: 'job-1',
    document_id: 'doc-1',
    page,
    kind,
    bbox: [0.1, 0.1, 0.4, 0.4],
    bbox_px: null,
    text: '',
    confidence: 0.9,
    source: 'test',
    image_id: null,
    crop_object_key: null,
  }
}

function record(id: string, pages: number[]): ExtractionRecord {
  return {
    id,
    job_id: 'job-1',
    record_type: 'artifact',
    source_pages: pages,
    fields: {},
    linkage: {},
    link_hints: {},
    warnings: [],
    review_status: 'unreviewed',
    reviewed_at: null,
    model_run_ids: [],
    region_ids: [],
    relation_ids: [],
  } as ExtractionRecord
}

describe('resolvePreviewDocumentPage', () => {
  it('keeps a non-color primary text page', () => {
    expect(
      resolvePreviewDocumentPage({
        primary_text_page: 146,
        page_numbers: [104, 146],
        regions: [
          region('color', 104, 'color_plate'),
          region('line', 146, 'artifact'),
          region('text', 146, 'text'),
        ],
        text_record: record('text', [146]),
        record: record('color-caption', [104]),
      }),
    ).toBe(146)
  })

  it('replaces a color-plate primary page with the text sibling page', () => {
    expect(
      resolvePreviewDocumentPage({
        primary_text_page: 104,
        page_numbers: [104, 146],
        regions: [
          region('color', 104, 'color_plate'),
          region('caption', 104, 'text'),
          region('line', 146, 'artifact'),
          region('body', 146, 'text'),
        ],
        text_record: record('text', [146]),
        record: record('color-caption', [104]),
      }),
    ).toBe(146)
  })
})
