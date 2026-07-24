export interface ExtractionTemplateField {
  key: string
  label: string
  type: LabelConstraintType
  required: boolean
  instruction?: string
  evidence_kind?: SourceRegionKind | null
}

/** 内容抽取模板，字段 key 一经创建便保持稳定。 */
export interface ExtractionTemplate {
  id: string
  name: string
  fields: ExtractionTemplateField[]
  builtin?: boolean
  custom?: boolean
}

/** 抽取字段可采用的数据约束类型。 */
export type LabelConstraintType = 'Num' | 'Text' | 'Date' | 'Yes/No' | 'Image' | 'Obj' | 'Arr'

/** PDF 内容抽取完成后应用的文本标准化规则。 */
export interface PostProcessingRule {
  id: string
  key: string
  name: string
  description: string
  example: string
  handler: 'builtin' | 'instruction'
  enabled: boolean
  builtin?: boolean
  custom?: boolean
}

/** 后端和 Coze 统一使用的字段类型，避免把 UI 展示值扩散到接口层。 */
export type ExtractionFieldType =
  | 'string'
  | 'number'
  | 'date'
  | 'boolean'
  | 'image'
  | 'object'
  | 'array'

export interface ExtractionFieldSpec {
  key: string
  label: string
  type: ExtractionFieldType
  required: boolean
  instruction?: string
  evidence_kind?: SourceRegionKind | null
}

export interface ExtractionRuleSpec {
  key: string
  name: string
  description: string
  example?: string
  handler: 'builtin' | 'instruction'
}

/** 点击开始抽取时由设置面板提交的完整、可序列化配置快照。 */
export interface ExtractionConfigPayload {
  schemaVersion: '1.0'
  /** Stable business pipeline alias; the UI never sends model paths or Coze workflow IDs. */
  pipelineId: string
  templateId: string
  templateName: string
  fields: ExtractionFieldSpec[]
  postProcessingRules: ExtractionRuleSpec[]
  pages: number[]
}

export type ExtractionJobStatus =
  | 'queued'
  | 'preparing'
  | 'parsing'
  | 'extracting'
  | 'matching'
  | 'merging'
  | 'post_processing'
  | 'completed'
  | 'completed_with_warnings'
  | 'failed'
  | 'cancelling'
  | 'cancelled'

export interface ExtractionJobEvent {
  id: string
  level: 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR'
  message: string
  created_at: string
}

export interface ExtractionJob {
  id: string
  document_id: string
  pipeline_id: string
  status: ExtractionJobStatus
  stage: string
  progress: {
    current: number
    total: number
    percent: number
  }
  cancel_requested: boolean
  error: string | null
  page_issues: Array<{
    page: number
    stage: string
    severity: 'warning' | 'error'
    message: string
  }>
  succeeded_pages: number
  failed_pages: number
  requested_pages?: number[]
  discovered_pages?: number[]
  effective_pages?: number[]
  retry_pages?: number[]
  active_matching_version_id?: string
  created_at: string
  updated_at: string
  attempt_started_at?: string | null
  events: ExtractionJobEvent[]
}

export interface ExtractionEvidence {
  page: number
  quote: string
  /** PDF 页面归一化坐标：[left, top, right, bottom]。 */
  bbox: number[] | null
  region_id?: string | null
  kind?: SourceRegionKind
  relation_ids?: string[]
  linked_region_ids?: string[]
  image_id?: string | null
  crop_object_key?: string | null
  confidence?: number | null
  source?: string
}

export interface ExtractedField {
  raw_value: unknown
  value: unknown
  status: 'valid' | 'missing' | 'needs_review'
  evidence: ExtractionEvidence[]
}

export interface ExtractionRecord {
  id: string
  job_id: string
  record_type: string
  source_pages: number[]
  fields: Record<string, ExtractedField>
  linkage?: {
    identity: {
      artifact_id_raw?: string | null
      artifact_id_normalized?: string | null
    }
    visual_link: {
      figure_no?: string | null
      figure_item_no?: string | null
      plate_no?: string | null
      plate_item_no?: string | null
      caption_raw?: string | null
      evidence_block_ids?: string[]
      evidence?: ExtractionEvidence[]
    }
  }
  link_hints?: {
    artifact_ids?: string[]
    figure_refs?: string[]
    figure_item_nos?: string[]
    plate_refs?: string[]
    caption_texts?: string[]
    aliases?: string[]
  }
  warnings: string[]
  review_status: 'unreviewed' | 'passed' | 'failed'
  reviewed_at: string | null
  model_run_ids?: string[]
  region_ids?: string[]
  relation_ids?: string[]
  associated_pages?: number[]
  thumbnail_region_id?: string | null
  primary_number_region_id?: string | null
  primary_artifact_region_id?: string | null
  primary_relation_id?: string | null
  primary_link_score?: number | null
  fusion_status?: 'unlinked' | 'partial' | 'linked'
  entity_id?: string | null
  entity_confidence?: number | null
  entity_match_status?: 'linked' | 'needs_review' | 'unlinked'
  created_at: string
}

