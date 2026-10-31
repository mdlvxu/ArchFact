from __future__ import annotations

import base64
import binascii
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.application.views import build_record_view
from app.core.config import Settings
from app.core.errors import DomainError
from app.repositories.mongo_repository import MongoRepository
from app.services.detection_engine import DetectionEngine, PageImageInput
from app.services.document_text_index import DocumentTextIndexer
from app.services.ocr_engine import OcrEngine, OcrPageInput


class RecognitionService:
    """Runs YOLO/OCR on one uploaded page and resolves explicit artifact IDs."""

    _max_image_bytes = 12 * 1024 * 1024
    _max_image_pixels = 40_000_000
    _artifact_id_pattern = re.compile(r"[A-Z]?[MHT]\s*\d+\s*[:：]\s*\d+", re.I)

    def __init__(
        self,
        *,
        settings: Settings,
        repository: MongoRepository,
        detector: DetectionEngine,
        ocr_engine: OcrEngine,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._detector = detector
        self._ocr = ocr_engine

    async def identify(
        self, *, image_base64: str, filename: str, source_job_id: str | None = None
    ) -> dict[str, Any]:
        source_job = await self._repository.get_job(source_job_id) if source_job_id else None
        if source_job is not None and source_job.get("status") not in {
            "completed",
            "completed_with_warnings",
        }:
            raise DomainError("所选报告的提取任务尚未完成", code=422, status_code=422)
        content = self._decode_image(image_base64)
        with tempfile.TemporaryDirectory(prefix="archfact-recognition-") as directory:
            image_path = Path(directory) / "input.png"
            width, height = self._normalize_image(content, image_path)
            run_id = uuid4().hex
            page = PageImageInput(
                job_id=f"recognition_{run_id}",
                document_id="mobile_upload",
                page_no=1,
                image_path=image_path,
                object_key=f"recognition/{run_id}.png",
                width=width,
                height=height,
            )
            detections = await self._detector.detect(page)
            ocr_result = await self._ocr.recognize(
                OcrPageInput(page_no=1, image_path=image_path, width=width, height=height)
            )

        latest_job = source_job or await self._repository.get_latest_completed_job()
        ids = self._extract_ids(ocr_result.text)
        cards_by_id: dict[str, dict[str, Any]] = {}
        if latest_job is not None and ids:
            records = await self._load_artifact_records(str(latest_job["_id"]))
            requested = set(ids)
            for record in records:
                for artifact_id in DocumentTextIndexer._record_artifact_ids(record):
                    if artifact_id in requested:
                        card = build_record_view(record).model_dump(mode="json")
                        cards_by_id.setdefault(artifact_id, card)

        matched_ids = [artifact_id for artifact_id in ids if artifact_id in cards_by_id]
        warnings: list[str] = []
        if not self._detector.enabled:
            warnings.append("后端 YOLO 检测模型未启用；当前结果不包含器物框。")
        if not self._ocr.enabled:
            warnings.append("后端 OCR 未启用；当前无法从图片读取器物编号。")
        if latest_job is None:
            warnings.append("尚无已完成的提取任务，无法关联到器物卡片。")
        elif ids and not matched_ids:
            warnings.append("识别到了器物编号，但未在所选报告的提取结果中找到完全一致的器物卡片。")
        elif not ids and self._ocr.enabled:
            warnings.append("未能从图片中识别出明确的器物编号；没有进行模糊匹配。")

        return {
            "filename": Path(filename).name[:180],
            "source_job_id": str(latest_job["_id"]) if latest_job else None,
            "detector": {"enabled": self._detector.enabled, "model": self._detector.model},
            "ocr": {"enabled": self._ocr.enabled, "model": self._ocr.model},
            "ocr_text": ocr_result.text,
            "recognized_artifact_ids": ids,
            "detections": [
                {
                    "id": str(item.get("id", "")),
                    "kind": str(item.get("kind", "other")),
                    "bbox": item.get("bbox", []),
                    "confidence": item.get("confidence"),
                    "class_name": item.get("class_name"),
                }
                for item in detections
            ],
            "matched_records": [cards_by_id[item] for item in matched_ids],
            "unmatched_artifact_ids": [item for item in ids if item not in cards_by_id],
            "warnings": warnings,
        }

    async def _load_artifact_records(self, job_id: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            batch, total = await self._repository.list_records(job_id, page=page, page_size=200)
            records.extend(record for record in batch if record.get("record_type") == "artifact")
            if len(batch) < 200 or len(records) >= total:
                return records
            page += 1

    @classmethod
    def _decode_image(cls, encoded: str) -> bytes:
        value = encoded.strip()
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        if len(value) > (cls._max_image_bytes * 4 // 3 + 16):
            raise DomainError("图片不能超过 12 MB", code=422, status_code=413)
        try:
            content = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DomainError("图片编码无效，请重新选择图片", code=422, status_code=422) from exc
        if not content or len(content) > cls._max_image_bytes:
            raise DomainError("图片不能为空且不能超过 12 MB", code=422, status_code=413)
        return content

    @classmethod
    def _normalize_image(cls, content: bytes, destination: Path) -> tuple[int, int]:
        try:
            with Image.open(__import__("io").BytesIO(content)) as source:
                if source.width * source.height > cls._max_image_pixels:
                    raise DomainError("图片分辨率过大，请压缩后重试", code=422, status_code=413)
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.save(destination, format="PNG", optimize=True)
                return image.width, image.height
        except DomainError:
            raise
        except (UnidentifiedImageError, OSError) as exc:
            raise DomainError(
                "无法读取该图片格式，请选择 JPEG、PNG 或 WebP 图片",
                code=422,
                status_code=415,
            ) from exc

    @classmethod
    def _extract_ids(cls, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).upper()
        result: list[str] = []
        for match in cls._artifact_id_pattern.finditer(normalized):
            value = DocumentTextIndexer.normalize_artifact_id(match.group(0))
            if value and value not in result:
                result.append(value)
        return result[:100]
