from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_container
from app.container import Container
from app.core.errors import ConflictError
from app.models.schemas import (
    AiVerificationRunView,
    ApiResponse,
    ArtifactEntityView,
    ExtractionFieldReviewResult,
    ExtractionFieldReviewUpdate,
    ExtractionJobCreate,
    ExtractionJobCreated,
    ExtractionJobView,
    ExtractionRecordPage,
    ExtractionRecordReviewUpdate,
    ExtractionRecordView,
    JobEventView,
    ModelRunView,
    PageAnnotationView,
    RecordEvidenceContextView,
    RecordRevisionView,
    RegionRelationRebindRequest,
    RegionRelationReviewUpdate,
    RegionRelationView,
    RelationRevisionView,
    RematchChangesView,
    RematchCreate,
    RematchCreated,
    RematchReportView,
    RematchRunView,
    SourceRegionView,
    VerificationCompleteResult,
    VerificationItemUpdate,
    VerificationSessionCreate,
    VerificationSessionView,
    VerificationVersionView,
)
from app.services.result_fusion import ResultFusionService

router = APIRouter(prefix="/extraction-jobs", tags=["extraction-jobs"])

_TEXT_PAGE_FIELD_WEIGHTS = {
    "artifact_id": 3,
    "category": 4,
    "material": 6,
    "surface_color": 6,
    "texture": 6,
    "surface_treatment": 6,
    "measurements": 10,
    "morphological_description": 12,
    "figure_caption": 1,
}


def _has_field_value(field: object) -> bool:
    if not isinstance(field, dict):
        return False
    value = field.get("value", field.get("raw_value"))
    return value not in (None, "", [], {})


def _candidate_evidence_pages(records: list[dict]) -> set[int]:
    pages: set[int] = set()
    for record in records:
        for page in record.get("source_pages", []):
            if isinstance(page, int):
                pages.add(page)
        for field in record.get("fields", {}).values():
            if not isinstance(field, dict):
                continue
            for evidence in field.get("evidence", []):
                if isinstance(evidence, dict) and isinstance(evidence.get("page"), int):
                    pages.add(int(evidence["page"]))
        for evidence in record.get("text_evidence", []):
            if isinstance(evidence, dict) and isinstance(evidence.get("page"), int):
                pages.add(int(evidence["page"]))
    return pages


def _select_primary_text_evidence(
    records: list[dict],
    selected_record: dict,
    *,
    color_plate_pages: set[int] | None = None,
) -> tuple[dict, int]:
    """Pick the entity page with the richest descriptive text evidence.

    Color-plate pages may carry short captions, but the left preview column must
    always prefer a non-color page when one exists. Every artifact is expected to
    have non-color text evidence; color plates are optional third-column support.
    """

    color_plate_pages = color_plate_pages or set()
    candidates: list[tuple[int, int, bool, int, dict]] = []
    selected_id = str(selected_record["_id"])

    for record in records:
        page_scores: dict[int, int] = {}
        page_field_counts: dict[int, int] = {}
        for field_key, field in record.get("fields", {}).items():
            if not _has_field_value(field):
                continue
            weight = _TEXT_PAGE_FIELD_WEIGHTS.get(field_key, 2)
            evidence_pages = {
                int(evidence["page"])
                for evidence in field.get("evidence", [])
                if isinstance(evidence, dict)
                and isinstance(evidence.get("page"), int)
                and evidence.get("kind", "text") == "text"
            }
            for page in evidence_pages:
                page_scores[page] = page_scores.get(page, 0) + weight
                page_field_counts[page] = page_field_counts.get(page, 0) + 1

        for page in record.get("source_pages", []):
            if isinstance(page, int):
                page_scores.setdefault(page, 0)
                page_field_counts.setdefault(page, 0)

        for page, score in page_scores.items():
            candidates.append(
                (
                    score,
                    page_field_counts[page],
                    str(record["_id"]) == selected_id,
                    -page,
                    record,
                )
            )

    if not candidates:
        fallback_page = next(
            (
                int(page)
                for page in selected_record.get("source_pages", [])
                if isinstance(page, int)
            ),
            1,
        )
        return selected_record, fallback_page

    non_color_candidates = [
        item for item in candidates if (-item[3]) not in color_plate_pages
    ]
    ranked = non_color_candidates or candidates
    score, field_count, selected, negative_page, record = max(
        ranked,
        key=lambda item: item[:4],
    )
    del score, field_count, selected
    return record, -negative_page


async def _resolve_color_plate_pages(
    container: Container,
    *,
    job_id: str,
    document_id: str | None,
    candidate_pages: set[int],
) -> set[int]:
    """Identify color-plate pages among evidence candidates."""

    color_pages: set[int] = set()
    if document_id:
        for page in await container.repository.list_document_pages(document_id):
            page_no = page.get("page_no")
            if (
                isinstance(page_no, int)
                and page_no in candidate_pages
                and page.get("page_type") == "color_plate"
            ):
                color_pages.add(page_no)

    for page_no in sorted(candidate_pages):
        if page_no in color_pages:
            continue
        regions = await container.repository.list_page_regions(job_id, page_no)
        if any(region.get("kind") == "color_plate" for region in regions):
            color_pages.add(page_no)
    return color_pages


