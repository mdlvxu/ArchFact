from app.core.config import Settings
from app.core.hardware import (
    HardwareProfile,
    apply_hardware_profile,
    recommend_local_runtime,
    resolve_yolo_device,
)


def _profile(**overrides: object) -> HardwareProfile:
    values: dict[str, object] = {
        "cpu_count": 8,
        "memory_gb": 16.0,
        "cuda_available": False,
        "cuda_device_count": 0,
        "cuda_device_name": None,
        "cuda_memory_gb": None,
        "mps_available": False,
    }
    values.update(overrides)
    return HardwareProfile(**values)  # type: ignore[arg-type]


def test_recommend_keeps_small_laptops_from_oversubscribing() -> None:
    tune = recommend_local_runtime(_profile(cpu_count=4, memory_gb=8.0))

    assert tune.yolo_device == "cpu"
    assert tune.paddle_ocr_workers == 1
    assert tune.paddle_ocr_worker_threads <= 3
    assert tune.discovery_ocr_concurrency == 1


def test_recommend_uses_more_ocr_workers_on_high_core_gpu_machine() -> None:
    tune = recommend_local_runtime(
        _profile(
            cpu_count=16,
            memory_gb=32.0,
            cuda_available=True,
            cuda_device_count=1,
            cuda_device_name="NVIDIA GeForce RTX 4070",
            cuda_memory_gb=12.0,
        )
    )

    assert tune.yolo_device == "0"
    assert tune.paddle_ocr_workers >= 4
    assert tune.page_preparation_batch_size >= 16


def test_yolo_device_falls_back_to_cpu_when_cuda_is_missing() -> None:
    profile = _profile()
    assert resolve_yolo_device("0", profile, auto_tune=True) == "cpu"
    assert resolve_yolo_device("0", profile, auto_tune=False) == "cpu"


def test_yolo_device_uses_gpu_when_auto_tune_sees_cuda() -> None:
    profile = _profile(cuda_available=True, cuda_device_count=1, cuda_device_name="GPU")
    assert resolve_yolo_device("cpu", profile, auto_tune=True) == "0"
    assert resolve_yolo_device("cpu", profile, auto_tune=False) == "cpu"


def test_apply_hardware_profile_overrides_copied_env_counts() -> None:
    settings = Settings(
        _env_file=None,
        hardware_auto_tune=True,
        paddle_ocr_workers=2,
        paddle_ocr_worker_threads=6,
        discovery_ocr_concurrency=2,
        page_preparation_batch_size=8,
        yolo_device="0",
    )
    apply_hardware_profile(
        settings,
        _profile(
            cpu_count=16,
            memory_gb=32.0,
            cuda_available=True,
            cuda_device_count=1,
            cuda_device_name="NVIDIA",
            cuda_memory_gb=12.0,
        ),
    )

    assert settings.yolo_device == "0"
    assert settings.paddle_ocr_workers > 2
    assert settings.page_preparation_batch_size >= 16


def test_apply_hardware_profile_can_be_disabled() -> None:
    settings = Settings(
        _env_file=None,
        hardware_auto_tune=False,
        paddle_ocr_workers=2,
        paddle_ocr_worker_threads=6,
        yolo_device="cpu",
    )
    apply_hardware_profile(
        settings,
        _profile(cuda_available=True, cuda_device_count=1, cuda_device_name="NVIDIA"),
    )

    assert settings.paddle_ocr_workers == 2
    assert settings.yolo_device == "cpu"
