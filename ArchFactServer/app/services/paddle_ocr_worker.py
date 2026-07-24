"""Long-lived JSON-lines worker for the locally installed PaddleOCR 2.x runtime.

The FastAPI process uses Python 3.13 while the existing PaddleOCR environment uses
Python 3.10. Keeping this process boundary avoids importing PaddlePaddle into the
API environment and loads the OCR model only once for the whole extraction job.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any


def _box(points: Any) -> list[int] | None:
    if not isinstance(points, list) or len(points) < 4:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError, IndexError):
        return None
    x1, y1, x2, y2 = round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _recognize(ocr: Any, image_path: str, use_angle_cls: bool) -> list[dict[str, Any]]:
    # PaddleOCR 2.x may emit locale-specific progress text to stdout even with
    # show_log=False. Keep the JSON-lines protocol on stdout clean.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = ocr.ocr(image_path, cls=use_angle_cls)
    if not result:
        return []
    lines = result[0] if isinstance(result, list) else result
    if not isinstance(lines, list):
        return []
    blocks: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            continue
        bbox = _box(line[0])
        payload = line[1]
        if bbox is None or not isinstance(payload, (list, tuple)) or len(payload) < 2:
            continue
        text = str(payload[0] or "").strip()
        try:
            confidence = float(payload[1])
        except (TypeError, ValueError):
            confidence = -1.0
        if text and confidence >= 0:
            blocks.append(
                {
                    "text": text,
                    "confidence": confidence,
                    "bbox_px": bbox,
                }
            )
    blocks.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
    return blocks


def main() -> None:
    # Windows may default stdout to a local code page (for example cp936).
    # The API process always reads UTF-8 JSON lines.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="ch")
    parser.add_argument("--use-angle-cls", action="store_true")
    args = parser.parse_args()

    # Import only inside the worker so the API process never needs PaddlePaddle.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(
            use_angle_cls=args.use_angle_cls,
            lang=args.language,
            show_log=False,
        )
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            image_path = Path(str(request.get("image_path") or ""))
            if not image_path.is_file():
                raise FileNotFoundError(f"OCR image does not exist: {image_path}")
            use_angle_cls = bool(request.get("use_angle_cls", args.use_angle_cls))
            blocks = _recognize(ocr, str(image_path), use_angle_cls)
            response = {"ok": True, "page_no": request.get("page_no"), "blocks": blocks}
        except Exception as exc:  # keep the worker alive for the next page
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
