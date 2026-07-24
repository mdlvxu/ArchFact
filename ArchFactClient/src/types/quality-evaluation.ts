export type QualityEvaluationStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface GoldDataset {
  id: string
  name: string
  document_id: string
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
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface QualityEvaluationSummary {
  predicted_records: number
  gold_records: number
  matched_records: number
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
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}
