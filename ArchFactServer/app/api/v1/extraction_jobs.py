from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.api.dependencies import get_container
from app.api.v1.extraction_records import get_record_evidence_context
from app.application.record_enrichment import (
    _record_card_fields_changed,
    enrich_records_with_paragraph_fields,
)
from app.application.views import build_job_view, build_record_view
from app.container import Container
from app.models.schemas import (
    ApiResponse,
    ExtractionJobCreate,
    ExtractionJobCreated,
    ExtractionJobView,
)

router = APIRouter(prefix="/extraction-jobs", tags=["extraction-jobs"])


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
    document_id: Annotated[str | None, Query()] = None,
    include_active: Annotated[bool, Query()] = False,
) -> ApiResponse[ExtractionJobView | None]:
    job = await container.repository.get_latest_completed_job(
        document_id=document_id,
        include_active=include_active,
    )
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


__all__ = [
    "build_record_view",
    "enrich_records_with_paragraph_fields",
    "get_record_evidence_context",
    "router",
    "_record_card_fields_changed",
]
