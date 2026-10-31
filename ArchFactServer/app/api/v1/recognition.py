from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import get_container
from app.container import Container
from app.models.schemas import ApiResponse

router = APIRouter(prefix="/recognition", tags=["mobile-recognition"])


class IdentifyImageRequest(BaseModel):
    filename: str = Field(default="capture.jpg", max_length=255)
    content_type: str = Field(default="image/jpeg", max_length=100)
    image_base64: str = Field(min_length=1, max_length=16_777_216)
    source_job_id: str | None = Field(default=None, max_length=100)


@router.post("/identify", response_model=ApiResponse[dict])
async def identify_image(
    payload: IdentifyImageRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[dict]:
    result = await container.recognition_service.identify(
        image_base64=payload.image_base64,
        filename=payload.filename,
        source_job_id=payload.source_job_id,
    )
    return ApiResponse(message="Image recognition completed", data=result)
