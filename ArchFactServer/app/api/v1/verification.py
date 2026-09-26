from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_container
from app.application.views import (
    build_ai_verification_run_view,
    build_machine_verification_run_view,
    build_record_view,
    build_verification_experiment_view,
    build_verification_session_view,
    build_verification_version_view,
)
from app.container import Container
from app.core.errors import NotFoundError
from app.models.schemas import (
    AiVerificationRunView,
    ApiResponse,
    ExtractionRecordView,
    MachineVerificationRunView,
    VerificationCompleteResult,
    VerificationExperimentView,
    VerificationItemUpdate,
    VerificationSessionCreate,
    VerificationSessionView,
    VerificationVersionView,
)
from app.services.verification_excel_export import build_machine_verification_excel

router = APIRouter(prefix="/extraction-jobs", tags=["verification"])


@router.get(
    "/{job_id}/verification-experiments/active",
    response_model=ApiResponse[VerificationExperimentView],
)
async def get_active_verification_experiment(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationExperimentView]:
    experiment = await container.repository.get_active_verification_experiment(job_id)
    return ApiResponse(data=build_verification_experiment_view(experiment))


@router.get(
    "/{job_id}/verification-experiments",
    response_model=ApiResponse[list[VerificationExperimentView]],
)
async def list_verification_experiments(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[VerificationExperimentView]]:
    experiments = await container.repository.list_verification_experiments(job_id)
    return ApiResponse(
        data=[build_verification_experiment_view(experiment) for experiment in experiments]
    )


@router.post(
    "/{job_id}/verification-experiments",
    response_model=ApiResponse[VerificationExperimentView],
    status_code=status.HTTP_201_CREATED,
)
async def create_verification_experiment(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[VerificationExperimentView]:
    experiment = await container.repository.create_verification_experiment(job_id)
    return ApiResponse(
        message=(
            f"{experiment['name']} 已创建：已保留 PDF 与提取结果，"
            "请从 LLM 断言 V1 开始重新执行全量校验"
        ),
        data=build_verification_experiment_view(experiment),
    )


@router.post(
    "/{job_id}/verification-experiments/active/reset-invalid-v1",
    response_model=ApiResponse[None],
)
async def reset_invalid_v1_experiment(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[None]:
    reset = await container.repository.reset_invalid_v1_experiment(job_id)
    return ApiResponse(
        data=None,
        message=(
            "已清除因模型服务不可用产生的无效 V1；可使用 LLM 断言 V1 重新执行"
            if reset
            else "无效 V1 已在此前清除；页面将刷新为可重新执行的 V1 状态"
        ),
    )


@router.post(
    "/{job_id}/machine-verification-runs",
    response_model=ApiResponse[MachineVerificationRunView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_machine_verification_run(
    job_id: str,
    payload: VerificationSessionCreate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[MachineVerificationRunView]:
    run = await container.verification_service.start_machine_verification(
        job_id=job_id,
        rules=[rule.model_dump() for rule in payload.rules],
        sample_size=payload.sample_size,
        assertion_baseline_id=payload.assertion_baseline_id,
    )
    message = (
        "正在进行全量机器校验；完成后将生成 18 条人工核验样本"
        if run.get("mode") == "initial"
        else "正在按新断言复核全量器物，将复用既有 18 条人工核验结论"
    )
    return ApiResponse(message=message, data=build_machine_verification_run_view(run))


@router.get(
    "/{job_id}/machine-verification-runs/active",
    response_model=ApiResponse[MachineVerificationRunView | None],
)
async def get_active_machine_verification_run(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[MachineVerificationRunView | None]:
    await container.repository.get_job(job_id)
    run = await container.repository.get_active_machine_verification_run(job_id)
    return ApiResponse(data=build_machine_verification_run_view(run) if run else None)


@router.get(
    "/{job_id}/machine-verification-runs/{run_id}",
    response_model=ApiResponse[MachineVerificationRunView],
)
async def get_machine_verification_run(
    job_id: str,
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[MachineVerificationRunView]:
    run = await container.repository.get_machine_verification_run(job_id=job_id, run_id=run_id)
    return ApiResponse(data=build_machine_verification_run_view(run))


@router.post(
    "/{job_id}/machine-verification-runs/{run_id}/pause",
    response_model=ApiResponse[MachineVerificationRunView],
)
async def pause_machine_verification_run(
    job_id: str,
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[MachineVerificationRunView]:
    run = await container.verification_service.pause_machine_verification(
        job_id=job_id,
        run_id=run_id,
    )
    return ApiResponse(
        message="全量机器校验已暂停，可稍后继续",
        data=build_machine_verification_run_view(run),
    )


@router.post(
    "/{job_id}/machine-verification-runs/{run_id}/resume",
    response_model=ApiResponse[MachineVerificationRunView],
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_machine_verification_run(
    job_id: str,
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[MachineVerificationRunView]:
    run = await container.verification_service.resume_machine_verification(
        job_id=job_id,
        run_id=run_id,
    )
    return ApiResponse(message="全量机器校验已继续", data=build_machine_verification_run_view(run))


@router.post(
    "/{job_id}/machine-verification-runs/{run_id}/terminate",
    response_model=ApiResponse[MachineVerificationRunView],
)
async def terminate_machine_verification_run(
    job_id: str,
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[MachineVerificationRunView]:
    run = await container.verification_service.terminate_machine_verification(
        job_id=job_id,
        run_id=run_id,
    )
    return ApiResponse(
        message="本次全量校验已终止；可修改规则后重新执行",
        data=build_machine_verification_run_view(run),
    )


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
    # 人工核验必须属于当前活动实验。否则在历史实验遗留会话尚未清理时，
    # 前端可能跳转到错误的 18 条样本。
    experiment = await container.repository.get_active_verification_experiment(job_id)
    session = await container.repository.get_active_verification_session(
        job_id,
        experiment_id=experiment["_id"],
    )
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


@router.get("/{job_id}/verification-versions/{version_id}/machine-details.xlsx")
async def export_machine_verification_details_excel(
    job_id: str,
    version_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> StreamingResponse:
    version, machine_run, machine_items, records = (
        await container.repository.get_machine_verification_export_data(
            job_id=job_id,
            version_id=version_id,
        )
    )
    content = build_machine_verification_excel(
        version=version,
        machine_run=machine_run,
        machine_items=machine_items,
        records=records,
    )
    filename = f"ArchFact-V{version['version']}-full-machine-verification.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{job_id}/verification-versions",
    response_model=ApiResponse[list[VerificationVersionView]],
)
async def list_verification_versions(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
    experiment_id: str | None = None,
) -> ApiResponse[list[VerificationVersionView]]:
    await container.repository.get_job(job_id)
    versions = await container.repository.list_verification_versions(
        job_id, experiment_id=experiment_id
    )
    return ApiResponse(data=[build_verification_version_view(version) for version in versions])
