export type QualityEvaluationStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'terminated'

export interface GoldDataset {
  id: string
  name: string
  document_id: string
  document_filename: string
  report_key: string
  version: string
  status: 'ready' | 'importing' | 'failed'
  source_type: string
  record_count: number
  region_count: number
  asset_count: number
  link_count: number
  matched_artifact_assets: number
  matched_color_plate_assets: number
  source_document_verified: boolean
  workbook_name: string
  key_mode: 'artifact_id' | 'context_sequence'
  field_mapping: Record<string, string>
  capabilities: Record<string, boolean>
  source_row_count: number
  unkeyed_row_count: number
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface QualityEvaluationSummary {
  predicted_records: number
  gold_records: number
  matched_records: number
  unmatched_gold_records?: number
  unmatched_predicted_records: number
  ambiguous_records: number
  artifact_id_precision: number
  artifact_id_recall: number | null
  field_macro_score: number | null
  ocr_anchor_score: number | null
  relation_score: number | null
  detection_macro_f1: number | null
  full_document_scope: boolean
  evaluated_pages: number[]
}

export interface QualityFieldMetric {
  key: string
  label: string
  evaluated: number
  matched: number
  exact: number
  missing: number
  mismatched: number
  extra: number
  score: number | null
  exact_score: number | null
}

export interface QualityOcrMetric {
  key: string
  label: string
  evaluated: number
  matched: number
  score: number | null
}

export interface QualityDetectionMetric {
  kind: string
  predicted_count: number
  gold_count: number
  matched_count: number
  precision: number
  recall: number
  f1: number
  mean_iou: number
  iou_threshold: number
}

export interface QualityRelationMetric {
  evaluated: number
  matched: number
  score: number | null
}

export interface QualityEvaluationRun {
  id: string
  job_id: string
  document_id: string
  dataset_id: string
  dataset_version: string
  matching_version_id: string
  template_name: string
  scoring_version: string
  semantic_model: string
  status: QualityEvaluationStatus
  progress: {
    current: number
    total: number
    percent: number
    stage: string
  }
  summary: QualityEvaluationSummary | null
  field_metrics: QualityFieldMetric[]
  ocr_metrics: QualityOcrMetric[]
  detection_metrics: QualityDetectionMetric[]
  relation_metrics: Record<string, QualityRelationMetric>
  unmatched: Record<string, string[]>
  warnings: string[]
  logs: Array<{ at: string; stage: string; message: string }>
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface GoldWorkbookPreview {
  filename: string
  sheet_name: string
  headers: string[]
  sample_rows: Array<Array<string | null>>
}

export interface QualityEvaluationHistoryItem {
  id: string
  job_id: string
  document_id: string
  status: QualityEvaluationStatus
  template_name: string
  matching_version_id: string
  summary: QualityEvaluationSummary | null
  created_at: string
}

export interface EvaluationJobOption {
  job_id: string
  document_id: string
  document_filename: string
  status: string
  created_at: string
  completed_at: string | null
  template_name: string
  matching_version_id: string
}

export interface CrossReportEvaluationItem extends QualityEvaluationHistoryItem {
  dataset_id: string
}

export interface QualityEvaluationItem {
  id: string
  evaluation_id: string
  job_id: string
  record_id: string
  crop_region_id: string | null
  color_plate_region_id: string | null
  artifact_id: string
  gold_record_id: string | null
  match_status: 'matched' | 'not_found' | 'ambiguous'
  source_pages: number[]
  field_results: Array<{
    key: string
    label: string
    verdict: string
    score: number | null
    actual: unknown
    expected: unknown
    reason?: string
    method?: string
  }>
}

export interface QualityEvaluationItemPage {
  items: QualityEvaluationItem[]
  total: number
  page: number
  page_size: number
}
