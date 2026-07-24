import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.services import detection_engine as detection_engine_module
from app.services.detection_engine import (
    JsonYoloDetectionEngine,
    PageImageInput,
    UltralyticsYoloDetectionEngine,
)


class FakeTensor:
    def __init__(self, value: Any) -> None:
        self._value = value

    def cpu(self) -> "FakeTensor":
        return self

    def tolist(self) -> Any:
        return self._value


class FakeBoxes:
    def __init__(self) -> None:
        self.xyxy = FakeTensor([[10.0, 20.0, 50.0, 100.0]])
        self.cls = FakeTensor([0.0])
        self.conf = FakeTensor([0.91])


class FakeResult:
    orig_shape = (200, 100)
    boxes = FakeBoxes()


class FakeUltralyticsModel:
    task = "detect"
    names = {0: "qiwu", 1: "xuhao", 2: "tuzhu", 3: "muzang", 4: "zhengti"}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def predict(self, **kwargs: Any) -> list[FakeResult]:
        self.calls.append(kwargs)
        return [FakeResult()]


def test_json_yolo_adapter_returns_normalized_regions(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.json"
    predictions_path.write_text(
        json.dumps(
            {
                "pages": {
                    "3": [
                        {
                            "class_id": 0,
                            "bbox": [0.1, 0.2, 0.4, 0.6],
                            "confidence": 0.92,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    engine = JsonYoloDetectionEngine(predictions_path)

    regions = asyncio.run(
        engine.detect(
            PageImageInput(
                job_id="job-test",
                document_id="document-test",
                page_no=3,
                image_path=tmp_path / "page.png",
                object_key="documents/document-test/pages/0003/rendered/page.png",
                width=1000,
                height=2000,
            )
        )
    )

    assert regions[0]["kind"] == "artifact"
    assert regions[0]["bbox"] == [0.1, 0.2, 0.4, 0.6]
    assert regions[0]["bbox_px"] == [100.0, 400.0, 400.0, 1200.0]
    assert regions[0]["confidence"] == 0.92


def test_ultralytics_adapter_normalizes_boxes_and_preserves_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"trusted-test-checkpoint")
    config_path = tmp_path / "model.yaml"
    config_path.write_text(
        "names:\n  0: qiwu\n  1: xuhao\n  2: tuzhu\n  3: muzang\n  4: zhengti\n",
        encoding="utf-8",
    )
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"test-image")
    backend = FakeUltralyticsModel()
    monkeypatch.setattr(
        detection_engine_module,
        "_load_ultralytics_model",
        lambda _: backend,
    )
    engine = UltralyticsYoloDetectionEngine(
        model_path=model_path,
        config_path=config_path,
        device="0",
        confidence=0.30,
        iou=0.50,
        image_size=640,
        model_name="archaeology-yolo",
        model_version="v1",
        class_mapping={"qiwu": "artifact"},
    )

    regions = asyncio.run(
        engine.detect(
            PageImageInput(
                job_id="job-test",
                document_id="document-test",
                page_no=3,
                image_path=image_path,
                object_key="documents/document-test/pages/0003/rendered/page.png",
                width=100,
                height=200,
            )
        )
    )

    assert regions == [
        {
            "id": "reg_job-test_3_yolo_0",
            "kind": "artifact",
            "bbox": [0.1, 0.1, 0.5, 0.5],
            "bbox_px": [10.0, 20.0, 50.0, 100.0],
            "text": "",
            "confidence": 0.91,
            "source": "yolo_ultralytics",
            "image_id": None,
            "crop_object_key": None,
            "class_id": 0,
            "class_name": "qiwu",
        }
    ]
    assert backend.calls == [
        {
            "source": str(image_path),
            "device": "0",
            "imgsz": 640,
            "conf": 0.30,
            "iou": 0.50,
            "verbose": False,
        }
    ]
    assert engine.config["adapter"] == "ultralytics"


def test_ultralytics_adapter_rejects_class_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"trusted-test-checkpoint")
    config_path = tmp_path / "model.yaml"
    config_path.write_text("names:\n  0: different\n", encoding="utf-8")
    monkeypatch.setattr(
        detection_engine_module,
        "_load_ultralytics_model",
        lambda _: FakeUltralyticsModel(),
    )

    with pytest.raises(ValueError, match="模型类别与配置文件不一致"):
        UltralyticsYoloDetectionEngine(
            model_path=model_path,
            config_path=config_path,
            device="0",
            confidence=0.30,
            iou=0.50,
            image_size=640,
            model_name="archaeology-yolo",
            model_version="v1",
            class_mapping={},
        )


def test_ultralytics_adapter_accepts_gb18030_model_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"trusted-test-checkpoint")
    config_path = tmp_path / "model.yaml"
    config_path.write_bytes(
        (
            "nc: 5  # 类别数量\n"
            "names:\n"
            "  0: qiwu\n"
            "  1: xuhao\n"
            "  2: tuzhu\n"
            "  3: muzang\n"
            "  4: zhengti\n"
        ).encode("gb18030")
    )
    monkeypatch.setattr(
        detection_engine_module,
        "_load_ultralytics_model",
        lambda _: FakeUltralyticsModel(),
    )

    engine = UltralyticsYoloDetectionEngine(
        model_path=model_path,
        config_path=config_path,
        device="0",
        confidence=0.30,
        iou=0.50,
        image_size=640,
        model_name="archaeology-yolo",
        model_version="v1",
        class_mapping={},
    )

    assert engine.model == "archaeology-yolo"
