import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ocr_engine import OcrPageInput, TesseractOcrEngine
from app.services.page_preprocessor import ocr_config_hash
from app.services.paddle_ocr_worker import (
    _box,
    _detect_api_version,
    _prepare_ocr_image,
    _recognize_v2,
    _recognize_v3,
    _resolve_v3_models,
    _scale_blocks,
    _v3_ocr_kwargs,
    bundled_ocr_model_dir,
)


def test_detect_api_version_auto_and_override() -> None:
    assert _detect_api_version("auto", "2.9.0") == "2"
    assert _detect_api_version("auto", "3.7.0") == "3"
    assert _detect_api_version("2", "3.7.0") == "2"
    assert _detect_api_version("3", "2.9.0") == "3"


def test_resolve_v3_models_maps_cpu_tiers() -> None:
    assert _resolve_v3_models("PP-OCRv6_small") == (
        "PP-OCRv6_small_det",
        "PP-OCRv6_small_rec",
    )
    assert _resolve_v3_models("PP-OCRv6_medium") == (
        "PP-OCRv6_medium_det",
        "PP-OCRv6_medium_rec",
    )
    assert _resolve_v3_models("ch_PP-OCRv4") == (
        "PP-OCRv4_mobile_det",
        "PP-OCRv4_mobile_rec",
    )


def test_bundled_small_model_dirs_are_passed_to_paddle() -> None:
    kwargs = _v3_ocr_kwargs(
        language="ch",
        use_angle_cls=False,
        model="PP-OCRv6_small",
    )
    assert kwargs["text_detection_model_name"] == "PP-OCRv6_small_det"
    assert kwargs["text_recognition_model_name"] == "PP-OCRv6_small_rec"
    det_dir = Path(kwargs["text_detection_model_dir"])
    rec_dir = Path(kwargs["text_recognition_model_dir"])
    assert det_dir.name == "PP-OCRv6_small_det"
    assert rec_dir.name == "PP-OCRv6_small_rec"
    assert (det_dir / "inference.pdiparams").is_file()
    assert (rec_dir / "inference.pdiparams").is_file()
    assert bundled_ocr_model_dir("PP-OCRv6_small_det") == det_dir


def test_bundled_ocr_model_dir_requires_weights(tmp_path: Path) -> None:
    empty = tmp_path / "models" / "paddleocr" / "PP-OCRv6_small_det"
    empty.mkdir(parents=True)
    assert bundled_ocr_model_dir("PP-OCRv6_small_det", root=tmp_path) is None
    (empty / "inference.pdiparams").write_bytes(b"x")
    assert bundled_ocr_model_dir("PP-OCRv6_small_det", root=tmp_path) == empty


def test_prepare_ocr_image_resizes_and_maps_boxes_back(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (1500, 2096), "white").save(image_path)

    ocr_path, scale, temporary = _prepare_ocr_image(image_path, 1600)
    assert temporary is not None
    assert scale == pytest.approx(1600 / 2096)
    with Image.open(ocr_path) as resized:
        assert max(resized.size) == 1600
        assert resized.size[0] == round(1500 * scale)

    inverse = 1.0 / scale
    blocks = _scale_blocks(
        [{"text": "M1:40", "confidence": 0.9, "bbox_px": [10, 20, 40, 50]}],
        scale,
    )
    assert blocks[0]["bbox_px"] == [
        int(round(10 * inverse)),
        int(round(20 * inverse)),
        int(round(40 * inverse)),
        int(round(50 * inverse)),
    ]
    temporary.unlink()
    assert _prepare_ocr_image(image_path, 4096)[2] is None


def test_ocr_config_hash_ignores_timeout_and_worker_counts() -> None:
    base = {
        "adapter": "paddle",
        "language": "ch",
        "use_angle_cls": False,
        "api_version": "auto",
        "model": "PP-OCRv6_medium",
        "version": "3.7",
        "min_confidence": 0.15,
        "max_side": 1600,
        "timeout_seconds": 120,
        "worker_count": 4,
        "worker_cpu_threads": 4,
    }
    tuned = dict(base, timeout_seconds=180, worker_count=2, worker_cpu_threads=8)
    assert ocr_config_hash(base) == ocr_config_hash(tuned)
    assert ocr_config_hash(base) != ocr_config_hash(dict(base, max_side=960))


