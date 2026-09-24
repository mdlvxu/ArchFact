import type { ExtractionRecord } from '@/types/extraction'

/**
 * An artifact card is meaningful only when the matching pipeline persisted an
 * artifact crop anchor. Text evidence, a line drawing, or a colour plate alone
 * can be retained in the preview, but must never create an incomplete card.
 */
export function hasArtifactCropBinding(record: ExtractionRecord) {
  return Boolean(record.primary_artifact_region_id || record.thumbnail_region_id)
}
