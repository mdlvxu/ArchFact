import { describe, expect, it } from 'vitest'
import { filterExtractedPdfPages } from '@/domain/preview-pages'
import type { PdfPageItem } from '@/types/pdf'

function createPages(total: number): PdfPageItem[] {
  return Array.from({ length: total }, (_, index) => ({
    page: index + 1,
    thumbnailUrl: index === 4 ? 'page-5.png' : '',
    loading: false,
  }))
}

describe('Data Preview 抽取页导航', () => {
  it('只保留任务提交的 PDF 页面并维持原页码顺序', () => {
    const pages = createPages(8)

    const result = filterExtractedPdfPages(pages, [7, 2, 5, 5, 99])

    expect(result.map((item) => item.page)).toEqual([2, 5, 7])
    expect(result[1]).toBe(pages[4])
    expect(result[1]?.thumbnailUrl).toBe('page-5.png')
  })

  it('任务尚未提交页面时不显示完整 PDF 列表', () => {
    expect(filterExtractedPdfPages(createPages(4), [])).toEqual([])
  })
})
