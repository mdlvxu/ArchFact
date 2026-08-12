from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: T


class ExtractionFieldSpec(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=100)
    type: Literal["string", "number", "date", "boolean", "image", "object", "array"]
    required: bool = False
    instruction: str | None = Field(default=None, max_length=500)
    evidence_kind: (
        Literal[
            "text",
            "line_drawing",
            "color_plate",
            "artifact",
            "caption",
            "number",
            "group",
            "grave_drawing",
            "other",
        ]
        | None
    ) = None


class PostProcessingRuleSpec(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    example: str = Field(default="", max_length=300)
    handler: Literal["builtin", "instruction"] = "builtin"


class ExtractionTemplateDefinition(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    fields: list[ExtractionFieldSpec] = Field(min_length=1, max_length=50)
    builtin: bool = False

    @field_validator("fields")
    @classmethod
    def ensure_unique_field_keys(
        cls, fields: list[ExtractionFieldSpec]
    ) -> list[ExtractionFieldSpec]:
        keys = [field.key for field in fields]
        if len(keys) != len(set(keys)):
            raise ValueError("模板字段 key 不能重复")
        return fields


class PostProcessingRuleDefinition(PostProcessingRuleSpec):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    enabled: bool = True
    builtin: bool = False


class ExtractionConfig(BaseModel):
    schema_version: str = "1.0"
    template_id: str = Field(min_length=1, max_length=100)
    template_name: str = Field(min_length=1, max_length=100)
    fields: list[ExtractionFieldSpec] = Field(min_length=1, max_length=50)
    post_processing_rules: list[PostProcessingRuleSpec] = Field(default_factory=list, max_length=20)

    @field_validator("fields")
    @classmethod
    def ensure_unique_field_keys(
        cls, fields: list[ExtractionFieldSpec]
    ) -> list[ExtractionFieldSpec]:
        keys = [field.key for field in fields]
        if len(keys) != len(set(keys)):
            raise ValueError("字段 key 不能重复")
        return fields


class DocumentCreated(BaseModel):
    document_id: str
    filename: str
    size: int
    status: str


class DocumentView(DocumentCreated):
    page_count: int | None = None
    sha256: str
    created_at: datetime
    error: str | None = None


class LocalStorageReference(BaseModel):
    type: Literal["local"] = "local"
    object_key: str


class DocumentImageView(BaseModel):
    image_id: str
    document_id: str
    page_no: int
    image_type: Literal["page_render", "embedded"]
    content_type: str
    width: int
    height: int
    size: int
    sha256: str
    storage: LocalStorageReference
    created_at: datetime


class ExtractionJobCreate(BaseModel):
    document_id: str
    pages: list[int] | None = Field(default=None, max_length=1000)
    pipeline_id: str = Field(default="default", min_length=1, max_length=100)
    config: ExtractionConfig

    @field_validator("pages")
    @classmethod
    def validate_pages(cls, pages: list[int] | None) -> list[int] | None:
        if pages is None:
            return None
        if not pages or any(page < 1 for page in pages):
            raise ValueError("pages 必须包含大于 0 的页码")
        return sorted(set(pages))


class ExtractionJobCreated(BaseModel):
    job_id: str
    status: str


class JobProgress(BaseModel):
    current: int = 0
    total: int = 0
    percent: int = 0


class JobEventView(BaseModel):
    id: str
    level: Literal["INFO", "SUCCESS", "WARNING", "ERROR"]
    message: str
    created_at: datetime


class PageIssueView(BaseModel):
    page: int
    stage: str
    severity: Literal["warning", "error"]
    message: str


class ExtractionJobView(BaseModel):
    id: str
    document_id: str
    pipeline_id: str = "default"
    status: Literal[
        "queued",
        "preparing",
        "parsing",
        "extracting",
        "matching",
        "merging",
        "post_processing",
        "completed",
        "completed_with_warnings",
        "failed",
        "cancelling",
        "cancelled",
    ]
    stage: str
    progress: JobProgress
    cancel_requested: bool = False
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    attempt_started_at: datetime | None = None
    # Extraction finish time. Distinct from updated_at, which rematch/apply may bump later.
    completed_at: datetime | None = None
    events: list[JobEventView] = Field(default_factory=list)
    page_issues: list[PageIssueView] = Field(default_factory=list)
    succeeded_pages: int = 0
    failed_pages: int = 0
    requested_pages: list[int] = Field(default_factory=list)
    discovered_pages: list[int] = Field(default_factory=list)
    effective_pages: list[int] = Field(default_factory=list)
    retry_pages: list[int] = Field(default_factory=list)
    active_matching_version_id: str = "M0"


class EvidenceView(BaseModel):
    page: int
    quote: str = ""
    # Normalized PDF coordinates: [left, top, right, bottom], each from 0 to 1.
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    region_id: str | None = None
    kind: Literal[
        "text",
        "line_drawing",
        "color_plate",
        "artifact",
        "caption",
        "number",
        "group",
        "grave_drawing",
        "other",
    ] = "text"
    relation_ids: list[str] = Field(default_factory=list)
    linked_region_ids: list[str] = Field(default_factory=list)
    image_id: str | None = None
    crop_object_key: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str = "unknown"


class SourceRegionView(BaseModel):
    id: str
    job_id: str
    document_id: str
    page: int
    kind: Literal[
        "text",
        "line_drawing",
        "color_plate",
        "artifact",
        "caption",
        "number",
        "group",
        "grave_drawing",
        "other",
    ]
    bbox: list[float] = Field(min_length=4, max_length=4)
    bbox_px: list[float] | None = Field(default=None, min_length=4, max_length=4)
    text: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str
    model_run_id: str | None = None
    image_id: str | None = None
    crop_object_key: str | None = None
    crop_width: int | None = None
    crop_height: int | None = None
    crop_content_type: str | None = None
    crop_error: str | None = None
    ocr_raw_text: str | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_source: str | None = None
    ocr_model: str | None = None
    ocr_version: str | None = None
    ocr_model_run_id: str | None = None
    ocr_error: str | None = None
    approximate: bool = False
    geometry_type: str | None = None
    match_key: str | None = None
    match_reason: str | None = None


class RegionRelationView(BaseModel):
    id: str
    job_id: str
    source_region_id: str
    target_region_id: str
    relation_type: str
    score: float | None = Field(default=None, ge=0, le=1)
    method: str
    version: str = "1"
    model_run_id: str | None = None
    review_status: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    reviewed_at: datetime | None = None
    reviewer: str | None = None
    review_reason: str = ""
    supersedes_relation_id: str | None = None
    superseded_by_relation_id: str | None = None


class RegionRelationReviewUpdate(BaseModel):
    status: Literal["unreviewed", "accepted", "rejected"]
    reason: str = Field(default="", max_length=500)
    reviewer: str | None = Field(default=None, max_length=100)


class RegionRelationRebindRequest(BaseModel):
    source_region_id: str = Field(min_length=1, max_length=100)
    target_region_id: str = Field(min_length=1, max_length=100)
    relation_type: str | None = Field(default=None, min_length=1, max_length=100)
    reason: str = Field(default="", max_length=500)
    reviewer: str | None = Field(default=None, max_length=100)

    @field_validator("target_region_id")
    @classmethod
    def ensure_distinct_regions(cls, target_region_id: str, info: Any) -> str:
        if info.data.get("source_region_id") == target_region_id:
            raise ValueError("source_region_id and target_region_id must be different")
        return target_region_id


class RelationRevisionView(BaseModel):
    id: str
    job_id: str
    relation_id: str
    action: Literal["review", "rebind"]
    before: Any = None
    after: Any = None
    reason: str = ""
    reviewer: str | None = None
    created_at: datetime


class ModelRunView(BaseModel):
    id: str
    job_id: str
    stage: str
    provider: str
    model: str
    version: str
    status: Literal["running", "completed", "failed", "cancelled"]
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class FieldConflictCandidateView(BaseModel):
    raw_value: Any = None
    value: Any = None
    evidence: list[EvidenceView] = Field(default_factory=list)
    selected: bool = False


class ExtractedFieldView(BaseModel):
    raw_value: Any = None
    value: Any = None
    status: Literal["valid", "missing", "needs_review"] = "valid"
    evidence: list[EvidenceView] = Field(default_factory=list)
    conflict_candidates: list[FieldConflictCandidateView] = Field(default_factory=list)


class RecordIdentityView(BaseModel):
    artifact_id_raw: str | None = None
    artifact_id_normalized: str | None = None


class VisualLinkView(BaseModel):
    figure_no: str | None = None
    figure_item_no: str | None = None
    plate_no: str | None = None
    plate_item_no: str | None = None
    caption_raw: str | None = None
    evidence_block_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)


class SystemLinkageView(BaseModel):
    identity: RecordIdentityView = Field(default_factory=RecordIdentityView)
    visual_link: VisualLinkView = Field(default_factory=VisualLinkView)


class ArtifactEntityView(BaseModel):
    id: str
    job_id: str
    document_id: str
    canonical_artifact_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    figure_refs: list[str] = Field(default_factory=list)
    plate_refs: list[str] = Field(default_factory=list)
    match_keys: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    region_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    associated_pages: list[int] = Field(default_factory=list)
    thumbnail_region_id: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    link_status: Literal["linked", "needs_review", "unlinked"] = "unlinked"
    link_reasons: list[str] = Field(default_factory=list)
    version: str = "1"


class ExtractionRecordView(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    job_id: str
    record_type: str
    source_pages: list[int]
    fields: dict[str, ExtractedFieldView]
    text_evidence: list[EvidenceView] = Field(default_factory=list)
    linkage: SystemLinkageView = Field(default_factory=SystemLinkageView)
    link_hints: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    review_status: Literal["unreviewed", "passed", "failed"] = "unreviewed"
    reviewed_at: datetime | None = None
    model_run_ids: list[str] = Field(default_factory=list)
    region_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    associated_pages: list[int] = Field(default_factory=list)
    thumbnail_region_id: str | None = None
    primary_number_region_id: str | None = None
    primary_artifact_region_id: str | None = None
    primary_relation_id: str | None = None
    primary_link_score: float | None = Field(default=None, ge=0, le=1)
    fusion_status: Literal["unlinked", "partial", "linked"] = "unlinked"
    entity_id: str | None = None
    entity_confidence: float | None = Field(default=None, ge=0, le=1)
    entity_match_status: Literal["linked", "needs_review", "unlinked"] = "unlinked"
    created_at: datetime


class ExtractionRecordReviewUpdate(BaseModel):
    status: Literal["unreviewed", "passed", "failed"]


class ExtractionRecordPage(BaseModel):
    items: list[ExtractionRecordView]
    total: int
    page: int
    page_size: int


class PageAnnotationView(BaseModel):
    page: int
    regions: list[SourceRegionView]
    relations: list[RegionRelationView]
    records: list[ExtractionRecordView]


class RecordEvidenceContextView(BaseModel):
    record: ExtractionRecordView
    text_record: ExtractionRecordView
    primary_text_page: int
    entity: ArtifactEntityView | None = None
    page_numbers: list[int]
    regions: list[SourceRegionView]
    relations: list[RegionRelationView]


class ExtractionFieldReviewUpdate(BaseModel):
    decision: Literal["accepted", "rejected", "corrected"]
    value: Any = None
    reason: str = Field(default="", max_length=500)
    reviewer: str | None = Field(default=None, max_length=100)


class RecordRevisionView(BaseModel):
    id: str
    job_id: str
    record_id: str
    field_key: str
    decision: Literal["accepted", "rejected", "corrected"]
    before: Any = None
    after: Any = None
    reason: str = ""
    reviewer: str | None = None
    created_at: datetime


class ExtractionFieldReviewResult(BaseModel):
    record: ExtractionRecordView
    revision: RecordRevisionView


class GoldDatasetImportRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=120)
    version: str = Field(default="1.0", min_length=1, max_length=40)
    replace: bool = False


class GoldDatasetView(BaseModel):
    id: str
    name: str
    document_id: str
    version: str
    status: Literal["ready", "importing", "failed"] = "ready"
    source_type: str = "human_annotation"
    record_count: int = 0
    region_count: int = 0
    asset_count: int = 0
    link_count: int = 0
    matched_artifact_assets: int = 0
    matched_color_plate_assets: int = 0
    source_document_verified: bool = False
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class QualityEvaluationCreate(BaseModel):
    gold_dataset_id: str | None = Field(default=None, min_length=1, max_length=120)


class QualityEvaluationProgress(BaseModel):
    current: int = 0
    total: int = 5
    percent: int = Field(default=0, ge=0, le=100)
    stage: str = "queued"


class QualityEvaluationRunView(BaseModel):
    id: str
    job_id: str
    document_id: str
    dataset_id: str
    dataset_version: str = ""
    matching_version_id: str = "M0"
    status: Literal["queued", "running", "completed", "failed"]
    progress: QualityEvaluationProgress = Field(default_factory=QualityEvaluationProgress)
    summary: dict[str, Any] | None = None
    field_metrics: list[dict[str, Any]] = Field(default_factory=list)
    ocr_metrics: list[dict[str, Any]] = Field(default_factory=list)
    detection_metrics: list[dict[str, Any]] = Field(default_factory=list)
    relation_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    unmatched: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class QualityEvaluationItemView(BaseModel):
    id: str
    evaluation_id: str
    job_id: str
    record_id: str
    artifact_id: str = ""
    gold_record_id: str | None = None
    match_status: Literal["matched", "not_found", "ambiguous"]
    source_pages: list[int] = Field(default_factory=list)
    field_results: list[dict[str, Any]] = Field(default_factory=list)
    ocr_results: list[dict[str, Any]] = Field(default_factory=list)
    relation_results: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class QualityEvaluationItemPage(BaseModel):
    items: list[QualityEvaluationItemView]
    total: int
    page: int
    page_size: int


class VerificationRuleSnapshot(BaseModel):
    id: int | str
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True


class VerificationSessionCreate(BaseModel):
    rules: list[VerificationRuleSnapshot] = Field(min_length=1, max_length=100)
    sample_size: int = Field(default=18, ge=1, le=100)

    @field_validator("rules")
    @classmethod
    def require_enabled_rule(
        cls,
        rules: list[VerificationRuleSnapshot],
    ) -> list[VerificationRuleSnapshot]:
        if not any(rule.enabled for rule in rules):
            raise ValueError("至少启用一条校验规则")
        return rules


class VerificationItemUpdate(BaseModel):
    verdict: Literal["passed", "failed"]
    failure_code: (
        Literal[
            "field_error",
            "text_evidence_error",
            "caption_match_error",
            "number_match_error",
            "artifact_crop_error",
            "color_plate_error",
            "other",
        ]
        | None
    ) = None
    failure_reason: str = Field(default="", max_length=500)


class VerificationItemView(BaseModel):
    record_id: str
    verdict: Literal["unreviewed", "passed", "failed", "stale"] = "unreviewed"
    failure_code: str | None = None
    failure_reason: str = ""
    relation_signature: str = ""
    relation_changed: bool = False
    sampling_strata: list[str] = Field(default_factory=list)
    stale: bool = False
    reviewed_at: datetime | None = None
    ai_verdict: Literal["passed", "failed", "uncertain"] | None = None
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    ai_reason: str = ""
    ai_field_results: list[dict[str, Any]] = Field(default_factory=list)
    gold_record_id: str | None = None
    gold_match_status: Literal["matched", "not_found", "ambiguous", "unavailable"] | None = None
    consensus_status: Literal[
        "pending",
        "agreed",
        "conflict",
        "human_resolved",
        "benchmark_unavailable",
    ] = "pending"
    conflict_resolved: bool = False


class VerificationCohortView(BaseModel):
    id: str
    job_id: str
    sample_size: int
    random_seed: int
    record_ids: list[str]
    sampling_strategy: str = "random_v0"
    eligible_count: int = 0
    strata_by_record: dict[str, dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime


class VerificationReportView(BaseModel):
    sample_count: int
    reviewed_count: int
    pass_count: int
    fail_count: int
    stale_count: int = 0
    relation_changed_count: int = 0
    pass_rate: float
    total_artifacts: int
    ai_pass_count: int = 0
    ai_fail_count: int = 0
    ai_uncertain_count: int = 0
    conflict_count: int = 0
    benchmark_matched_count: int = 0


class VerificationSessionView(BaseModel):
    id: str
    job_id: str
    cohort_id: str
    target_version: int
    status: Literal["in_progress", "ai_review", "conflict_review", "completed"]
    rules: list[VerificationRuleSnapshot]
    items: list[VerificationItemView]
    reviewed_count: int
    sample_count: int
    version_id: str | None = None
    ai_run_id: str | None = None
    gold_dataset_id: str | None = None
    matching_version_id: str = "M0"
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class VerificationVersionView(BaseModel):
    id: str
    job_id: str
    cohort_id: str
    version: int
    parent_version_id: str | None = None
    matching_version_id: str = "M0"
    rules: list[VerificationRuleSnapshot]
    items: list[VerificationItemView]
    report: VerificationReportView
    ai_run_id: str | None = None
    gold_dataset_id: str | None = None
    gold_dataset_version: str | None = None
    created_at: datetime


class AiVerificationProgress(BaseModel):
    current: int = 0
    total: int = 0
    percent: int = 0


class AiVerificationRunView(BaseModel):
    id: str
    job_id: str
    session_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: AiVerificationProgress = Field(default_factory=AiVerificationProgress)
    gold_dataset_id: str | None = None
    benchmark_available: bool = False
    conflict_count: int = 0
    uncertain_count: int = 0
    version_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class VerificationCompleteResult(BaseModel):
    session: VerificationSessionView
    version: VerificationVersionView | None = None
    ai_run: AiVerificationRunView | None = None


class RematchCreate(BaseModel):
    preserve_reviewed: bool = True
    apply_immediately: bool = False


class RematchCreated(BaseModel):
    rematch_id: str
    status: str


class RematchProgress(BaseModel):
    current: int = 0
    total: int = 0
    percent: int = 0
    stage: str = "waiting"


class RematchConfidenceBuckets(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0


class RematchDeltaView(BaseModel):
    added: int = 0
    removed: int = 0
    changed: int = 0
    unchanged: int = 0


class RematchProtectionView(BaseModel):
    accepted_relations: int = 0
    rejected_relations: int = 0
    passed_records: int = 0
    protected_relations: int = 0


class RematchReportView(BaseModel):
    total_records: int = 0
    linked_records: int = 0
    partial_records: int = 0
    unlinked_records: int = 0
    complete_chains: int = 0
    ocr_exact_relations: int = 0
    layout_fallback_relations: int = 0
    conflict_relations: int = 0
    confidence: RematchConfidenceBuckets = Field(default_factory=RematchConfidenceBuckets)
    delta: RematchDeltaView = Field(default_factory=RematchDeltaView)
    protection: RematchProtectionView = Field(default_factory=RematchProtectionView)


class RematchRelationChangeView(BaseModel):
    change: Literal["added", "removed", "changed"]
    relation_id: str
    relation_type: str = ""
    source_region_id: str = ""
    target_region_id: str = ""
    before_method: str | None = None
    after_method: str | None = None
    before_score: float | None = None
    after_score: float | None = None
    protected: bool = False


class RematchChangesView(BaseModel):
    total: int = 0
    items: list[RematchRelationChangeView] = Field(default_factory=list)


class RematchRunView(BaseModel):
    id: str
    job_id: str
    base_matching_version_id: str = "M0"
    status: Literal[
        "queued",
        "running",
        "completed",
        "applying",
        "applied",
        "failed",
        "cancelling",
        "cancelled",
    ]
    preserve_reviewed: bool = True
    apply_immediately: bool = False
    cancel_requested: bool = False
    progress: RematchProgress = Field(default_factory=RematchProgress)
    report: RematchReportView | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    applied_at: datetime | None = None
