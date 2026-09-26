import { describe, expect, it } from 'vitest'

import {
  hasArtifactCropBinding,
} from '@/domain/preview-card-visibility'
import type { ExtractionRecord } from '@/types/extraction'

function record(overrides: Partial<ExtractionRecord> = {}): ExtractionRecord {
  return {
    id: 'record-1',
    job_id: 'job-1',
    record_type: 'artifact',
    source_pages: [12],
    fields: {},
    warnings: [],
    review_status: 'unreviewed',
    reviewed_at: null,
    created_at: '2026-09-23T00:00:00Z',
    ...overrides,
  }
}

describe('preview card visibility', () => {
  it('does not generate a catalog card for a record with text information only', () => {
    expect(hasArtifactCropBinding(record())).toBe(false)
  })

  it('keeps a record with a persisted artifact crop binding', () => {
    expect(
      hasArtifactCropBinding(
        record({
          primary_artifact_region_id: 'artifact-region-1',
          thumbnail_region_id: 'artifact-region-1',
        }),
      ),
    ).toBe(true)
  })

  it('accepts the persisted thumbnail fallback for legacy matched records', () => {
    expect(hasArtifactCropBinding(record({ thumbnail_region_id: 'artifact-region-2' }))).toBe(true)
  })
})
