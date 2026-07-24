from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_container
from app.container import Container
from app.models.schemas import ApiResponse, GoldDatasetImportRequest, GoldDatasetView

router = APIRouter(prefix="/gold-datasets", tags=["gold-datasets"])


def build_gold_dataset_view(dataset: dict) -> GoldDatasetView:
    return GoldDatasetView(
        id=dataset["_id"],
        name=dataset["name"],
        document_id=dataset["document_id"],
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
        message="文家山金标准已导入，仅用于质量评测",
        data=build_gold_dataset_view(dataset),
    )


@router.get("", response_model=ApiResponse[list[GoldDatasetView]])
async def list_gold_datasets(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[GoldDatasetView]]:
    datasets = await container.repository.list_gold_datasets()
    return ApiResponse(data=[build_gold_dataset_view(dataset) for dataset in datasets])


@router.get("/{dataset_id}", response_model=ApiResponse[GoldDatasetView])
async def get_gold_dataset(
    dataset_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[GoldDatasetView]:
    dataset = await container.repository.get_gold_dataset(dataset_id)
    return ApiResponse(data=build_gold_dataset_view(dataset))
