import { get, patch, post, put } from '@/api/http'
import { constraintTypeMap, fieldTypeMap } from '@/domain/extraction-config'
import type {
  ExtractionConfigPayload,
  ExtractionFieldType,
  ExtractionJob,
  ExtractionRecord,
  ExtractionTemplate,
  PostProcessingRule,
  ModelRun,
  PageAnnotations,
  RecordRevision,
  RecordEvidenceContext,
  RematchChanges,
  RematchRun,
  RegionRelation,
  RelationRevision,
  SourceRegionKind,
} from '@/types/extraction'
import type {
  AiVerificationRun,
  VerificationCompleteResult,
  VerificationFailureCode,
  VerificationRule,
  VerificationSession,
  VerificationVersionSnapshot,
} from '@/types/verification'
import type { GoldDataset, QualityEvaluationRun } from '@/types/quality-evaluation'

export interface UploadedDocument {
  document_id: string
  filename: string
  size: number
  status: string
  page_count?: number | null
}

interface CreatedExtractionJob {
  job_id: string
  status: string
}

interface ExtractionRecordPage {
  items: ExtractionRecord[]
  total: number
  page: number
  page_size: number
}

interface BackendExtractionTemplate {
  id: string
  name: string
  fields: Array<{
    key: string
    label: string
    type: ExtractionFieldType
    required: boolean
    instruction?: string
    evidence_kind?: SourceRegionKind | null
  }>
  builtin: boolean
}

export interface DocumentImage {
  image_id: string
  document_id: string
  page_no: number
  image_type: 'page_render' | 'embedded'
  content_type: string
  width: number
  height: number
  size: number
  sha256: string
  storage: {
    type: 'local'
    object_key: string
  }
  created_at: string
}

function fromBackendTemplate(template: BackendExtractionTemplate): ExtractionTemplate {
  return {
    id: template.id,
    name: template.name,
    fields: template.fields.map((field) => ({
      ...field,
      type: constraintTypeMap[field.type],
    })),
    builtin: template.builtin,
    custom: !template.builtin,
  }
}

function toBackendTemplate(template: ExtractionTemplate): BackendExtractionTemplate {
  return {
    id: template.id,
    name: template.name,
    fields: template.fields.map((field) => ({
      ...field,
      type: fieldTypeMap[field.type],
    })),
    builtin: template.builtin ?? false,
  }
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
}

