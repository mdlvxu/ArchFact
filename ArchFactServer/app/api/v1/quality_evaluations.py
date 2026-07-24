from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_container
from app.container import Container
from app.models.schemas import (
    ApiResponse,
    QualityEvaluationCreate,
    QualityEvaluationItemPage,
    QualityEvaluationItemView,
    QualityEvaluationRunView,
)

router = APIRouter(
    prefix="/extraction-jobs/{job_id}/quality-evaluations",
    tags=["quality-evaluations"],
)


def build_run_view(run: dict) -> QualityEvaluationRunView:
    return QualityEvaluationRunView(
        id=run["_id"],
        job_id=run["job_id"],
        document_id=run["document_id"],
        dataset_id=run["dataset_id"],
        dataset_version=run.get("dataset_version", ""),
        matching_version_id=run.get("matching_version_id", "M0"),
        status=run["status"],
        progress=run.get("progress", {}),
        summary=run.get("summary"),
        field_metrics=run.get("field_metrics", []),
        ocr_metrics=run.get("ocr_metrics", []),
        detection_metrics=run.get("detection_metrics", []),
        relation_metrics=run.get("relation_metrics", {}),
        unmatched=run.get("unmatched", {}),
        warnings=run.get("warnings", []),
        error=run.get("error"),
        created_at=run["created_at"],
        updated_at=run["updated_at"],
        completed_at=run.get("completed_at"),
    )


def build_item_view(item: dict) -> QualityEvaluationItemView:
    return QualityEvaluationItemView(
        id=item["_id"],
        evaluation_id=item["evaluation_id"],
        job_id=item["job_id"],
        record_id=item["record_id"],
        artifact_id=item.get("artifact_id", ""),
        gold_record_id=item.get("gold_record_id"),
        match_status=item["match_status"],
        source_pages=item.get("source_pages", []),
        field_results=item.get("field_results", []),
        ocr_results=item.get("ocr_results", []),
        relation_results=item.get("relation_results", []),
        created_at=item["created_at"],
    )


@router.post(
    "",
    response_model=ApiResponse[QualityEvaluationRunView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_quality_evaluation(
    job_id: str,
    payload: QualityEvaluationCreate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[QualityEvaluationRunView]:
    run = await container.quality_evaluation_service.create(
        job_id=job_id,
        gold_dataset_id=payload.gold_dataset_id,
    )
    return ApiResponse(message="质量评测任务已进入队列", data=build_run_view(run))


@router.get("", response_model=ApiResponse[list[QualityEvaluationRunView]])
async def list_quality_evaluations(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[QualityEvaluationRunView]]:
    runs = await container.repository.list_quality_evaluation_runs(job_id)
    return ApiResponse(data=[build_run_view(run) for run in runs])


@router.get(
    "/{evaluation_id}",
    response_model=ApiResponse[QualityEvaluationRunView],
)
async def get_quality_evaluation(
    job_id: str,
    evaluation_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[QualityEvaluationRunView]:
    run = await container.repository.get_quality_evaluation_run(
        job_id=job_id,
        evaluation_id=evaluation_id,
    )
    return ApiResponse(data=build_run_view(run))


@router.get(
    "/{evaluation_id}/items",
    response_model=ApiResponse[QualityEvaluationItemPage],
)
async def list_quality_evaluation_items(
    job_id: str,
    evaluation_id: str,
    container: Annotated[Container, Depends(get_container)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    match_status: Annotated[
        Literal["matched", "not_found", "ambiguous"] | None,
        Query(),
    ] = None,
) -> ApiResponse[QualityEvaluationItemPage]:
    await container.repository.get_quality_evaluation_run(
        job_id=job_id,
        evaluation_id=evaluation_id,
    )
    items, total = await container.repository.list_quality_evaluation_items(
        job_id=job_id,
        evaluation_id=evaluation_id,
        page=page,
        page_size=page_size,
        match_status=match_status,
    )
    return ApiResponse(
        data=QualityEvaluationItemPage(
            items=[build_item_view(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )
    )
