"""Probe local CPU, RAM and GPU so OCR/YOLO knobs can follow the machine."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    cpu_count: int
    memory_gb: float
    cuda_available: bool
    cuda_device_count: int
    cuda_device_name: str | None
    cuda_memory_gb: float | None
    mps_available: bool


@dataclass(frozen=True, slots=True)
class RuntimeTune:
    yolo_device: str
    paddle_ocr_workers: int
    paddle_ocr_worker_threads: int
    discovery_ocr_concurrency: int
    page_preparation_batch_size: int


_applied_profile: HardwareProfile | None = None
_applied_tune: RuntimeTune | None = None


def last_hardware_profile() -> HardwareProfile | None:
    return _applied_profile


def last_runtime_tune() -> RuntimeTune | None:
    return _applied_tune


def detect_hardware() -> HardwareProfile:
    cpu_count = max(1, os.cpu_count() or 1)
    memory_gb = _physical_memory_gb()
    cuda_available, cuda_count, cuda_name, cuda_memory_gb = _cuda_info()
    return HardwareProfile(
        cpu_count=cpu_count,
        memory_gb=memory_gb,
        cuda_available=cuda_available,
        cuda_device_count=cuda_count,
        cuda_device_name=cuda_name,
        cuda_memory_gb=cuda_memory_gb,
        mps_available=_mps_available(),
    )


def ocr_worker_cap(ocr_model: str | None) -> int:
    """Heavy 3.x medium/server keeps 2 processes; v4/small/tiny can use 8.

    Medium on CPU is memory-bandwidth bound. Four concurrent processes make
    each page slower than the timeout, then workers restart and reload the
    model. Two processes finish pages faster and keep the same model quality.
    """
    compact = "".join(char for char in (ocr_model or "").casefold() if char.isalnum())
    if any(token in compact for token in ("tiny", "small", "mobile", "v4")):
        return 8
    if any(token in compact for token in ("medium", "server", "v5")):
        return 2
    return 8


def recommend_local_runtime(
    profile: HardwareProfile,
    *,
    ocr_model: str | None = None,
) -> RuntimeTune:
    """Pick OCR process counts and a YOLO device that fit this machine.

    Detection thresholds stay unchanged so moving computers does not change
    extraction quality, only throughput.
    """

    reserve = 2 if profile.cuda_available else 1
    usable_cores = max(2, profile.cpu_count - reserve)
    ram_workers = max(1, int((profile.memory_gb - 3.0) / 1.2))
    worker_cap = ocr_worker_cap(ocr_model)
    if worker_cap >= 8:
        cpu_workers = max(1, usable_cores - 2)
    else:
        cpu_workers = max(1, usable_cores // 2)
    workers = min(worker_cap, ram_workers, cpu_workers)
    threads = min(8, max(1, usable_cores // workers))
    if profile.cpu_count <= 4:
        threads = min(threads, 3)
    discovery = min(4, workers)
    batch = min(16, max(8, workers * 4))
    return RuntimeTune(
        yolo_device=_preferred_yolo_device(profile),
        paddle_ocr_workers=workers,
        paddle_ocr_worker_threads=threads,
        discovery_ocr_concurrency=discovery,
        page_preparation_batch_size=batch,
    )


def apply_hardware_profile(
    settings: Settings,
    profile: HardwareProfile | None = None,
) -> HardwareProfile:
    """Mutate runtime knobs on ``settings`` to match the current computer."""

    global _applied_profile, _applied_tune
    profile = profile or detect_hardware()
    tune = recommend_local_runtime(profile, ocr_model=settings.paddle_ocr_model)
    yolo_device = resolve_yolo_device(
        settings.yolo_device,
        profile,
        auto_tune=settings.hardware_auto_tune,
    )
    if settings.hardware_auto_tune:
        settings.paddle_ocr_workers = tune.paddle_ocr_workers
        settings.paddle_ocr_worker_threads = tune.paddle_ocr_worker_threads
        settings.discovery_ocr_concurrency = tune.discovery_ocr_concurrency
        settings.page_preparation_batch_size = tune.page_preparation_batch_size
        settings.yolo_device = yolo_device
        applied = RuntimeTune(
            yolo_device=yolo_device,
            paddle_ocr_workers=tune.paddle_ocr_workers,
            paddle_ocr_worker_threads=tune.paddle_ocr_worker_threads,
            discovery_ocr_concurrency=tune.discovery_ocr_concurrency,
            page_preparation_batch_size=tune.page_preparation_batch_size,
        )
    else:
        settings.yolo_device = yolo_device
        applied = RuntimeTune(
            yolo_device=yolo_device,
            paddle_ocr_workers=settings.paddle_ocr_workers,
            paddle_ocr_worker_threads=settings.paddle_ocr_worker_threads,
            discovery_ocr_concurrency=settings.discovery_ocr_concurrency,
            page_preparation_batch_size=settings.page_preparation_batch_size,
        )
    _applied_profile = profile
    _applied_tune = applied
    return profile


def resolve_yolo_device(
    requested: str,
    profile: HardwareProfile,
    *,
    auto_tune: bool,
) -> str:
    requested = (requested or "0").strip()
    if profile.cuda_available:
        if auto_tune:
            return "0"
        if requested.lower() in {"cpu", "mps"}:
            return requested.lower()
        return requested
    if profile.mps_available and requested.lower() != "cpu":
        return "mps"
    return "cpu"


def hardware_status_payload() -> dict[str, Any]:
    profile = _applied_profile
    tune = _applied_tune
    return {
        "hardware": asdict(profile) if profile is not None else None,
        "runtime": asdict(tune) if tune is not None else None,
    }


def format_hardware_startup_log(profile: HardwareProfile, settings: Settings) -> str:
    gpu = "none"
    if profile.cuda_available:
        memory = (
            f", {profile.cuda_memory_gb:.1f} GB"
            if profile.cuda_memory_gb is not None
            else ""
        )
        gpu = f"CUDA {profile.cuda_device_name or 'GPU'}{memory}"
    elif profile.mps_available:
        gpu = "Apple MPS"
    return (
        f"Hardware: {profile.cpu_count} cores, {profile.memory_gb:.1f} GB RAM, {gpu}. "
        f"OCR/YOLO tune: workers={settings.paddle_ocr_workers}, "
        f"threads={settings.paddle_ocr_worker_threads}, "
        f"discovery_ocr={settings.discovery_ocr_concurrency}, "
        f"render_batch={settings.page_preparation_batch_size}, "
        f"yolo_device={settings.yolo_device}"
    )


def _preferred_yolo_device(profile: HardwareProfile) -> str:
    if profile.cuda_available:
        return "0"
    if profile.mps_available:
        return "mps"
    return "cpu"


def _physical_memory_gb() -> float:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return max(1.0, status.ullTotalPhys / (1024**3))
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if page_size > 0 and phys_pages > 0:
            return max(1.0, (page_size * phys_pages) / (1024**3))
    except (ValueError, OSError, AttributeError):
        pass
    meminfo = _linux_meminfo_gb()
    if meminfo is not None:
        return meminfo
    return 8.0


def _linux_meminfo_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            return max(1.0, int(parts[1]) / (1024**2))
    return None


def _cuda_info() -> tuple[bool, int, str | None, float | None]:
    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            return (
                True,
                int(torch.cuda.device_count()),
                str(props.name),
                float(props.total_memory) / (1024**3),
            )
    except Exception:
        pass
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return False, 0, None, None
    try:
        completed = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False, 0, None, None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or not lines:
        return False, 0, None, None
    name, _, memory_text = lines[0].partition(",")
    try:
        memory_gb = float(memory_text.strip()) / 1024
    except ValueError:
        memory_gb = None
    return True, len(lines), name.strip() or None, memory_gb


def _mps_available() -> bool:
    try:
        import torch

        backend = getattr(torch.backends, "mps", None)
        return bool(backend is not None and backend.is_available())
    except Exception:
        return False
