from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.api.dependencies import get_container
from app.application.evidence_query import load_record_evidence_context
from app.application.record_enrichment import enrich_records_with_paragraph_fields
from app.application.views import (
    build_record_view,
    build_region_view,
    build_relation_revision_view,
    build_relation_view,
    build_revision_view,
)
from app.container import Container
from app.models.schemas import (
    ApiResponse,
    ExtractionFieldReviewResult,
    ExtractionFieldReviewUpdate,
    ExtractionRecordPage,
    ExtractionRecordReviewUpdate,
    ExtractionRecordView,
    ModelRunView,
    PageAnnotationView,
    RecordEvidenceContextView,
    RecordRevisionView,
    RegionRelationRebindRequest,
    RegionRelationReviewUpdate,
    RegionRelationView,
    RelationRevisionView,
)

router = APIRouter(prefix="/extraction-jobs", tags=["extraction-records"])


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
    return ApiResponse(
        data=await load_record_evidence_context(container, job_id, record_id)
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
