import asyncio
from types import SimpleNamespace

import pytest

from app.core.errors import DomainError
from app.services.recognition_service import RecognitionService


def test_extracts_and_normalizes_explicit_artifact_ids() -> None:
    assert RecognitionService._extract_ids("编号 M 13：20，另见 H125:1；M13:20") == [
        "M13:20",
        "H125:1",
    ]


def test_rejects_invalid_base64_image_payload() -> None:
    with pytest.raises(DomainError):
        RecognitionService._decode_image("not base64!")


def test_rejects_image_larger_than_limit() -> None:
    oversized = "A" * (RecognitionService._max_image_bytes * 4 // 3 + 20)
    with pytest.raises(DomainError):
        RecognitionService._decode_image(oversized)


def test_recognition_uses_selected_report_instead_of_global_latest(monkeypatch) -> None:
    class Repository:
        async def get_job(self, job_id):
            assert job_id == "selected_wenjiashan_job"
            return {"_id": job_id, "status": "completed"}

        async def get_latest_completed_job(self):
            raise AssertionError("Recognition must not switch to another report")

    class Detector:
        enabled = True
        model = "test"

        async def detect(self, page):
            return []

    class Ocr:
        enabled = True
        model = "test"

        async def recognize(self, page):
            return SimpleNamespace(text="")

    monkeypatch.setattr(RecognitionService, "_decode_image", lambda self, value: b"image")
    monkeypatch.setattr(
        RecognitionService, "_normalize_image", lambda self, content, path: (10, 10)
    )
    service = RecognitionService(
        settings=None, repository=Repository(), detector=Detector(), ocr_engine=Ocr()
    )
    result = asyncio.run(
        service.identify(
            image_base64="unused", filename="page.png", source_job_id="selected_wenjiashan_job"
        )
    )
    assert result["source_job_id"] == "selected_wenjiashan_job"
