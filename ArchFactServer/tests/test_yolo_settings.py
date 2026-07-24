from pathlib import Path

import pytest

from app.core.config import Settings


def test_ultralytics_settings_require_model_files() -> None:
    settings = Settings(_env_file=None, yolo_adapter="ultralytics")

    with pytest.raises(ValueError, match="YOLO_MODEL_PATH"):
        settings.validate_runtime()


def test_ultralytics_settings_accept_existing_model_files(tmp_path: Path) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"trusted-test-checkpoint")
    config_path = tmp_path / "model.yaml"
    config_path.write_text("names:\n  0: qiwu\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        yolo_adapter="ultralytics",
        yolo_model_path=model_path,
        yolo_config_path=config_path,
    )

    settings.validate_runtime()


def test_ultralytics_settings_reject_unknown_region_kind(tmp_path: Path) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"trusted-test-checkpoint")
    config_path = tmp_path / "model.yaml"
    config_path.write_text("names:\n  0: qiwu\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        yolo_adapter="ultralytics",
        yolo_model_path=model_path,
        yolo_config_path=config_path,
        yolo_class_mapping={"qiwu": "unsupported-kind"},
    )

    with pytest.raises(ValueError, match="不支持的区域类型"):
        settings.validate_runtime()
