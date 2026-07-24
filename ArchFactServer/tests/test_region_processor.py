import asyncio
from pathlib import Path

from PIL import Image

from app.core.config import Settings
from app.infrastructure.local_image_storage import LocalImageStorage
from app.services.detection_engine import PageImageInput
from app.services.ocr_engine import OcrPageInput, OcrPageResult
from app.services.region_processor import RegionProcessor


class FakeRegionOcrEngine:
    enabled = True
    provider = "test-ocr"
    model = "region-ocr"
    version = "1"
    config = {"adapter": "fake"}

    def __init__(self, confidence: float = 0.88) -> None:
        self.calls: list[OcrPageInput] = []
        self.confidence = confidence

    async def recognize(self, page: OcrPageInput) -> OcrPageResult:
        self.calls.append(page)
        text = "12" if page.segmentation_mode == 7 else "图一二三 出土器物"
        return OcrPageResult(
            text=text,
            blocks=[
                {
                    "text": text,
                    "bbox": [0.0, 0.0, 1.0, 1.0],
                    "confidence": self.confidence,
                }
            ],
        )


def test_region_processor_persists_crops_and_ocr_text(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    settings = Settings(
        app_env="test",
        file_storage_root=tmp_path / "files",
        region_crop_padding=0,
    )
    storage = LocalImageStorage(settings)
    ocr = FakeRegionOcrEngine()
    processor = RegionProcessor(settings=settings, image_storage=storage, ocr_engine=ocr)
    regions = [
        {"id": "artifact-1", "kind": "artifact", "bbox": [0.1, 0.1, 0.5, 0.5]},
        {"id": "number-1", "kind": "number", "bbox": [0.2, 0.6, 0.3, 0.7]},
        {"id": "caption-1", "kind": "caption", "bbox": [0.1, 0.75, 0.9, 0.9]},
        {"id": "group-1", "kind": "group", "bbox": [0.0, 0.0, 1.0, 1.0]},
    ]

    result = asyncio.run(
        processor.process(
            page=PageImageInput(
                job_id="job-1",
                document_id="document-1",
                page_no=3,
                image_path=image_path,
                object_key="documents/document-1/pages/0003/rendered/page.png",
                width=100,
                height=100,
            ),
            regions=regions,
            ocr_model_run_id="run-ocr",
        )
    )

    artifact, number, caption, group = result
    assert storage.resolve(artifact["crop_object_key"]).is_file()
    assert artifact["crop_width"] == 40
    assert "crop_object_key" not in group
    assert number["text"] == "12"
    assert number["ocr_raw_text"] == "12"
    assert caption["text"] == "图一二三 出土器物"
    assert number["ocr_confidence"] == 0.88
    assert number["ocr_model_run_id"] == "run-ocr"
    assert [call.segmentation_mode for call in ocr.calls] == [7, 6]
    assert [(call.width, call.height) for call in ocr.calls] == [(80, 80), (320, 60)]
    assert not any((tmp_path / "files").rglob("*.ocr.png"))


def test_region_processor_keeps_low_confidence_ocr_as_audit_candidate(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    settings = Settings(
        app_env="test",
        file_storage_root=tmp_path / "files",
        region_crop_padding=0,
        region_ocr_min_confidence=0.5,
    )
    processor = RegionProcessor(
        settings=settings,
        image_storage=LocalImageStorage(settings),
        ocr_engine=FakeRegionOcrEngine(confidence=0.3),
    )

    result = asyncio.run(
        processor.process(
            page=PageImageInput(
                job_id="job-1",
                document_id="document-1",
                page_no=3,
                image_path=image_path,
                object_key="documents/document-1/pages/0003/rendered/page.png",
                width=100,
                height=100,
            ),
            regions=[{"id": "number-1", "kind": "number", "bbox": [0.2, 0.6, 0.3, 0.7]}],
            ocr_model_run_id="run-ocr",
        )
    )

    assert result[0].get("text", "") == ""
    assert result[0]["ocr_raw_text"] == "12"
    assert result[0]["ocr_confidence"] == 0.3
    assert "置信度低于阈值" in result[0]["ocr_error"]


def test_region_processor_reuses_confident_page_caption_before_crop_ocr(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    settings = Settings(
        app_env="test",
        file_storage_root=tmp_path / "files",
        region_crop_padding=0,
        region_ocr_min_confidence=0.5,
    )
    ocr = FakeRegionOcrEngine()
    processor = RegionProcessor(
        settings=settings,
        image_storage=LocalImageStorage(settings),
        ocr_engine=ocr,
    )

    result = asyncio.run(
        processor.process(
            page=PageImageInput(
                job_id="job-1",
                document_id="document-1",
                page_no=3,
                image_path=image_path,
                object_key="documents/document-1/pages/0003/rendered/page.png",
                width=100,
                height=100,
            ),
            regions=[{"id": "caption-1", "kind": "caption", "bbox": [0.2, 0.6, 0.6, 0.7]}],
            page_ocr_blocks=[
                {
                    "text": "图12 出土器物",
                    "bbox": [0.22, 0.61, 0.55, 0.69],
                    "confidence": 0.96,
                    "source": "paddleocr",
                }
            ],
            page_ocr_model_run_id="run-page-ocr",
        )
    )

    assert result[0]["text"] == "图12 出土器物"
    assert result[0]["ocr_reused_from_page"] is True
    assert result[0]["ocr_model_run_id"] == "run-page-ocr"
    assert ocr.calls == []
