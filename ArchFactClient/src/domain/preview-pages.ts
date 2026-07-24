import type { PdfPageItem } from '@/types/pdf'

/**
 * 从完整 PDF 页列表中选出本次任务实际提交的页面。
 * 保留原始 PdfPageItem 引用，使已生成的缩略图和 loading 状态可以继续复用。
 */
export function filterExtractedPdfPages(
  pages: PdfPageItem[],
  extractedPageNumbers: Iterable<number>,
) {
  const extractedPages = new Set(extractedPageNumbers)
  return pages.filter((item) => extractedPages.has(item.page))
}