def _record_evidence_region_ids(record: dict) -> set[str]:
    region_ids: set[str] = set()
    for field in record.get("fields", {}).values():
        if not isinstance(field, dict):
            continue
        for evidence in field.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            if evidence.get("region_id"):
                region_ids.add(str(evidence["region_id"]))
    for evidence in record.get("text_evidence", []):
        if isinstance(evidence, dict) and evidence.get("region_id"):
            region_ids.add(str(evidence["region_id"]))
    return region_ids


def _record_core_visual_region_ids(record: dict) -> set[str]:
    return {
        str(region_id)
        for region_id in (
            record.get("primary_number_region_id"),
            record.get("primary_artifact_region_id"),
            record.get("thumbnail_region_id"),
        )
        if region_id
    }


def _record_explicit_relation_ids(record: dict) -> set[str]:
    relation_ids: set[str] = set()
    if record.get("primary_relation_id"):
        relation_ids.add(str(record["primary_relation_id"]))
    for field in record.get("fields", {}).values():
        if not isinstance(field, dict):
            continue
        for evidence in field.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            relation_ids.update(
                str(relation_id)
                for relation_id in evidence.get("relation_ids", [])
                if relation_id
            )
    return relation_ids


def _select_relevant_evidence_relations(
    relations: list[dict],
    *,
    evidence_region_ids: set[str],
    core_visual_region_ids: set[str],
    explicit_relation_ids: set[str],
) -> list[dict]:
    """Keep the local evidence graph without traversing the whole entity graph."""
    structural_relation_types = {
        "caption_to_number",
        "number_of",
        "caption_of",
        "drawing_of",
        "color_plate_of",
        "plate_reference_to_color",
        "image_of",
    }
    selected: list[dict] = []
    for relation in relations:
        relation_id = str(relation["_id"])
        source_id = str(relation["source_region_id"])
        target_id = str(relation["target_region_id"])
        relation_type = relation.get("relation_type")
        touches_core = (
            source_id in core_visual_region_ids
            or target_id in core_visual_region_ids
        )
        is_local_self_evidence = (
            relation_type == "evidence_for"
            and source_id == target_id
            and source_id in evidence_region_ids
        )
        if (
            relation_id in explicit_relation_ids
            or is_local_self_evidence
            or (
                touches_core
                and relation_type in structural_relation_types | {"evidence_for"}
            )
        ):
            selected.append(relation)
    return selected


_CARD_ENRICHMENT_FIELD_KEYS = (
    "category",
    "texture",
    "surface_color",
    "morphological_description",
    "measurements",
    "figure_caption",
)


def _field_snapshot(fields: dict, key: str) -> tuple[str, str]:
    field = fields.get(key) if isinstance(fields, dict) else None
    if not isinstance(field, dict):
        return ("", "")
    return (
        str(field.get("value") or "").strip(),
        str(field.get("raw_value") or "").strip(),
    )


def _record_card_fields_changed(before: dict, after: dict) -> bool:
    before_fields = before.get("fields", {}) if isinstance(before.get("fields"), dict) else {}
    after_fields = after.get("fields", {}) if isinstance(after.get("fields"), dict) else {}
    for key in _CARD_ENRICHMENT_FIELD_KEYS:
        if _field_snapshot(before_fields, key) != _field_snapshot(after_fields, key):
            return True
    before_quotes = [
        str(item.get("quote") or "")
        for item in before.get("text_evidence", [])
        if isinstance(item, dict)
    ]
    after_quotes = [
        str(item.get("quote") or "")
        for item in after.get("text_evidence", [])
        if isinstance(item, dict)
    ]
    return before_quotes != after_quotes


