import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@/api/http', () => ({ default: {}, get: vi.fn(), del: vi.fn(), patch: vi.fn(), put: vi.fn(), post: api.post }))

import { importGoldWorkbook } from '@/api/modules/extraction'

describe('gold workbook upload progress', () => {
  beforeEach(() => api.post.mockReset())

  it('forwards upload bytes without presenting upload completion as server completion', async () => {
    const progress = vi.fn()
    api.post.mockResolvedValue({ id: 'baseline-1' })
    const result = await importGoldWorkbook({
      documentId: 'doc-1', name: 'test', version: '1.0', keyMode: 'context_sequence',
      fieldMapping: { context_id: '遗迹号', sequence_no: '序号' },
      file: new File(['data'], 'test.xlsx'), onUploadProgress: progress,
    })
    const options = api.post.mock.calls[0][2]
    options.onUploadProgress({ loaded: 25, total: 100 })
    options.onUploadProgress({ loaded: 100, total: 100 })
    expect(progress.mock.calls).toEqual([[25, 100], [100, 100]])
    expect(result.id).toBe('baseline-1')
  })
})
