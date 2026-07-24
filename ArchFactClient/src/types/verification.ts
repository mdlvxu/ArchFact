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
  stale?: boolean
  reviewed_at: string | null
  ai_verdict?: 'passed' | 'failed' | 'uncertain' | null
  ai_confidence?: number | null
  ai_reason?: string
  ai_field_results?: Array<Record<string, unknown>>
  gold_record_id?: string | null
  gold_match_status?: 'matched' | 'not_found' | 'ambiguous' | 'unavailable' | null
  consensus_status?: 'pending' | 'agreed' | 'conflict' | 'human_resolved' | 'benchmark_unavailable'
  conflict_resolved?: boolean
}

export interface VerificationSession {
  id: string
  job_id: string
  cohort_id: string
  target_version: number
  status: 'in_progress' | 'ai_review' | 'conflict_review' | 'completed'
  rules: VerificationRule[]
  items: VerificationItem[]
  reviewed_count: number
  sample_count: number
  version_id: string | null
  ai_run_id?: string | null
  gold_dataset_id?: string | null
  matching_version_id?: string
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
}

export interface VerificationVersionSnapshot {
  id: string
  job_id: string
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
  created_at: string
}

export interface AiVerificationRun {
  id: string
  job_id: string
  session_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
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
  totalArtifacts: number
  passed: number
  errors: number
  stale: number
  relationChanged: number
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
