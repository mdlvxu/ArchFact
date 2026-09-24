/** 可由用户维护的机器校验断言。 */
export interface VerificationRule {
  id: number
  title: string
  description: string
  enabled: boolean
  updated?: boolean
}

export type VerificationVerdict = 'unreviewed' | 'passed' | 'failed' | 'stale'

export type VerificationFailureCode =
  | 'artifact_id_missing'
  | 'artifact_id_duplicate'
  | 'artifact_id_evidence_conflict'
  | 'sequence_crop_conflict'
  | 'figure_caption_missing'
  | 'figure_caption_evidence_conflict'
  | 'color_plate_relation_conflict'
  | 'artifact_crop_missing'
  | 'text_evidence_conflict'
  | 'structured_measurements_error'
  | 'structured_classification_error'
  | 'structured_field_evidence_conflict'
  | 'unclassified_failure'
  | 'field_error'
  | 'text_evidence_error'
  | 'caption_match_error'
  | 'number_match_error'
  | 'artifact_crop_error'
  | 'color_plate_error'
  | 'other'

export interface VerificationItem {
  record_id: string
  verdict: VerificationVerdict
  failure_code?: VerificationFailureCode | null
  failure_reason: string
  relation_signature?: string
  relation_changed?: boolean
  sampling_strata?: string[]
  expected_label?: 'correct' | 'incorrect' | null
  stale?: boolean
  reviewed_at: string | null
  ai_verdict?: 'passed' | 'failed' | 'uncertain' | null
  ai_confidence?: number | null
  ai_reason?: string
  ai_field_results?: Array<Record<string, unknown>>
  machine_verdict?: 'passed' | 'failed' | 'uncertain' | null
  machine_confidence?: number | null
  machine_reason?: string
  calibrated_machine_verdict?: 'passed' | 'failed' | 'uncertain' | null
  calibrated_machine_confidence?: number | null
  calibrated_machine_reason?: string
  gold_record_id?: string | null
  gold_match_status?: 'matched' | 'not_found' | 'ambiguous' | 'unavailable' | null
  consensus_status?: 'pending' | 'agreed' | 'conflict' | 'human_resolved' | 'benchmark_unavailable' | 'machine_verified'
  conflict_resolved?: boolean
}

export interface VerificationSession {
  id: string
  job_id: string
  experiment_id?: string
  experiment_name?: string
  cohort_id: string
  target_version: number
  status: 'in_progress' | 'ai_review' | 'conflict_review' | 'completed'
  rules: VerificationRule[]
  items: VerificationItem[]
  reviewed_count: number
  sample_count: number
  version_id: string | null
  ai_run_id?: string | null
  machine_run_id?: string | null
  gold_dataset_id?: string | null
  matching_version_id?: string
  assertion_baseline_id?: 'v1' | 'v2'
  assertion_baseline_name?: string
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface VerificationVersionReport {
  sample_count: number
  reviewed_count: number
  pass_count: number
  fail_count: number
  stale_count?: number
  relation_changed_count?: number
  pass_rate: number
  total_artifacts: number
  ai_pass_count?: number
  ai_fail_count?: number
  ai_uncertain_count?: number
  conflict_count?: number
  benchmark_matched_count?: number
  full_pass_count?: number
  full_fail_count?: number
  full_uncertain_count?: number
  model_unavailable_count?: number
  model_unavailable_reason?: string | null
  error_coverage?: number | null
  error_precision?: number | null
  human_machine_alignment?: number | null
  review_load?: number | null
  false_positive_rate?: number | null
  false_negative_rate?: number | null
  true_positive_count?: number
  true_negative_count?: number
  false_positive_count?: number
  false_negative_count?: number
  review_required_count?: number
  field_error_distribution?: Array<{ key: string; count: number }>
}

export interface VerificationVersionSnapshot {
  id: string
  job_id: string
  experiment_id?: string
  experiment_name?: string
  cohort_id: string
  version: number
  parent_version_id: string | null
  matching_version_id?: string
  rules: VerificationRule[]
  items: VerificationItem[]
  report: VerificationVersionReport
  ai_run_id?: string | null
  gold_dataset_id?: string | null
  gold_dataset_version?: string | null
  calibration_profile?: Record<string, unknown> | null
  assertion_baseline_id?: 'v1' | 'v2'
  assertion_baseline_name?: string
  created_at: string
}

export interface AiVerificationRun {
  id: string
  job_id: string
  session_id: string
  status: 'queued' | 'running' | 'paused' | 'completed' | 'failed'
  progress: { current: number; total: number; percent: number }
  gold_dataset_id: string | null
  benchmark_available: boolean
  conflict_count: number
  uncertain_count: number
  version_id: string | null
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

/** 全量机器校验任务。首次完成后生成固定人工样本；后续仅复用该样本。 */
export interface MachineVerificationRun {
  id: string
  job_id: string
  experiment_id?: string
  experiment_name?: string
  mode: 'initial' | 'calibrated' | 'recheck'
  status: 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'terminated'
  progress: { current: number; total: number; percent: number }
  rules: VerificationRule[]
  assertion_baseline_id?: 'v1' | 'v2'
  assertion_baseline_name?: string
  sample_size: number
  total_artifacts: number
  pass_count: number
  fail_count: number
  uncertain_count: number
  model_unavailable_count?: number
  model_unavailable_reason?: string | null
  session_id: string | null
  version_id: string | null
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

/** A clean, isolated V1/V2 evaluation over the current extraction results. */
export interface VerificationExperiment {
  id: string
  job_id: string
  name: string
  sequence: number
  status: 'active' | 'archived' | 'legacy'
  matching_version_id: string
  artifact_count: number
  created_at: string
}

export interface VerificationCompleteResult {
  session: VerificationSession
  version: VerificationVersionSnapshot | null
  ai_run: AiVerificationRun | null
}

/** 单个错误字段及其错误数量。 */
export interface VerificationField {
  label: string
  count: number
}

/** 一次机器校验产生的汇总指标。 */
export interface VerificationReport {
  sampleCount: number
  errorCoverage: number
  precision: number
  alignment: number
  reviewLoad: number | null
  totalArtifacts: number
  passed: number
  errors: number
  uncertain: number
  stale: number
  relationChanged: number
  reviewRequired: number
  fields: VerificationField[]
}

/** 版本相较上一个版本的指标变化。 */
export interface VerificationImpact {
  alignmentBefore: number
  alignmentAfter: number
  errorsBefore: number
  errorsAfter: number
  passedBefore: number
  passedAfter: number
}

/** 每次执行校验保存的版本记录，版本号越大越新。 */
export interface VerificationVersion {
  version: number
  createdAt: string
  title: string
  summary: string
  matchingVersionId?: string
  staleCount: number
  relationChangedCount: number
  exportable: boolean
  before: string
  after: string
  impact: VerificationImpact
}