def test_paddle_worker_ready_handshake_before_recognize(
    tmp_path: Path,
) -> None:
    from app.services.ocr_engine import PaddleOcrEngine

    worker_path = tmp_path / "fake_paddle_worker.py"
    worker_path.write_text(
        "\n".join(
            [
                "import json, sys",
                "sys.stdout.write(json.dumps({'ok': True, 'ready': True}) + '\\n')",
                "sys.stdout.flush()",
                "for line in sys.stdin:",
                "    request = json.loads(line)",
                "    sys.stdout.write(json.dumps({",
                "        'ok': True,",
                "        'page_no': request.get('page_no'),",
                "        'blocks': [{'text': 'M1', 'confidence': 0.99, 'bbox_px': [1, 2, 10, 20]}],",
                "    }) + '\\n')",
                "    sys.stdout.flush()",
            ]
        ),
        encoding="utf-8",
    )
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"png")
    engine = PaddleOcrEngine(
        python_command=Path(sys.executable),
        worker_path=worker_path,
        language="ch",
        use_angle_cls=False,
        api_version="auto",
        model="PP-OCRv6_medium",
        version="3.7",
        min_confidence=0.15,
        timeout_seconds=5,
        worker_count=1,
        worker_cpu_threads=1,
    )

    async def run() -> str:
        try:
            await engine.warmup()
            result = await engine.recognize(
                OcrPageInput(
                    page_no=1,
                    image_path=image_path,
                    width=100,
                    height=100,
                )
            )
            return result.text
        finally:
            await engine.aclose()

    assert asyncio.run(run()) == "M1"


def test_full_page_ocr_schedules_smaller_retry_side() -> None:
    from app.services.ocr_engine import PaddleOcrEngine

    engine = object.__new__(PaddleOcrEngine)
    engine._max_side = 1600
    engine._fallback_max_side = 960
    page = OcrPageInput(page_no=185, image_path=Path("page.png"), width=1500, height=2096)
    crop = OcrPageInput(
        page_no=185,
        image_path=Path("crop.png"),
        width=140,
        height=90,
        timeout_seconds=20,
    )
    assert engine._max_sides_for(page) == [1600, 960]
    assert engine._max_sides_for(crop) == [1600]


def test_box_accepts_numpy_like_polys() -> None:
    class Array:
        def tolist(self) -> list[list[float]]:
            return [[10.2, 20.8], [40.0, 21.0], [39.0, 50.1], [11.0, 49.0]]

    assert _box(Array()) == [10, 21, 40, 50]


def test_recognize_v3_parses_rec_fields() -> None:
    class FakeOcr:
        def predict(self, image_path: str) -> list[dict[str, object]]:
            assert image_path.endswith("page.png")
            return [
                {
                    "rec_texts": ["T03022:3", ""],
                    "rec_scores": [0.99, 0.1],
                    "dt_polys": [
                        [[1, 2], [40, 2], [40, 20], [1, 20]],
                        [[1, 30], [10, 30], [10, 40], [1, 40]],
                    ],
                }
            ]

    blocks = _recognize_v3(FakeOcr(), "C:/tmp/page.png")
    assert blocks == [
        {
            "text": "T03022:3",
            "confidence": 0.99,
            "bbox_px": [1, 2, 40, 20],
        }
    ]


def test_recognize_v3_reads_nested_json_payload() -> None:
    class FakeOcr:
        def predict(self, image_path: str) -> list[object]:
            del image_path
            return [
                SimpleNamespace(
                    json={
                        "res": {
                            "rec_texts": ["图一"],
                            "rec_scores": [0.88],
                            "dt_polys": [[[5, 5], [25, 5], [25, 15], [5, 15]]],
                        }
                    }
                )
            ]

    blocks = _recognize_v3(FakeOcr(), "page.png")
    assert blocks[0]["text"] == "图一"
    assert blocks[0]["confidence"] == 0.88


def test_recognize_v2_parses_classic_lines() -> None:
    class FakeOcr:
        def ocr(self, image_path: str, cls: bool = False) -> list[list[object]]:
            del image_path, cls
            return [
                [
                    [[[0, 0], [30, 0], [30, 10], [0, 10]], ("M1:2", 0.91)],
                ]
            ]

    blocks = _recognize_v2(FakeOcr(), "page.png", use_angle_cls=False)
    assert blocks == [
        {"text": "M1:2", "confidence": 0.91, "bbox_px": [0, 0, 30, 10]}
    ]


def test_cancelling_tesseract_recognition_kills_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            started.set()
            await asyncio.Event().wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    process = FakeProcess()

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    image_path = tmp_path / "page.png"
    image_path.touch()
    engine = TesseractOcrEngine(
        command=sys.executable,
        languages="chi_sim",
        page_segmentation_mode=6,
        min_confidence=0.4,
    )

    async def run() -> None:
        task = asyncio.create_task(
            engine.recognize(
                OcrPageInput(
                    page_no=1,
                    image_path=image_path,
                    width=100,
                    height=100,
                )
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert process.killed is True
