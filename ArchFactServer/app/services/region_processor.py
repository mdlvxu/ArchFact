import asyncio
import io
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from app.core.config import Settings
from app.infrastructure.local_image_storage import LocalImageStorage
from app.services.detection_engine import PageImageInput
from app.services.ocr_engine import OcrEngine, OcrPageInput


class RegionProcessor:
    """Persists useful detector crops and enriches textual regions with local OCR."""

    _crop_kinds = {
        "artifact",
        "number",
        "caption",
        "grave_drawing",
        "line_drawing",
        "color_plate",
    }
    _ocr_segmentation_modes = {"number": 7, "caption": 6}

    def __init__(
        self,
        *,
        settings: Settings,
        image_storage: LocalImageStorage,
        ocr_engine: OcrEngine,
    ) -> None:
        self._image_storage = image_storage
        self.ocr_engine = ocr_engine
        self._padding = settings.region_crop_padding
        self._ocr_min_confidence = settings.region_ocr_min_confidence

    async def process(
        self,
        *,
        page: PageImageInput,
        regions: list[dict[str, Any]],
        ocr_model_run_id: str | None = None,
        page_ocr_blocks: list[dict[str, Any]] | None = None,
        page_ocr_model_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        processed = [dict(region) for region in regions]
        candidates = [region for region in processed if region.get("kind") in self._crop_kinds]
        if not candidates:
            return processed

        crops = await asyncio.to_thread(self._crop_sync, page.image_path, candidates)
        for region in processed:
            crop = crops.get(str(region.get("id")))
            if crop is None:
                continue
            object_key = (
                f"documents/{page.document_id}/pages/{page.page_no:04d}/crops/"
                f"{region['kind']}/{region['id']}.png"
            )
            try:
                crop_path = await self._image_storage.write(object_key, crop["content"])
            except Exception as exc:
                region["crop_error"] = str(exc)
                continue
            region.update(
                crop_object_key=object_key,
                crop_width=crop["width"],
                crop_height=crop["height"],
                crop_content_type="image/png",
            )

            segmentation_mode = self._ocr_segmentation_modes.get(str(region.get("kind")))
            if segmentation_mode is not None and self.ocr_engine.enabled:
                if self._reuse_page_ocr(
                    region=region,
                    blocks=page_ocr_blocks or [],
                    ocr_model_run_id=page_ocr_model_run_id,
                ):
                    continue
                await self._recognize_region(
                    page_no=page.page_no,
                    crop_path=crop_path,
                    region=region,
                    segmentation_mode=segmentation_mode,
                    ocr_model_run_id=ocr_model_run_id,
                )
        return processed

    def _reuse_page_ocr(
        self,
        *,
        region: dict[str, Any],
        blocks: list[dict[str, Any]],
        ocr_model_run_id: str | None,
    ) -> bool:
        region_bbox = region.get("bbox")
        if not self._is_bbox(region_bbox):
            return False
        if region.get("kind") == "number":
            return False
        matches: list[dict[str, Any]] = []
        for block in blocks:
            block_bbox = block.get("bbox")
            if not self._is_bbox(block_bbox):
                continue
            intersection_width = max(
                0.0,
                min(region_bbox[2], block_bbox[2]) - max(region_bbox[0], block_bbox[0]),
            )
            intersection_height = max(
                0.0,
                min(region_bbox[3], block_bbox[3]) - max(region_bbox[1], block_bbox[1]),
            )
            intersection = intersection_width * intersection_height
            block_area = max(
                (block_bbox[2] - block_bbox[0]) * (block_bbox[3] - block_bbox[1]),
                1e-9,
            )
            center_x = (block_bbox[0] + block_bbox[2]) / 2
            center_y = (block_bbox[1] + block_bbox[3]) / 2
            center_inside = (
                region_bbox[0] <= center_x <= region_bbox[2]
                and region_bbox[1] <= center_y <= region_bbox[3]
            )
            if intersection / block_area >= 0.55 or center_inside:
                matches.append(block)
        if not matches:
            return False

        matches.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
        text = " ".join(
            str(block.get("text") or "").strip()
            for block in matches
            if str(block.get("text") or "").strip()
        ).strip()
        if not text:
            return False
        confidences = [
            float(block["confidence"])
            for block in matches
            if isinstance(block.get("confidence"), (int, float))
        ]
        confidence = sum(confidences) / len(confidences) if confidences else None
        if confidence is None or confidence < max(self._ocr_min_confidence, 0.85):
            return False

        region.update(
            text=text,
            ocr_raw_text=text,
            ocr_confidence=confidence,
            ocr_source=str(matches[0].get("source") or self.ocr_engine.provider),
            ocr_model=self.ocr_engine.model,
            ocr_version=self.ocr_engine.version,
            ocr_model_run_id=ocr_model_run_id,
            ocr_reused_from_page=True,
            ocr_error=None,
        )
        return True

    async def _recognize_region(
        self,
        *,
        page_no: int,
        crop_path: Path,
        region: dict[str, Any],
        segmentation_mode: int,
        ocr_model_run_id: str | None,
    ) -> None:
        region["ocr_model_run_id"] = ocr_model_run_id
        ocr_path: Path | None = None
        try:
            ocr_path, ocr_width, ocr_height = await asyncio.to_thread(
                self._prepare_ocr_image_sync,
                crop_path,
                str(region.get("kind")),
            )
            result = await self.ocr_engine.recognize(
                OcrPageInput(
                    page_no=page_no,
                    image_path=ocr_path,
                    width=ocr_width,
                    height=ocr_height,
                    segmentation_mode=segmentation_mode,
                )
            )
        except Exception as exc:
            region["ocr_error"] = str(exc)
            return
        finally:
            if ocr_path is not None:
                ocr_path.unlink(missing_ok=True)

        text = " ".join(line.strip() for line in result.text.splitlines() if line.strip())
        if not text:
            region["ocr_error"] = "OCR 未识别到有效文字"
            return
        confidences = [
            float(block["confidence"])
            for block in result.blocks
            if isinstance(block.get("confidence"), (int, float))
        ]
        confidence = sum(confidences) / len(confidences) if confidences else None
        region.update(
            ocr_raw_text=text,
            ocr_confidence=confidence,
            ocr_source=self.ocr_engine.provider,
            ocr_model=self.ocr_engine.model,
            ocr_version=self.ocr_engine.version,
        )
        if confidence is None or confidence < self._ocr_min_confidence:
            region["ocr_error"] = (
                f"OCR 置信度低于阈值 {self._ocr_min_confidence:.2f}，已保留原始候选文本"
            )
            return
        region.update(text=text, ocr_error=None)

    @staticmethod
    def _prepare_ocr_image_sync(crop_path: Path, kind: str) -> tuple[Path, int, int]:
        target_min_dimension, base_scale, max_scale = (96, 4, 8) if kind == "number" else (48, 2, 4)
        temporary_path = crop_path.with_name(f".{crop_path.stem}.ocr.png")
        with Image.open(crop_path) as source:
            image = ImageOps.autocontrast(ImageOps.grayscale(source))
            smallest_dimension = max(1, min(image.size))
            scale = min(
                max_scale,
                max(base_scale, math.ceil(target_min_dimension / smallest_dimension)),
            )
            if scale > 1:
                image = image.resize(
                    (image.width * scale, image.height * scale),
                    Image.Resampling.LANCZOS,
                )
            image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=140, threshold=3))
            image.save(temporary_path, format="PNG", optimize=True)
        return temporary_path, image.width, image.height

    def _crop_sync(
        self,
        image_path: Path,
        regions: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        crops: dict[str, dict[str, Any]] = {}
        with Image.open(image_path) as source:
            image = source.convert("RGB")
            width, height = image.size
            for region in regions:
                bbox = region.get("bbox")
                if not self._is_bbox(bbox):
                    continue
                x1 = max(0, math.floor((bbox[0] - self._padding) * width))
                y1 = max(0, math.floor((bbox[1] - self._padding) * height))
                x2 = min(width, math.ceil((bbox[2] + self._padding) * width))
                y2 = min(height, math.ceil((bbox[3] + self._padding) * height))
                if x1 >= x2 or y1 >= y2:
                    continue
                crop = image.crop((x1, y1, x2, y2))
                output = io.BytesIO()
                crop.save(output, format="PNG", optimize=True)
                crops[str(region["id"])] = {
                    "content": output.getvalue(),
                    "width": crop.width,
                    "height": crop.height,
                }
        return crops

    @staticmethod
    def _is_bbox(bbox: Any) -> bool:
        return (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in bbox)
            and bbox[0] < bbox[2]
            and bbox[1] < bbox[3]
        )