async def enrich_records_with_paragraph_fields(
    container: Container,
    job_id: str,
    records: list[dict],
    *,
    persist: bool = True,
) -> int:
    """Fill/upgrade sparse card fields from OCR paragraphs.

    Older jobs often store a truncated morphological_description (e.g. ``片状``)
    while the full prose still exists in page OCR. Re-run deterministic paragraph
    completion on read, and persist upgrades once per fusion version so export /
    rematch baselines see the same card data as the UI.
    """

    if not records:
        return 0
    source_pages = sorted(
        {
            page
            for record in records
            for page in record.get("source_pages", [])
            if isinstance(page, int)
        }
    )
    if not source_pages:
        return 0

    persisted_regions: list[dict] = []
    for page_no in source_pages:
        persisted_regions.extend(
            await container.repository.list_page_regions(job_id, page_no)
        )
    if not persisted_regions:
        return 0

    regions = [
        {
            **region,
            "id": str(region.get("id") or region["_id"]),
        }
        for region in persisted_regions
    ]
    region_by_id = {str(region["id"]): region for region in regions}
    before_by_id = {
        str(record["_id"]): {
            "fields": {
                key: dict(field)
                for key, field in (record.get("fields") or {}).items()
                if isinstance(field, dict)
            },
            "text_evidence": list(record.get("text_evidence") or []),
        }
        for record in records
        if record.get("_id")
    }

    ResultFusionService.complete_record_text_evidence(
        records=records,
        regions=regions,
        region_by_id=region_by_id,
    )
    ResultFusionService.complete_multiline_figure_caption_evidence(
        records=records,
        regions=regions,
        region_by_id=region_by_id,
    )
    ResultFusionService.prune_cross_artifact_field_evidence(
        records=records,
        region_by_id=region_by_id,
    )

    if not persist:
        return 0

    persisted = 0
    enrichment_version = str(ResultFusionService.version)
    for record in records:
        record_id = str(record.get("_id") or "")
        if not record_id:
            continue
        before = before_by_id.get(record_id)
        if before is None:
            continue
        changed = _record_card_fields_changed(before, record)
        already_enriched = record.get("paragraph_enrichment_version") == enrichment_version
        if not changed and already_enriched:
            continue
        if not changed:
            continue

        region_ids: list[str] = []
        seen_region_ids: set[str] = set()
        for region_id in record.get("region_ids", []) or []:
            value = str(region_id)
            if value and value not in seen_region_ids:
                seen_region_ids.add(value)
                region_ids.append(value)
        await container.repository.patch_record_paragraph_enrichment(
            job_id=job_id,
            record_id=record_id,
            fields=record.get("fields") or {},
            text_evidence=list(record.get("text_evidence") or []),
            region_ids=region_ids,
            enrichment_version=enrichment_version,
        )
        record["paragraph_enrichment_version"] = enrichment_version
        persisted += 1
    return persisted


def _coerce_record_timestamp(record: dict[str, Any], *keys: str) -> datetime:
    """Older restored/rematch payloads may omit created_at; keep list APIs readable."""

    for key in keys:
        value = record.get(key)
        if isinstance(value, datetime):
            return value
    return datetime.now(timezone.utc)


def build_record_view(record: dict, *, compact: bool = False) -> ExtractionRecordView:
    linkage = record.get("linkage", {})
    if compact:
        visual_link = dict(linkage.get("visual_link", {}))
        visual_link["evidence"] = []
        visual_link["evidence_block_ids"] = []
        linkage = {
            "identity": linkage.get("identity", {}),
            "visual_link": visual_link,
        }
    return ExtractionRecordView(
        id=record["_id"],
        job_id=record["job_id"],
        record_type=record.get("record_type", "artifact"),
        source_pages=record.get("source_pages", []),
        fields=record.get("fields", {}),
        text_evidence=[] if compact else record.get("text_evidence", []),
        linkage=linkage,
        link_hints=record.get("link_hints", {}),
        warnings=record.get("warnings", []),
        review_status=record.get("review_status", "unreviewed"),
        reviewed_at=record.get("reviewed_at"),
        model_run_ids=[] if compact else record.get("model_run_ids", []),
        region_ids=[] if compact else record.get("region_ids", []),
        relation_ids=[] if compact else record.get("relation_ids", []),
        associated_pages=record.get("associated_pages", []),
        thumbnail_region_id=record.get("thumbnail_region_id"),
        primary_number_region_id=record.get("primary_number_region_id"),
        primary_artifact_region_id=record.get("primary_artifact_region_id"),
        primary_relation_id=record.get("primary_relation_id"),
        primary_link_score=record.get("primary_link_score"),
        fusion_status=record.get("fusion_status", "unlinked"),
        entity_id=record.get("entity_id"),
        entity_confidence=record.get("entity_confidence"),
        entity_match_status=record.get("entity_match_status", "unlinked"),
        created_at=_coerce_record_timestamp(record, "created_at", "updated_at"),
    )


def build_entity_view(entity: dict) -> ArtifactEntityView:
    return ArtifactEntityView(
        id=entity["_id"],
        job_id=entity["job_id"],
        document_id=entity["document_id"],
        canonical_artifact_id=entity.get("canonical_artifact_id"),
        aliases=entity.get("aliases", []),
        figure_refs=entity.get("figure_refs", []),
        plate_refs=entity.get("plate_refs", []),
        match_keys=entity.get("match_keys", []),
        record_ids=entity.get("record_ids", []),
        region_ids=entity.get("region_ids", []),
        relation_ids=entity.get("relation_ids", []),
        source_pages=entity.get("source_pages", []),
        associated_pages=entity.get("associated_pages", []),
        thumbnail_region_id=entity.get("thumbnail_region_id"),
        confidence=entity.get("confidence", 0),
        link_status=entity.get("link_status", "unlinked"),
        link_reasons=entity.get("link_reasons", []),
        version=entity.get("version", "1"),
    )


