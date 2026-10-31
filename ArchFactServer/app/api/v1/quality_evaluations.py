from io import BytesIO
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_container
from app.container import Container
from app.core.errors import ConflictError
from app.models.schemas import (
    ApiResponse,
    QualityEvaluationCreate,
    QualityEvaluationItemPage,
    QualityEvaluationItemView,
    QualityEvaluationRunView,
)
from app.services.quality_catalog import preview_field_value, select_preview_cards

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
        template_name=run.get("template_name", ""),
        scoring_version=run.get("scoring_version", ""),
        semantic_model=run.get("semantic_model", ""),
        status=run["status"],
        progress=run.get("progress", {}),
        summary=run.get("summary"),
        field_metrics=run.get("field_metrics", []),
        ocr_metrics=run.get("ocr_metrics", []),
        detection_metrics=run.get("detection_metrics", []),
        relation_metrics=run.get("relation_metrics", {}),
        unmatched=run.get("unmatched", {}),
        warnings=run.get("warnings", []),
        logs=run.get("logs", []),
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
        crop_region_id=item.get("crop_region_id"),
        color_plate_region_id=item.get("color_plate_region_id"),
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


@router.post("/{evaluation_id}/{action}", response_model=ApiResponse[QualityEvaluationRunView])
async def control_quality_evaluation(
    job_id: str,
    evaluation_id: str,
    action: Literal["pause", "resume", "terminate"],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[QualityEvaluationRunView]:
    run = await container.quality_evaluation_service.control(job_id, evaluation_id, action)
    return ApiResponse(data=build_run_view(run))


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


@router.get("/{evaluation_id}/export")
async def export_quality_evaluation(
    job_id: str,
    evaluation_id: str,
    container: Annotated[Container, Depends(get_container)],
    kind: Annotated[Literal["detail", "text_image"], Query()] = "detail",
) -> StreamingResponse:
    """Export saved results, including historical runs; never recompute them."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as WorkbookImage
    from openpyxl.styles import Alignment, Font, PatternFill

    run = await container.repository.get_quality_evaluation_run(
        job_id=job_id, evaluation_id=evaluation_id
    )
    if run["status"] != "completed":
        raise ConflictError("评测完成后才可导出结果")
    dataset = await container.repository.get_gold_dataset(run["dataset_id"])
    gold = await container.repository.list_gold_records(run["dataset_id"])
    gold_by_id = {str(item["_id"]): item for item in gold}
    items: list[dict] = []
    page = 1
    while True:
        batch, total = await container.repository.list_quality_evaluation_items(
            job_id=job_id,
            evaluation_id=evaluation_id,
            page=page,
            page_size=200,
        )
        items.extend(batch)
        if len(items) >= total or not batch:
            break
        page += 1
    items.sort(
        key=lambda item: gold_by_id.get(str(item.get("gold_record_id")), {}).get(
            "source_row", 10**9
        )
    )
    all_records = (
        await container.repository.list_job_records(job_id) if kind == "text_image" else []
    )
    records = select_preview_cards(all_records)
    record_by_id = {str(record["_id"]): record for record in records}
    crop_by_entity = {
        str(record["entity_id"]): (
            record.get("primary_artifact_region_id") or record.get("thumbnail_region_id")
        )
        for record in all_records
        if record.get("entity_id") and (
            record.get("primary_artifact_region_id") or record.get("thumbnail_region_id")
        )
    }

    def safe(value: object) -> object:
        if value is None:
            return ""
        text = str(value)
        return "'" + text if text.startswith(("=", "+", "-", "@")) else text

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "人工标注左外关联" if kind == "detail" else "文本与器物图片"
    fields = [
        "artifact_id",
        "measurements",
        "morphological_description",
        "surface_color",
        "artifact_group",
        "category",
        "type",
        "subtype",
        "texture",
        "completeness",
        "figure_caption",
        "stratigraphy",
        "notes",
    ]
    labels = {
        "artifact_id": "器物编号",
        "measurements": "尺寸",
        "morphological_description": "外形描述",
        "surface_color": "颜色",
        "artifact_group": "器型1",
        "category": "器型2/类别",
        "type": "型别",
        "subtype": "式别",
        "texture": "材质/质地",
        "completeness": "完整程度",
        "figure_caption": "图注",
        "stratigraphy": "地层",
        "notes": "备注",
    }
    source_headers = [header for header in dataset.get("source_headers", []) if header]
    human_headers = source_headers if source_headers else [labels[key] for key in fields]
    headers = (
        ["人工Excel行号"]
        + [f"人工_{header}" for header in human_headers]
        + ["匹配状态"]
        + [f"机器_{labels[key]}" for key in fields]
    )
    if kind == "detail":
        headers += [f"{labels[key]}_得分" for key in fields] + ["形态描述_语义理由", "机器来源页"]
    else:
        headers += ["机器器物裁剪图", "机器彩图", "机器来源页", "裁剪图区域ID", "彩图区域ID"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="9A5A25")
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = (
        f"A1:{sheet.cell(row=1, column=len(headers)).column_letter}{len(items) + 1}"
    )

    async def embed_image(region_id: object, header: str, row_number: int) -> None:
        if not region_id:
            return
        column = headers.index(header) + 1
        try:
            path = await container.image_service.get_region_crop_path(job_id, str(region_id))
            picture = WorkbookImage(str(path))
            scale = min(90 / picture.width, 82 / picture.height, 1)
            picture.width, picture.height = (
                int(picture.width * scale), int(picture.height * scale)
            )
            column_letter = sheet.cell(row=1, column=column).column_letter
            sheet.add_image(picture, f"{column_letter}{row_number}")
        except Exception:
            sheet.cell(row=row_number, column=column, value="图片不可用")

    for item in items:
        human = gold_by_id.get(str(item.get("gold_record_id")), {})
        human_fields = human.get("fields") or {}
        machine = record_by_id.get(str(item.get("record_id")))
        comparison = {field["key"]: field for field in item.get("field_results", [])}
        machine_fields = {
            key: (
                comparison[key].get("actual")
                if key in comparison
                else preview_field_value(machine, key)
                if machine
                else ""
            )
            for key in fields
        }
        if source_headers:
            human_values = [safe((human.get("raw") or {}).get(header)) for header in source_headers]
        else:
            human_values = [safe(human_fields.get(key)) for key in fields]
        row = [human.get("source_row", ""), *human_values, item["match_status"]]
        row += [safe(machine_fields.get(key)) for key in fields]
        if kind == "detail":
            row += [comparison.get(key, {}).get("score", "") for key in fields]
            row += [
                safe(comparison.get("morphological_description", {}).get("reason")),
                ",".join(str(page) for page in item.get("source_pages", [])),
            ]
        else:
            region_id = (
                item.get("crop_region_id")
                or (machine or {}).get("primary_artifact_region_id")
                or (machine or {}).get("thumbnail_region_id")
            )
            if not region_id and machine and machine.get("entity_id"):
                region_id = crop_by_entity.get(str(machine["entity_id"]))
            plate_id = item.get("color_plate_region_id")
            row += [
                "",
                "",
                ",".join(str(page) for page in item.get("source_pages", [])),
                safe(region_id),
                safe(plate_id),
            ]
        sheet.append(row)
        row_number = sheet.max_row
        sheet.row_dimensions[row_number].height = 76 if kind == "text_image" else 34
        if kind == "text_image":
            await embed_image(region_id, "机器器物裁剪图", row_number)
            await embed_image(plate_id, "机器彩图", row_number)
    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = (
            20 if header.endswith("裁剪图") else 28 if "描述" in header or "图注" in header else 17
        )
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = quote(f"{evaluation_id}_{kind}.xlsx")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
