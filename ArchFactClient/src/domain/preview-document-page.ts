import type { RecordEvidenceContext, SourceRegion } from '@/types/extraction'

function colorPlatePagesFromRegions(regions: SourceRegion[]) {
  return new Set(
    regions
      .filter((region) => region.kind === 'color_plate')
      .map((region) => region.page),
  )
}

/**
 * Left preview column must show a non-color page whenever one exists.
 * Color plates are optional supporting evidence for the third column only.
 */
export function resolvePreviewDocumentPage(context: Pick<
  RecordEvidenceContext,
  'primary_text_page' | 'page_numbers' | 'regions' | 'text_record' | 'record'
>) {
  const colorPages = colorPlatePagesFromRegions(context.regions)
  const primary = context.primary_text_page
  if (!colorPages.has(primary)) return primary

  const preferredPages = [
    ...(context.text_record?.source_pages ?? []),
    ...(context.record?.source_pages ?? []),
    ...context.page_numbers,
  ]
  for (const page of preferredPages) {
    if (typeof page === 'number' && page > 0 && !colorPages.has(page)) return page
  }
  return primary
}
