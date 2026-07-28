from typing import Annotated

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
        record_type=record["record_type"],
        source_pages=record["source_pages"],
        fields=record["fields"],
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
        created_at=record["created_at"],
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


async def build_job_view(container: Container, job: dict) -> ExtractionJobView:
    events = await container.repository.list_events(
        job["_id"],
        container.settings.job_event_limit,
    )
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
    await container.repository.get_job(job_id)
    record = await container.repository.get_record(job_id, record_id)

    # Older extraction results may contain only the first OCR line of a wrapped
    # figure/plate reference. Complete it in the response as well as during new
    # fusion runs, so an existing job becomes correct as soon as it is reopened.
    source_pages = {
        page
        for page in record.get("source_pages", [])
        if isinstance(page, int)
    }
    for field_key in ("figure_caption", "artifact_id"):
        field = record.get("fields", {}).get(field_key, {})
        if not isinstance(field, dict):
            continue
        source_pages.update(
            evidence["page"]
            for evidence in field.get("evidence", [])
            if isinstance(evidence, dict) and isinstance(evidence.get("page"), int)
        )
    persisted_source_regions = []
    for page_no in sorted(source_pages):
        persisted_source_regions.extend(
            await container.repository.list_page_regions(job_id, page_no)
        )
    source_regions = [
        {
            **region,
            "id": str(region.get("id") or region["_id"]),
        }
        for region in persisted_source_regions
    ]
    if source_regions:
        ResultFusionService.complete_record_text_evidence(
            records=[record],
            regions=source_regions,
            region_by_id={str(region["id"]): region for region in source_regions},
        )
        ResultFusionService.complete_multiline_figure_caption_evidence(
            records=[record],
            regions=source_regions,
            region_by_id={str(region["id"]): region for region in source_regions},
        )

    region_ids = set(record.get("region_ids", []))
    relation_ids = set(record.get("relation_ids", []))
    entity = None
    if record.get("entity_id"):
        entity = await container.repository.get_entity(job_id, record["entity_id"])
        if entity is not None:
            region_ids.update(entity.get("region_ids", []))
            relation_ids.update(entity.get("relation_ids", []))
    for field in record.get("fields", {}).values():
        for evidence in field.get("evidence", []):
            if evidence.get("region_id"):
                region_ids.add(evidence["region_id"])
            region_ids.update(evidence.get("linked_region_ids", []))
            relation_ids.update(evidence.get("relation_ids", []))

    relations = await container.repository.list_relations_by_ids(
        job_id,
        sorted(relation_ids),
    )
    for relation in relations:
        region_ids.add(relation["source_region_id"])
        region_ids.add(relation["target_region_id"])
    regions = await container.repository.list_regions_by_ids(job_id, sorted(region_ids))
    page_numbers = sorted(
        set(record.get("source_pages", []))
        | set(entity.get("associated_pages", []) if entity else [])
        | {int(region["page"]) for region in regions}
    )
    return ApiResponse(
        data=RecordEvidenceContextView(
            record=build_record_view(record),
            entity=build_entity_view(entity) if entity else None,
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