export function uploadPdfDocument(file: File): Promise<UploadedDocument> {
  const form = new FormData()
  form.append('file', file)
  return post<UploadedDocument>('/v1/documents', form, {
    timeout: 120_000,
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export async function getExtractionTemplates(): Promise<ExtractionTemplate[]> {
  const templates = await get<BackendExtractionTemplate[]>('/v1/extraction-templates')
  return templates.map(fromBackendTemplate)
}

export async function replaceExtractionTemplates(
  templates: ExtractionTemplate[],
): Promise<ExtractionTemplate[]> {
  const saved = await put<BackendExtractionTemplate[]>(
    '/v1/extraction-templates',
    templates.map(toBackendTemplate),
  )
  return saved.map(fromBackendTemplate)
}

export function getPostProcessingRules(): Promise<PostProcessingRule[]> {
  return get<PostProcessingRule[]>('/v1/post-processing-rules')
}

export function replacePostProcessingRules(
  rules: PostProcessingRule[],
): Promise<PostProcessingRule[]> {
  return put<PostProcessingRule[]>('/v1/post-processing-rules', rules)
}

export function renderDocumentPage(documentId: string, pageNo: number): Promise<DocumentImage> {
  return post<DocumentImage>(`/v1/documents/${documentId}/pages/${pageNo}/image`)
}

export function getDocumentImages(documentId: string): Promise<DocumentImage[]> {
  return get<DocumentImage[]>(`/v1/documents/${documentId}/images`)
}

export function getUploadedDocument(documentId: string): Promise<UploadedDocument> {
  return get<UploadedDocument>(`/v1/documents/${documentId}`)
}

export function getDocumentImageContentUrl(documentId: string, imageId: string) {
  return `/api/v1/documents/${documentId}/images/${imageId}/content`
}

export function createExtractionJob(
  documentId: string,
  config: ExtractionConfigPayload,
): Promise<CreatedExtractionJob> {
  return post<CreatedExtractionJob>(
    '/v1/extraction-jobs',
    {
      document_id: documentId,
      pages: config.pages,
      pipeline_id: config.pipelineId,
      config: {
        schema_version: config.schemaVersion,
        template_id: config.templateId,
        template_name: config.templateName,
        fields: config.fields,
        post_processing_rules: config.postProcessingRules,
      },
    },
    {
      headers: { 'Idempotency-Key': createIdempotencyKey() },
    },
  )
}

export function getExtractionJob(jobId: string): Promise<ExtractionJob> {
  return get<ExtractionJob>(`/v1/extraction-jobs/${jobId}`)
}

export function getLatestCompletedExtractionJob(): Promise<ExtractionJob | null> {
  return get<ExtractionJob | null>('/v1/extraction-jobs/recent/latest')
}

export function cancelExtractionJob(jobId: string): Promise<ExtractionJob> {
  return post<ExtractionJob>(`/v1/extraction-jobs/${jobId}/cancel`)
}

export function retryFailedExtractionPages(jobId: string): Promise<ExtractionJob> {
  return post<ExtractionJob>(`/v1/extraction-jobs/${jobId}/retry-failed-pages`)
}

export function createRematch(jobId: string): Promise<{ rematch_id: string; status: string }> {
  return post(`/v1/extraction-jobs/${jobId}/rematches`, {
    preserve_reviewed: true,
    apply_immediately: false,
  })
}

export function getRematch(jobId: string, rematchId: string): Promise<RematchRun> {
  return get<RematchRun>(`/v1/extraction-jobs/${jobId}/rematches/${rematchId}`)
}

export function getRematchChanges(jobId: string, rematchId: string): Promise<RematchChanges> {
  return get<RematchChanges>(`/v1/extraction-jobs/${jobId}/rematches/${rematchId}/changes`)
}

export function applyRematch(jobId: string, rematchId: string): Promise<RematchRun> {
  return post<RematchRun>(`/v1/extraction-jobs/${jobId}/rematches/${rematchId}/apply`)
}

export function cancelRematch(jobId: string, rematchId: string): Promise<RematchRun> {
  return post<RematchRun>(`/v1/extraction-jobs/${jobId}/rematches/${rematchId}/cancel`)
}

export async function getExtractionRecords(jobId: string): Promise<ExtractionRecord[]> {
  const records: ExtractionRecord[] = []
  let page = 1
  let total = 0

  do {
    const result = await get<ExtractionRecordPage>(
      `/v1/extraction-jobs/${jobId}/records?page=${page}&page_size=200&compact=true`,
    )
    records.push(...result.items)
    total = result.total
    page += 1
  } while (records.length < total)

  return records
}

export function updateExtractionRecordReview(
  jobId: string,
  recordId: string,
  status: ExtractionRecord['review_status'],
): Promise<ExtractionRecord> {
  return patch<ExtractionRecord>(`/v1/extraction-jobs/${jobId}/records/${recordId}/review`, {
    status,
  })
}

export function getPageAnnotations(jobId: string, pageNo: number): Promise<PageAnnotations> {
  return get<PageAnnotations>(`/v1/extraction-jobs/${jobId}/pages/${pageNo}/annotations`)
}

export function getRecordEvidenceContext(
  jobId: string,
  recordId: string,
): Promise<RecordEvidenceContext> {
  return get<RecordEvidenceContext>(
    `/v1/extraction-jobs/${jobId}/records/${recordId}/evidence-context`,
  )
}

export function getRegionCropContentUrl(jobId: string, regionId: string) {
  return `/api/v1/extraction-jobs/${jobId}/regions/${regionId}/crop`
}

export function updateRegionRelationReview(
  jobId: string,
  relationId: string,
  status: RegionRelation['review_status'],
  reason = '',
): Promise<RegionRelation> {
  return patch<RegionRelation>(`/v1/extraction-jobs/${jobId}/relations/${relationId}/review`, {
    status,
    reason,
  })
}

export function rebindRegionRelation(
  jobId: string,
  relationId: string,
  payload: {
    source_region_id: string
    target_region_id: string
    relation_type?: string
    reason?: string
  },
): Promise<RegionRelation> {
  return post<RegionRelation>(
    `/v1/extraction-jobs/${jobId}/relations/${relationId}/rebind`,
    payload,
  )
}

export function getRelationRevisions(
  jobId: string,
  relationId: string,
): Promise<RelationRevision[]> {
  return get<RelationRevision[]>(
    `/v1/extraction-jobs/${jobId}/relations/${relationId}/revisions`,
  )
}

export function getExtractionModelRuns(jobId: string): Promise<ModelRun[]> {
  return get<ModelRun[]>(`/v1/extraction-jobs/${jobId}/model-runs`)
}

export function updateExtractionFieldReview(
  jobId: string,
  recordId: string,
  fieldKey: string,
  payload: {
    decision: 'accepted' | 'rejected' | 'corrected'
    value?: unknown
    reason?: string
    reviewer?: string
  },
): Promise<{ record: ExtractionRecord; revision: RecordRevision }> {
  return patch(`/v1/extraction-jobs/${jobId}/records/${recordId}/fields/${fieldKey}/review`, payload)
}

export function getRecordRevisions(jobId: string, recordId: string): Promise<RecordRevision[]> {
  return get<RecordRevision[]>(`/v1/extraction-jobs/${jobId}/records/${recordId}/revisions`)
}

export function createVerificationSession(
  jobId: string,
  rules: VerificationRule[],
  sampleSize = 18,
): Promise<VerificationSession> {
  return post<VerificationSession>(`/v1/extraction-jobs/${jobId}/verification-sessions`, {
    rules: rules.map(({ id, title, description, enabled }) => ({
      id,
      title,
      description,
      enabled,
    })),
    sample_size: sampleSize,
  })
}

export function getVerificationSession(
  jobId: string,
  sessionId: string,
): Promise<VerificationSession> {
  return get<VerificationSession>(
    `/v1/extraction-jobs/${jobId}/verification-sessions/${sessionId}`,
  )
}

export function getVerificationSessionRecords(
  jobId: string,
  sessionId: string,
): Promise<ExtractionRecord[]> {
  return get<ExtractionRecord[]>(
    `/v1/extraction-jobs/${jobId}/verification-sessions/${sessionId}/records`,
  )
}

export function updateVerificationSessionItem(
  jobId: string,
  sessionId: string,
  recordId: string,
  verdict: 'passed' | 'failed',
  failureCode?: VerificationFailureCode,
  failureReason = '',
): Promise<VerificationSession> {
  return patch<VerificationSession>(
    `/v1/extraction-jobs/${jobId}/verification-sessions/${sessionId}/records/${recordId}`,
    {
      verdict,
      failure_code: failureCode,
      failure_reason: failureReason,
    },
  )
}

export function completeVerificationSession(
  jobId: string,
  sessionId: string,
): Promise<VerificationCompleteResult> {
  return post<VerificationCompleteResult>(
    `/v1/extraction-jobs/${jobId}/verification-sessions/${sessionId}/complete`,
  )
}

export function getAiVerificationRun(jobId: string, runId: string): Promise<AiVerificationRun> {
  return get<AiVerificationRun>(`/v1/extraction-jobs/${jobId}/ai-verification-runs/${runId}`)
}

export function getVerificationVersions(jobId: string): Promise<VerificationVersionSnapshot[]> {
  return get<VerificationVersionSnapshot[]>(
    `/v1/extraction-jobs/${jobId}/verification-versions`,
  )
}

export function createQualityEvaluation(
  jobId: string,
  goldDatasetId?: string,
): Promise<QualityEvaluationRun> {
  return post<QualityEvaluationRun>(
    `/v1/extraction-jobs/${jobId}/quality-evaluations`,
    { gold_dataset_id: goldDatasetId ?? null },
  )
}

export function getQualityEvaluation(
  jobId: string,
  evaluationId: string,
): Promise<QualityEvaluationRun> {
  return get<QualityEvaluationRun>(
    `/v1/extraction-jobs/${jobId}/quality-evaluations/${evaluationId}`,
  )
}

export function getQualityEvaluations(jobId: string): Promise<QualityEvaluationRun[]> {
  return get<QualityEvaluationRun[]>(`/v1/extraction-jobs/${jobId}/quality-evaluations`)
}

export function getGoldDatasets(): Promise<GoldDataset[]> {
  return get<GoldDataset[]>('/v1/gold-datasets')
}

export function importWenjiashanGoldDataset(
  documentId: string,
  replace = false,
): Promise<GoldDataset> {
  return post<GoldDataset>('/v1/gold-datasets/import/wenjiashan', {
    document_id: documentId,
    version: '1.0',
    replace,
  })
}
