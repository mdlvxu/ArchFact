from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.api.dependencies import get_container
from app.application.views import build_job_view
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
    filename_contains: Annotated[str | None, Query(max_length=100)] = None,
    prefer_verified: Annotated[bool, Query()] = False,
    include_active: Annotated[bool, Query()] = False,
) -> ApiResponse[ExtractionJobView | None]:
    job = await container.repository.get_latest_completed_job(
        document_id=document_id,
        filename_contains=filename_contains,
        prefer_verified=prefer_verified,
        include_active=include_active,
    )
    return ApiResponse(data=await build_job_view(container, job) if job else None)


@router.get("/history", response_model=ApiResponse[list[dict]])
async def list_recent_completed_extraction_jobs(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[dict]]:
    jobs = await container.repository.list_recent_completed_jobs()
    return ApiResponse(
        data=[
            {
                "job_id": job["_id"],
                "document_id": job["document_id"],
                "document_filename": job.get("document_filename", ""),
                "status": job.get("status", "unknown"),
                "created_at": job.get("created_at"),
                "completed_at": job.get("completed_at"),
                "template_name": (job.get("config") or {}).get("template_name", ""),
                "matching_version_id": job.get("active_matching_version_id", "M0"),
            }
            for job in jobs
        ]
    )


@router.get("", response_model=ApiResponse[list[dict]])
async def list_document_extraction_jobs(
    document_id: Annotated[str, Query(min_length=1, max_length=120)],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[dict]]:
    jobs = await container.repository.list_jobs_for_document(document_id)
    return ApiResponse(
        data=[
            {
                "job_id": job["_id"],
                "document_id": job["document_id"],
                "status": job.get("status", "unknown"),
                "created_at": job.get("created_at"),
                "completed_at": job.get("completed_at"),
                "template_name": (job.get("config") or {}).get("template_name", ""),
                "matching_version_id": job.get("active_matching_version_id", "M0"),
            }
            for job in jobs
        ]
    )


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