export interface ArtifactEntity {
  id: string
  job_id: string
  document_id: string
  canonical_artifact_id: string | null
  aliases: string[]
  figure_refs: string[]
  plate_refs: string[]
  match_keys: string[]
  record_ids: string[]
  region_ids: string[]
  relation_ids: string[]
  source_pages: number[]
  associated_pages: number[]
  thumbnail_region_id: string | null
  confidence: number
  link_status: 'linked' | 'needs_review' | 'unlinked'
  link_reasons: string[]
  version: string
}

export type PreviewAnnotationKind = 'line_drawing' | 'text' | 'color_plate'
export type SourceRegionKind =
  | PreviewAnnotationKind
  | 'artifact'
  | 'caption'
  | 'number'
  | 'group'
  | 'grave_drawing'
  | 'other'

export interface SourceRegion {
  id: string
  job_id: string
  document_id: string
  page: number
  kind: SourceRegionKind
  bbox: [number, number, number, number]
  bbox_px: [number, number, number, number] | null
  text: string
  confidence: number | null
  source: string
  model_run_id?: string | null
  image_id: string | null
  crop_object_key: string | null
  crop_width?: number | null
  crop_height?: number | null
  crop_content_type?: string | null
  crop_error?: string | null
  ocr_raw_text?: string | null
  ocr_confidence?: number | null
  ocr_source?: string | null
  ocr_model?: string | null
  ocr_version?: string | null
  ocr_model_run_id?: string | null
  ocr_error?: string | null
}

export interface RegionRelation {
  id: string
  job_id: string
  source_region_id: string
  target_region_id: string
  relation_type: string
  score: number | null
  method: string
  version: string
  model_run_id: string | null
  review_status: 'unreviewed' | 'accepted' | 'rejected'
  reviewed_at?: string | null
  reviewer?: string | null
  review_reason?: string
  supersedes_relation_id?: string | null
  superseded_by_relation_id?: string | null
}

export interface RelationRevision {
  id: string
  job_id: string
  relation_id: string
  action: 'review' | 'rebind'
  before: unknown
  after: unknown
  reason: string
  reviewer: string | null
  created_at: string
}

export interface PageAnnotations {
  page: number
  regions: SourceRegion[]
  relations: RegionRelation[]
  records: ExtractionRecord[]
}

export interface RecordEvidenceContext {
  record: ExtractionRecord
  entity?: ArtifactEntity | null
  page_numbers: number[]
  regions: SourceRegion[]
  relations: RegionRelation[]
}

export interface ModelRun {
  id: string
  job_id: string
  stage: string
  provider: string
  model: string
  version: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface RecordRevision {
  id: string
  job_id: string
  record_id: string
  field_key: string
  decision: 'accepted' | 'rejected' | 'corrected'
  before: unknown
  after: unknown
  reason: string
  reviewer: string | null
  created_at: string
}

export type RematchStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'applying'
  | 'applied'
  | 'failed'
  | 'cancelling'
  | 'cancelled'

export interface RematchReport {
  total_records: number
  linked_records: number
  partial_records: number
  unlinked_records: number
  complete_chains: number
  ocr_exact_relations: number
  layout_fallback_relations: number
  conflict_relations: number
  confidence: { high: number; medium: number; low: number }
  delta: { added: number; removed: number; changed: number; unchanged: number }
  protection: {
    accepted_relations: number
    rejected_relations: number
    passed_records: number
    protected_relations: number
  }
}

export interface RematchRun {
  id: string
  job_id: string
  base_matching_version_id: string
  status: RematchStatus
  preserve_reviewed: boolean
  apply_immediately: boolean
  cancel_requested: boolean
  progress: { current: number; total: number; percent: number; stage: string }
  report: RematchReport | null
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  applied_at: string | null
}

export type RematchChangeKind = 'added' | 'removed' | 'changed'

export interface RematchRelationChange {
  change: RematchChangeKind
  relation_id: string
  relation_type: string
  source_region_id: string
  target_region_id: string
  before_method: string | null
  after_method: string | null
  before_score: number | null
  after_score: number | null
  protected: boolean
}

export interface RematchChanges {
  total: number
  items: RematchRelationChange[]
}

/** Data Preview 中一个可点击证据标注及其关联内容。 */
export interface PreviewAnnotation {
  id: string
  regionId?: string
  recordId?: string
  fieldKey: string
  page: number
  kind: PreviewAnnotationKind
  regionKind?: SourceRegionKind
  label: string
  quote: string
  bbox: [number, number, number, number]
  approximate: boolean
  relationIds?: string[]
  source?: string
  confidence?: number | null
  /** Backend-generated region crop; PDF content itself is never translated or rewritten. */
  cropUrl?: string
  /** UI-only aggregate of adjacent semantic text evidence. */
  grouped?: boolean
  groupedRegionIds?: string[]
  /** The backend-selected artifact region reached through the exact sequence label. */
  primaryArtifact?: boolean
}
