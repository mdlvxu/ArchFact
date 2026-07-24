from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_container
from app.container import Container
from app.models.schemas import ApiResponse, DocumentCreated, DocumentImageView, DocumentView

router = APIRouter(prefix="/documents", tags=["documents"])


def build_image_view(image: dict) -> DocumentImageView:
    return DocumentImageView(
        image_id=image["_id"],
        document_id=image["document_id"],
        page_no=image["page_no"],
        image_type=image["image_type"],
        content_type=image["content_type"],
        width=image["width"],
        height=image["height"],
        size=image["size"],
        sha256=image["sha256"],
        storage=image["storage"],
        created_at=image["created_at"],
    )


@router.post(
    "",
    response_model=ApiResponse[DocumentCreated],
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: Annotated[UploadFile, File(description="待提取的 PDF 文件")],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[DocumentCreated]:
    document = await container.document_service.upload(file)
    return ApiResponse(
        message="PDF 上传成功",
        data=DocumentCreated(
            document_id=document["_id"],
            filename=document["filename"],
            size=document["size"],
            status=document["status"],
        ),
    )


@router.get("/{document_id}", response_model=ApiResponse[DocumentView])
async def get_document(
    document_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[DocumentView]:
    document = await container.document_service.get(document_id)
    return ApiResponse(
        data=DocumentView(
            document_id=document["_id"],
            filename=document["filename"],
            size=document["size"],
            status=document["status"],
            page_count=document.get("page_count"),
            sha256=document["sha256"],
            created_at=document["created_at"],
            error=document.get("error"),
        )
    )


@router.post(
    "/{document_id}/pages/{page_no}/image",
    response_model=ApiResponse[DocumentImageView],
)
async def render_document_page(
    document_id: str,
    page_no: int,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[DocumentImageView]:
    image = await container.image_service.render_page(document_id, page_no)
    return ApiResponse(message="分页图片已生成", data=build_image_view(image))


@router.get(
    "/{document_id}/images",
    response_model=ApiResponse[list[DocumentImageView]],
)
async def list_document_images(
    document_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[DocumentImageView]]:
    images = await container.image_service.list_images(document_id)
    return ApiResponse(data=[build_image_view(image) for image in images])


@router.get("/{document_id}/images/{image_id}", response_model=ApiResponse[DocumentImageView])
async def get_document_image(
    document_id: str,
    image_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[DocumentImageView]:
    image = await container.image_service.get_image(document_id, image_id)
    return ApiResponse(data=build_image_view(image))


@router.get("/{document_id}/images/{image_id}/content", response_class=FileResponse)
async def get_document_image_content(
    document_id: str,
    image_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> FileResponse:
    image = await container.image_service.get_image(document_id, image_id)
    return FileResponse(
        container.image_service.get_content_path(image),
        media_type=image["content_type"],
        filename=f"page-{image['page_no']}.png",
    )
