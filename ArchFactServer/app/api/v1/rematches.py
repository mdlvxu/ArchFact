from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_container
from app.application.views import build_rematch_view
from app.container import Container
from app.core.errors import ConflictError
from app.models.schemas import (
    ApiResponse,
    RematchChangesView,
    RematchCreate,
    RematchCreated,
    RematchReportView,
    RematchRunView,
)

router = APIRouter(prefix="/extraction-jobs", tags=["rematches"])


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
