/** PDF 分页信息：缩略图在页面进入可视区域后按需生成 */
export interface PdfPageItem {
  page: number
  thumbnailUrl: string
  loading: boolean
}
