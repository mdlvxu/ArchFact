"""Download PP-OCRv6_small det/rec into the project and the PaddleX cache.

Prefers Baidu BOS (reachable in China). Copies the same folders into
~/.paddlex/official_models so PaddleOCR 3.x can load them without HuggingFace.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

from paddlex.utils.download import download_and_extract

BOS_BASE = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/"
    "paddle3.0.0"
)
MODELS = ("PP-OCRv6_small_det", "PP-OCRv6_small_rec")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = PROJECT_ROOT / "models" / "paddleocr"
CACHE_DIR = Path.home() / ".paddlex" / "official_models"


def _has_weights(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    return any(
        path.suffix.lower() in {".pdiparams", ".pdmodel", ".json", ".safetensors"}
        for path in directory.iterdir()
        if path.is_file()
    )


def _install(name: str) -> Path:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = PROJECT_DIR / name
    if not _has_weights(dest):
        url = f"{BOS_BASE}/{name}_infer.tar"
        print(f"Downloading {url}", flush=True)
        if dest.exists():
            shutil.rmtree(dest)
        download_and_extract(url, str(PROJECT_DIR), name, overwrite=True)
    if not _has_weights(dest):
        raise FileNotFoundError(f"Downloaded {name} is missing inference weights: {dest}")
    cache_dest = CACHE_DIR / name
    if not _has_weights(cache_dest):
        if cache_dest.exists():
            shutil.rmtree(cache_dest)
        shutil.copytree(dest, cache_dest)
    print(f"Ready {dest}", flush=True)
    for item in sorted(dest.iterdir()):
        size = item.stat().st_size if item.is_file() else 0
        print(f"  {item.name}\t{size}", flush=True)
    return dest


def main() -> int:
    for name in MODELS:
        _install(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
