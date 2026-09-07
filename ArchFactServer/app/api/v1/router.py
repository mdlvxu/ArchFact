from typing import Any

from fastapi import APIRouter

from app.api.v1.configuration import router as configuration_router
from app.api.v1.documents import router as documents_router
from app.api.v1.extraction_jobs import router as extraction_jobs_router
from app.api.v1.extraction_records import router as extraction_records_router
from app.api.v1.gold_datasets import router as gold_datasets_router
from app.api.v1.quality_evaluations import router as quality_evaluations_router
from app.api.v1.rematches import router as rematches_router
from app.api.v1.verification import router as verification_router
from app.core.hardware import hardware_status_payload

router = APIRouter()
router.include_router(configuration_router)
router.include_router(documents_router)
router.include_router(extraction_jobs_router)
router.include_router(extraction_records_router)
router.include_router(rematches_router)
router.include_router(verification_router)
router.include_router(gold_datasets_router)
router.include_router(quality_evaluations_router)


@router.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    return {"status": "ok", **hardware_status_payload()}
