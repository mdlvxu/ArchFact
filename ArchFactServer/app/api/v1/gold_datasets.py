import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.api.dependencies import get_container
from app.container import Container
from app.core.errors import DomainError
from app.models.schemas import ApiResponse, GoldDatasetImportRequest, GoldDatasetView

router = APIRouter(prefix="/gold-datasets", tags=["gold-datasets"])


@router.post("/preview", response_model=ApiResponse[dict])
async def preview_gold_workbook(
    file: Annotated[UploadFile, File(description="人工标注 Excel 文件")],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[dict]:
    preview = container.gold_dataset_service.preview_workbook(
        filename=file.filename or "annotations.xlsx",
        content=await file.read(),
    )
    return ApiResponse(data=preview)


@router.post(
    "/import",
    response_model=ApiResponse[GoldDatasetView],
    status_code=status.HTTP_201_CREATED,
)
async def import_gold_workbook(
    document_id: Annotated[str, Form(min_length=1, max_length=120)],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    version: Annotated[str, Form(min_length=1, max_length=40)],
    key_mode: Annotated[str, Form()],
    field_mapping: Annotated[str, Form()],
    file: Annotated[UploadFile, File(description="人工标注 Excel 文件")],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[GoldDatasetView]:
    try:
        mapping = json.loads(field_mapping)
    except json.JSONDecodeError as exc:
        raise DomainError("字段映射格式无效") from exc
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
    ):
        raise DomainError("字段映射格式无效")
    dataset = await container.gold_dataset_service.import_workbook(
        document_id=document_id,
        name=name,
        version=version,
        filename=file.filename or "annotations.xlsx",
        content=await file.read(),
        key_mode=key_mode,
        field_mapping=mapping,
    )
    return ApiResponse(message="跨报告人工评测基准已导入", data=build_gold_dataset_view(dataset))


def build_gold_dataset_view(dataset: dict) -> GoldDatasetView:
    return GoldDatasetView(
        id=dataset["_id"],
        name=dataset["name"],
        document_id=dataset["document_id"],
        document_filename=dataset.get("document_filename", ""),
        report_key=dataset.get("report_key", ""),
        version=dataset["version"],
        status=dataset.get("status", "ready"),
        source_type=dataset.get("source_type", "human_annotation"),
        record_count=dataset.get("record_count", 0),
        region_count=dataset.get("region_count", 0),
        asset_count=dataset.get("asset_count", 0),
        link_count=dataset.get("link_count", 0),
        matched_artifact_assets=dataset.get("matched_artifact_assets", 0),
        matched_color_plate_assets=dataset.get("matched_color_plate_assets", 0),
        source_document_verified=dataset.get("source_document_verified", False),
        workbook_name=dataset.get("workbook_name", ""),
        key_mode=dataset.get("key_mode", "artifact_id"),
        field_mapping=dataset.get("field_mapping", {}),
        capabilities=dataset.get("capabilities", {}),
        source_row_count=dataset.get("source_row_count", dataset.get("record_count", 0)),
        unkeyed_row_count=dataset.get("unkeyed_row_count", 0),
        warnings=dataset.get("warnings", []),
        created_at=dataset["created_at"],
        updated_at=dataset["updated_at"],
    )


@router.post(
    "/import/wenjiashan",
    response_model=ApiResponse[GoldDatasetView],
    status_code=status.HTTP_201_CREATED,
)
async def import_wenjiashan_gold_dataset(
    payload: GoldDatasetImportRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[GoldDatasetView]:
    dataset = await container.gold_dataset_service.import_wenjiashan(
        document_id=payload.document_id,
        version=payload.version,
        replace=payload.replace,
    )
    return ApiResponse(
        message="文家山人工标注数据已导入，仅用于质量评测",
        data=build_gold_dataset_view(dataset),
    )


@router.get("", response_model=ApiResponse[list[GoldDatasetView]])
async def list_gold_datasets(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[GoldDatasetView]]:
    datasets = await container.repository.list_gold_datasets()
    return ApiResponse(data=[build_gold_dataset_view(dataset) for dataset in datasets])


@router.get("/evaluations", response_model=ApiResponse[list[dict]])
async def list_recent_cross_report_evaluations(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[dict]]:
    runs = await container.repository.list_recent_quality_evaluations()
    return ApiResponse(data=[
        {
            "id": run["_id"],
            "job_id": run["job_id"],
            "document_id": run["document_id"],
            "dataset_id": run["dataset_id"],
            "status": run["status"],
            "template_name": run.get("template_name", ""),
            "matching_version_id": run.get("matching_version_id", "M0"),
            "summary": run.get("summary"),
            "created_at": run["created_at"],
        }
        for run in runs
    ])


@router.get("/{dataset_id}", response_model=ApiResponse[GoldDatasetView])
async def get_gold_dataset(
    dataset_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[GoldDatasetView]:
    dataset = await container.repository.get_gold_dataset(dataset_id)
    return ApiResponse(data=build_gold_dataset_view(dataset))


@router.delete("/{dataset_id}", response_model=ApiResponse[None])
async def delete_gold_dataset(
    dataset_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[None]:
    await container.gold_dataset_service.delete_unreferenced_dataset(dataset_id)
    return ApiResponse(message="人工基准已删除", data=None)


@router.get("/{dataset_id}/evaluations", response_model=ApiResponse[list[dict]])
async def list_gold_dataset_evaluations(
    dataset_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[dict]]:
    await container.repository.get_gold_dataset(dataset_id)
    runs = await container.repository.list_quality_evaluation_runs_for_dataset(dataset_id)
    return ApiResponse(
        data=[
            {
                "id": run["_id"],
                "job_id": run["job_id"],
                "document_id": run["document_id"],
                "status": run["status"],
                "template_name": run.get("template_name", ""),
                "matching_version_id": run.get("matching_version_id", "M0"),
                "summary": run.get("summary"),
                "created_at": run["created_at"],
            }
            for run in runs
        ]
    )