def build_region_view(region: dict) -> SourceRegionView:
    return SourceRegionView(
        id=region["_id"],
        job_id=region["job_id"],
        document_id=region["document_id"],
        page=region["page"],
        kind=region["kind"],
        bbox=region["bbox"],
        bbox_px=region.get("bbox_px"),
        text=region.get("text", ""),
        confidence=region.get("confidence"),
        source=region.get("source", "unknown"),
        model_run_id=region.get("model_run_id"),
        image_id=region.get("image_id"),
        crop_object_key=region.get("crop_object_key"),
        crop_width=region.get("crop_width"),
        crop_height=region.get("crop_height"),
        crop_content_type=region.get("crop_content_type"),
        crop_error=region.get("crop_error"),
        ocr_raw_text=region.get("ocr_raw_text"),
        ocr_confidence=region.get("ocr_confidence"),
        ocr_source=region.get("ocr_source"),
        ocr_model=region.get("ocr_model"),
        ocr_version=region.get("ocr_version"),
        ocr_model_run_id=region.get("ocr_model_run_id"),
        ocr_error=region.get("ocr_error"),
        approximate=bool(region.get("approximate", False)),
        geometry_type=region.get("geometry_type"),
        match_key=region.get("match_key"),
        match_reason=region.get("match_reason"),
    )


def build_relation_view(relation: dict) -> RegionRelationView:
    return RegionRelationView(
        id=relation["_id"],
        job_id=relation["job_id"],
        source_region_id=relation["source_region_id"],
        target_region_id=relation["target_region_id"],
        relation_type=relation.get("relation_type", "related_to"),
        score=relation.get("score"),
        method=relation.get("method", "unknown"),
        version=relation.get("version", "1"),
        model_run_id=relation.get("model_run_id"),
        review_status=relation.get("review_status", "unreviewed"),
        reviewed_at=relation.get("reviewed_at"),
        reviewer=relation.get("reviewer"),
        review_reason=relation.get("review_reason", ""),
        supersedes_relation_id=relation.get("supersedes_relation_id"),
        superseded_by_relation_id=relation.get("superseded_by_relation_id"),
    )


def build_relation_revision_view(revision: dict) -> RelationRevisionView:
    return RelationRevisionView(
        id=revision["_id"],
        job_id=revision["job_id"],
        relation_id=revision["relation_id"],
        action=revision["action"],
        before=revision.get("before"),
        after=revision.get("after"),
        reason=revision.get("reason", ""),
        reviewer=revision.get("reviewer"),
        created_at=revision["created_at"],
    )


def build_revision_view(revision: dict) -> RecordRevisionView:
    return RecordRevisionView(
        id=revision["_id"],
        job_id=revision["job_id"],
        record_id=revision["record_id"],
        field_key=revision["field_key"],
        decision=revision["decision"],
        before=revision.get("before"),
        after=revision.get("after"),
        reason=revision.get("reason", ""),
        reviewer=revision.get("reviewer"),
        created_at=revision["created_at"],
    )


def build_verification_session_view(session: dict) -> VerificationSessionView:
    return VerificationSessionView(
        id=session["_id"],
        job_id=session["job_id"],
        cohort_id=session["cohort_id"],
        target_version=session["target_version"],
        status=session["status"],
        rules=session.get("rules", []),
        items=session.get("items", []),
        reviewed_count=session.get("reviewed_count", 0),
        sample_count=session.get("sample_count", len(session.get("items", []))),
        version_id=session.get("version_id"),
        ai_run_id=session.get("ai_run_id"),
        gold_dataset_id=session.get("gold_dataset_id"),
        matching_version_id=session.get("matching_version_id", "M0"),
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        completed_at=session.get("completed_at"),
    )


def build_verification_version_view(version: dict) -> VerificationVersionView:
    return VerificationVersionView(
        id=version["_id"],
        job_id=version["job_id"],
        cohort_id=version["cohort_id"],
        version=version["version"],
        parent_version_id=version.get("parent_version_id"),
        matching_version_id=version.get("matching_version_id", "M0"),
        rules=version.get("rules", []),
        items=version.get("items", []),
        report=version["report"],
        ai_run_id=version.get("ai_run_id"),
        gold_dataset_id=version.get("gold_dataset_id"),
        gold_dataset_version=version.get("gold_dataset_version"),
        created_at=version["created_at"],
    )


def build_ai_verification_run_view(run: dict) -> AiVerificationRunView:
    return AiVerificationRunView(
        id=run["_id"],
        job_id=run["job_id"],
        session_id=run["session_id"],
        status=run["status"],
        progress=run.get("progress", {}),
        gold_dataset_id=run.get("gold_dataset_id"),
        benchmark_available=run.get("benchmark_available", False),
        conflict_count=run.get("conflict_count", 0),
        uncertain_count=run.get("uncertain_count", 0),
        version_id=run.get("version_id"),
        error=run.get("error"),
        created_at=run["created_at"],
        updated_at=run["updated_at"],
        completed_at=run.get("completed_at"),
    )


_TERMINAL_JOB_STATUSES = {
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
}


