from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_container
from app.application.views import (
    build_ai_verification_run_view,
    build_record_view,
    build_verification_session_view,
    build_verification_version_view,
)
from app.container import Container
from app.core.errors import NotFoundError
from app.models.schemas import (
    AiVerificationRunView,
    ApiResponse,
    ExtractionRecordView,
    VerificationCompleteResult,
    VerificationItemUpdate,
    VerificationSessionCreate,
    VerificationSessionView,
    VerificationVersionView,
)

router = APIRouter(prefix="/extraction-jobs", tags=["verification"])


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
        message=f"V{session['target_version']} 校验会话已创建，请完成人工核验后再启动 AI 复核",
        data=build_verification_session_view(session),
    )


@router.get(
    "/{job_id}/verification-sessions/active",
    response_model=ApiResponse[VerificationSessionView],
)
async def get_active_verification_session(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationSessionView]:
    await container.repository.get_job(job_id)
    session = await container.repository.get_active_verification_session(job_id)
    if session is None:
        raise NotFoundError("当前没有进行中的校验")
    return ApiResponse(data=build_verification_session_view(session))


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
