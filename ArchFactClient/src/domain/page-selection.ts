import type { PdfPageItem } from '@/types/pdf'

export const DEFAULT_EXTRACTION_PAGE_COUNT = 5

/** PDF 导入后的初始抽取范围：最多选择文档开头五页。 */
export function getDefaultExtractionPages(pages: PdfPageItem[]) {
  return pages
    .slice(0, DEFAULT_EXTRACTION_PAGE_COUNT)
    .map((item) => item.page)
}