async def build_job_view(container: Container, job: dict) -> ExtractionJobView:
    events = await container.repository.list_events(
        job["_id"],
        container.settings.job_event_limit,
    )
    completed_at = job.get("completed_at")
    # Legacy jobs finished before completed_at existed: prefer the last extraction
    # event over updated_at (which rematch/apply can refresh days later).
    if completed_at is None and job.get("status") in _TERMINAL_JOB_STATUSES:
        completed_at = events[-1]["created_at"] if events else job.get("updated_at")
    return ExtractionJobView(
        id=job["_id"],
        document_id=job["document_id"],
        pipeline_id=job.get("pipeline_id", "default"),
        status=job["status"],
        stage=job["stage"],
        progress=job["progress"],
        cancel_requested=job.get("cancel_requested", False),
        error=job.get("error"),
        page_issues=job.get("page_issues", []),
        succeeded_pages=job.get("succeeded_pages", 0),
        failed_pages=job.get("failed_pages", 0),
        requested_pages=job.get("requested_pages", job.get("pages") or []),
        discovered_pages=job.get("discovered_pages", []),
        effective_pages=job.get("effective_pages", job.get("pages") or []),
        active_matching_version_id=job.get("active_matching_version_id", "M0"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        attempt_started_at=job.get("attempt_started_at"),
        completed_at=completed_at,
        retry_pages=job.get("retry_pages", []),
        events=[
            JobEventView(
                id=event["_id"],
                level=event["level"],
                message=event["message"],
                created_at=event["created_at"],
            )
            for event in events
        ],
    )


def build_rematch_view(run: dict) -> RematchRunView:
    return RematchRunView(
        id=run["_id"],
        job_id=run["job_id"],
        base_matching_version_id=run.get("base_matching_version_id", "M0"),
        status=run["status"],
        preserve_reviewed=run.get("preserve_reviewed", True),
        apply_immediately=run.get("apply_immediately", False),
        cancel_requested=run.get("cancel_requested", False),
        progress=run.get("progress", {}),
        report=run.get("report"),
        error=run.get("error"),
        created_at=run["created_at"],
        updated_at=run["updated_at"],
        completed_at=run.get("completed_at"),
        applied_at=run.get("applied_at"),
    )


@router.post(
    "",
    response_model=ApiResponse[ExtractionJobCreated],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_extraction_job(
    payload: ExtractionJobCreate,
    container: Annotated[Container, Depends(get_container)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ExtractionJobCreated]:
    job = await container.extraction_service.create_job(payload, idempotency_key)
    return ApiResponse(
        message="抽取任务已创建",
        data=ExtractionJobCreated(job_id=job["_id"], status=job["status"]),
    )


@router.get(
    "/recent/latest",
    response_model=ApiResponse[ExtractionJobView | None],
)
async def get_latest_completed_extraction_job(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionJobView | None]:
    job = await container.repository.get_latest_completed_job()
    return ApiResponse(data=await build_job_view(container, job) if job else None)


@router.get("/{job_id}", response_model=ApiResponse[ExtractionJobView])
async def get_extraction_job(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionJobView]:
    job = await container.repository.get_job(job_id)
    return ApiResponse(data=await build_job_view(container, job))


@router.post("/{job_id}/cancel", response_model=ApiResponse[ExtractionJobView])
async def cancel_extraction_job(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionJobView]:
    job = await container.extraction_service.cancel_job(job_id)
    return ApiResponse(message="任务已停止", data=await build_job_view(container, job))


@router.post(
    "/{job_id}/retry-failed-pages",
    response_model=ApiResponse[ExtractionJobView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_failed_extraction_pages(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionJobView]:
    job = await container.extraction_service.retry_failed_pages(job_id)
    return ApiResponse(
        message=f"已重新提交 {len(job.get('retry_pages', []))} 个失败页面",
        data=await build_job_view(container, job),
    )


@router.post(
    "/{job_id}/rematches",
    response_model=ApiResponse[RematchCreated],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_rematch(
    job_id: str,
    payload: RematchCreate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RematchCreated]:
    run = await container.rematch_service.create(job_id, payload)
    return ApiResponse(
        message="重新匹配预览任务已创建",
        data=RematchCreated(rematch_id=run["_id"], status=run["status"]),
    )


@router.get(
    "/{job_id}/rematches/{rematch_id}",
    response_model=ApiResponse[RematchRunView],
)
async def get_rematch(
    job_id: str,
    rematch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RematchRunView]:
    run = await container.repository.get_rematch_run(job_id, rematch_id)
    return ApiResponse(data=build_rematch_view(run))


@router.get(
    "/{job_id}/rematches/{rematch_id}/report",
    response_model=ApiResponse[RematchReportView],
)
async def get_rematch_report(
    job_id: str,
    rematch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RematchReportView]:
    run = await container.repository.get_rematch_run(job_id, rematch_id)
    if run.get("report") is None:
        raise ConflictError("重新匹配报告尚未生成")
    return ApiResponse(data=RematchReportView.model_validate(run["report"]))


@router.get(
    "/{job_id}/rematches/{rematch_id}/changes",
    response_model=ApiResponse[RematchChangesView],
)
async def get_rematch_changes(
    job_id: str,
    rematch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RematchChangesView]:
    changes = await container.repository.get_rematch_relation_changes(
        job_id=job_id,
        rematch_id=rematch_id,
    )
    return ApiResponse(data=RematchChangesView(total=len(changes), items=changes))


@router.post(
    "/{job_id}/rematches/{rematch_id}/apply",
    response_model=ApiResponse[RematchRunView],
)
async def apply_rematch(
    job_id: str,
    rematch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RematchRunView]:
    run = await container.rematch_service.apply(job_id, rematch_id)
    return ApiResponse(message="新的匹配版本已应用", data=build_rematch_view(run))


@router.post(
    "/{job_id}/rematches/{rematch_id}/cancel",
    response_model=ApiResponse[RematchRunView],
)
async def cancel_rematch(
    job_id: str,
    rematch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RematchRunView]:
    run = await container.rematch_service.cancel(job_id, rematch_id)
    return ApiResponse(message="重新匹配任务已停止", data=build_rematch_view(run))


@router.get("/{job_id}/records", response_model=ApiResponse[ExtractionRecordPage])
async def list_extraction_records(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
    compact: bool = False,
) -> ApiResponse[ExtractionRecordPage]:
    await container.repository.get_job(job_id)
    records, total = await container.repository.list_records(
        job_id,
        page=page,
        page_size=page_size,
    )
    await enrich_records_with_paragraph_fields(container, job_id, records)
    return ApiResponse(
        data=ExtractionRecordPage(
            items=[build_record_view(record, compact=compact) for record in records],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.patch(
    "/{job_id}/records/{record_id}/review",
    response_model=ApiResponse[ExtractionRecordView],
)
async def update_extraction_record_review(
    job_id: str,
    record_id: str,
    payload: ExtractionRecordReviewUpdate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionRecordView]:
    await container.repository.get_job(job_id)
    record = await container.repository.update_record_review(job_id, record_id, payload.status)
    return ApiResponse(message="Review status updated", data=build_record_view(record))


@router.get(
    "/{job_id}/records/{record_id}/evidence-context",
    response_model=ApiResponse[RecordEvidenceContextView],
)
async def get_record_evidence_context(
    job_id: str,
    record_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RecordEvidenceContextView]:
    job = await container.repository.get_job(job_id)
    record = await container.repository.get_record(job_id, record_id)
    entity = None
    entity_records = [record]
    if record.get("entity_id"):
        entity = await container.repository.get_entity(job_id, record["entity_id"])
        if entity is not None:
            siblings = await container.repository.list_records_by_ids(
                job_id,
                entity.get("record_ids", []),
            )
            sibling_by_id = {str(sibling["_id"]): sibling for sibling in siblings}
            sibling_by_id[str(record["_id"])] = record
            entity_records = list(sibling_by_id.values())

    # Also treat entity-associated pages as candidates so linked color plates are
    # excluded even when the selected record itself is non-color.
    candidate_pages = _candidate_evidence_pages(entity_records)
    if entity is not None:
        for page in entity.get("associated_pages", []):
            if isinstance(page, int):
                candidate_pages.add(page)

    text_record, primary_text_page = _select_primary_text_evidence(
        entity_records,
        record,
        color_plate_pages=await _resolve_color_plate_pages(
            container,
            job_id=job_id,
            document_id=str(job.get("document_id") or "") or None,
            candidate_pages=candidate_pages,
        ),
    )

    # Older extraction results may contain only the first OCR line of a wrapped
    # figure/plate reference, or a truncated morphological_description. Complete
    # and persist those card fields so reopen/export match the text evidence panel.
    context_records = list(
        {str(item["_id"]): item for item in (record, text_record)}.values()
    )
    await enrich_records_with_paragraph_fields(
        container,
        job_id,
        context_records,
        persist=True,
    )
    # Keep local aliases in sync when record/text_record are distinct objects.
    refreshed = {str(item["_id"]): item for item in context_records}
    if str(record.get("_id")) in refreshed:
        record = refreshed[str(record["_id"])]
    if str(text_record.get("_id")) in refreshed:
        text_record = refreshed[str(text_record["_id"])]
    context_records = list(refreshed.values())
    evidence_region_ids = set().union(
        *(_record_evidence_region_ids(candidate) for candidate in context_records)
    )
    core_visual_region_ids = set().union(
        *(_record_core_visual_region_ids(candidate) for candidate in context_records)
    )
    explicit_relation_ids = set().union(
        *(_record_explicit_relation_ids(candidate) for candidate in context_records)
    )
    candidate_relation_ids = set(record.get("relation_ids", []))
    candidate_relation_ids.update(text_record.get("relation_ids", []))
    if entity is not None:
        candidate_relation_ids.update(entity.get("relation_ids", []))

    candidate_relations = await container.repository.list_relations_by_ids(
        job_id,
        sorted(candidate_relation_ids),
    )
    relations = _select_relevant_evidence_relations(
        candidate_relations,
        evidence_region_ids=evidence_region_ids,
        core_visual_region_ids=core_visual_region_ids,
        explicit_relation_ids=explicit_relation_ids,
    )

    region_ids = evidence_region_ids | core_visual_region_ids
    for candidate in (record, text_record):
        for field in candidate.get("fields", {}).values():
            for evidence in field.get("evidence", []):
                if evidence.get("region_id"):
                    region_ids.add(evidence["region_id"])
    for relation in relations:
        region_ids.add(relation["source_region_id"])
        region_ids.add(relation["target_region_id"])
    regions = await container.repository.list_regions_by_ids(job_id, sorted(region_ids))
    page_numbers = sorted(
        set(record.get("source_pages", []))
        | set(text_record.get("source_pages", []))
        | {int(region["page"]) for region in regions}
    )
    relevant_region_ids = sorted(region_ids)
    relevant_relation_ids = [str(relation["_id"]) for relation in relations]
    context_record = {
        **record,
        "region_ids": relevant_region_ids,
        "relation_ids": relevant_relation_ids,
        "associated_pages": page_numbers,
    }
    context_text_record = {
        **text_record,
        "region_ids": relevant_region_ids,
        "relation_ids": relevant_relation_ids,
        "associated_pages": page_numbers,
    }
    context_entity = (
        {
            **entity,
            "region_ids": relevant_region_ids,
            "relation_ids": relevant_relation_ids,
            "associated_pages": page_numbers,
        }
        if entity
        else None
    )
    return ApiResponse(
        data=RecordEvidenceContextView(
            record=build_record_view(context_record),
            text_record=build_record_view(context_text_record),
            primary_text_page=primary_text_page,
            entity=build_entity_view(context_entity) if context_entity else None,
            page_numbers=page_numbers,
            regions=[build_region_view(region) for region in regions],
            relations=[build_relation_view(relation) for relation in relations],
        )
    )


@router.get(
    "/{job_id}/pages/{page_no}/annotations",
    response_model=ApiResponse[PageAnnotationView],
)
async def get_page_annotations(
    job_id: str,
    page_no: int,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[PageAnnotationView]:
    await container.repository.get_job(job_id)
    regions = await container.repository.list_page_regions(job_id, page_no)
    relations = await container.repository.list_page_relations(job_id, page_no)
    records = await container.repository.list_page_records(job_id, page_no)
    return ApiResponse(
        data=PageAnnotationView(
            page=page_no,
            regions=[build_region_view(region) for region in regions],
            relations=[build_relation_view(relation) for relation in relations],
            records=[build_record_view(record) for record in records],
        )
    )


@router.get("/{job_id}/regions/{region_id}/crop", response_class=FileResponse)
async def get_region_crop(
    job_id: str,
    region_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> FileResponse:
    await container.repository.get_job(job_id)
    return FileResponse(
        await container.image_service.get_region_crop_path(job_id, region_id),
        media_type="image/png",
        filename=f"{region_id}.png",
    )


@router.patch(
    "/{job_id}/relations/{relation_id}/review",
    response_model=ApiResponse[RegionRelationView],
)
async def update_region_relation_review(
    job_id: str,
    relation_id: str,
    payload: RegionRelationReviewUpdate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RegionRelationView]:
    await container.repository.get_job(job_id)
    relation = await container.repository.update_relation_review(
        job_id=job_id,
        relation_id=relation_id,
        status=payload.status,
        reason=payload.reason,
        reviewer=payload.reviewer,
    )
    return ApiResponse(message="Relation review saved", data=build_relation_view(relation))


@router.post(
    "/{job_id}/relations/{relation_id}/rebind",
    response_model=ApiResponse[RegionRelationView],
)
async def rebind_region_relation(
    job_id: str,
    relation_id: str,
    payload: RegionRelationRebindRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[RegionRelationView]:
    await container.repository.get_job(job_id)
    relation = await container.repository.rebind_relation(
        job_id=job_id,
        relation_id=relation_id,
        source_region_id=payload.source_region_id,
        target_region_id=payload.target_region_id,
        relation_type=payload.relation_type,
        reason=payload.reason,
        reviewer=payload.reviewer,
    )
    return ApiResponse(message="Relation rebound", data=build_relation_view(relation))


@router.get(
    "/{job_id}/relations/{relation_id}/revisions",
    response_model=ApiResponse[list[RelationRevisionView]],
)
async def list_relation_revisions(
    job_id: str,
    relation_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[RelationRevisionView]]:
    await container.repository.get_job(job_id)
    revisions = await container.repository.list_relation_revisions(
        job_id=job_id,
        relation_id=relation_id,
    )
    return ApiResponse(data=[build_relation_revision_view(revision) for revision in revisions])


@router.get("/{job_id}/model-runs", response_model=ApiResponse[list[ModelRunView]])
async def list_model_runs(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[ModelRunView]]:
    await container.repository.get_job(job_id)
    runs = await container.repository.list_model_runs(job_id)
    return ApiResponse(
        data=[
            ModelRunView(
                id=run["_id"],
                job_id=run["job_id"],
                stage=run["stage"],
                provider=run["provider"],
                model=run["model"],
                version=run["version"],
                status=run["status"],
                started_at=run["started_at"],
                completed_at=run.get("completed_at"),
                error=run.get("error"),
            )
            for run in runs
        ]
    )


@router.patch(
    "/{job_id}/records/{record_id}/fields/{field_key}/review",
    response_model=ApiResponse[ExtractionFieldReviewResult],
)
async def update_extraction_field_review(
    job_id: str,
    record_id: str,
    field_key: str,
    payload: ExtractionFieldReviewUpdate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionFieldReviewResult]:
    await container.repository.get_job(job_id)
    record, revision = await container.repository.update_field_review(
        job_id=job_id,
        record_id=record_id,
        field_key=field_key,
        decision=payload.decision,
        value=payload.value,
        reason=payload.reason,
        reviewer=payload.reviewer,
    )
    return ApiResponse(
        message="Field review saved",
        data=ExtractionFieldReviewResult(
            record=build_record_view(record),
            revision=build_revision_view(revision),
        ),
    )


@router.get(
    "/{job_id}/records/{record_id}/revisions",
    response_model=ApiResponse[list[RecordRevisionView]],
)
async def list_record_revisions(
    job_id: str,
    record_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[RecordRevisionView]]:
    await container.repository.get_job(job_id)
    revisions = await container.repository.list_record_revisions(
        job_id=job_id,
        record_id=record_id,
    )
    return ApiResponse(data=[build_revision_view(revision) for revision in revisions])


@router.post(
    "/{job_id}/verification-sessions",
    response_model=ApiResponse[VerificationSessionView],
    status_code=status.HTTP_201_CREATED,
)
async def create_verification_session(
    job_id: str,
    payload: VerificationSessionCreate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationSessionView]:
    session = await container.repository.create_verification_session(
        job_id=job_id,
        rules=[rule.model_dump() for rule in payload.rules],
        sample_size=payload.sample_size,
    )
    return ApiResponse(
        message=f"V{session['target_version']} 校验会话已创建",
        data=build_verification_session_view(session),
    )


@router.get(
    "/{job_id}/verification-sessions/{session_id}",
    response_model=ApiResponse[VerificationSessionView],
)
async def get_verification_session(
    job_id: str,
    session_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationSessionView]:
    session = await container.repository.get_verification_session(
        job_id=job_id,
        session_id=session_id,
    )
    return ApiResponse(data=build_verification_session_view(session))


@router.get(
    "/{job_id}/verification-sessions/{session_id}/records",
    response_model=ApiResponse[list[ExtractionRecordView]],
)
async def list_verification_session_records(
    job_id: str,
    session_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[ExtractionRecordView]]:
    records = await container.repository.list_verification_session_records(
        job_id=job_id,
        session_id=session_id,
    )
    return ApiResponse(data=[build_record_view(record) for record in records])


@router.patch(
    "/{job_id}/verification-sessions/{session_id}/records/{record_id}",
    response_model=ApiResponse[VerificationSessionView],
)
async def update_verification_session_item(
    job_id: str,
    session_id: str,
    record_id: str,
    payload: VerificationItemUpdate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationSessionView]:
    session = await container.repository.update_verification_item(
        job_id=job_id,
        session_id=session_id,
        record_id=record_id,
        verdict=payload.verdict,
        failure_code=payload.failure_code,
        failure_reason=payload.failure_reason,
    )
    return ApiResponse(message="样本核验结果已保存", data=build_verification_session_view(session))


@router.post(
    "/{job_id}/verification-sessions/{session_id}/complete",
    response_model=ApiResponse[VerificationCompleteResult],
)
async def complete_verification_session(
    job_id: str,
    session_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationCompleteResult]:
    session, version, ai_run = await container.verification_service.complete_or_start(
        job_id=job_id,
        session_id=session_id,
    )
    message = f"V{version['version']} 已生成" if version else "AI 复核任务已启动"
    return ApiResponse(
        message=message,
        data=VerificationCompleteResult(
            session=build_verification_session_view(session),
            version=build_verification_version_view(version) if version else None,
            ai_run=build_ai_verification_run_view(ai_run) if ai_run else None,
        ),
    )


@router.get(
    "/{job_id}/ai-verification-runs/{run_id}",
    response_model=ApiResponse[AiVerificationRunView],
)
async def get_ai_verification_run(
    job_id: str,
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[AiVerificationRunView]:
    run = await container.repository.get_ai_verification_run(job_id=job_id, run_id=run_id)
    return ApiResponse(data=build_ai_verification_run_view(run))


@router.get(
    "/{job_id}/verification-versions",
    response_model=ApiResponse[list[VerificationVersionView]],
)
async def list_verification_versions(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[VerificationVersionView]]:
    await container.repository.get_job(job_id)
    versions = await container.repository.list_verification_versions(job_id)
    return ApiResponse(data=[build_verification_version_view(version) for version in versions])
