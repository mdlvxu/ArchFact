"""Long-lived JSON-lines worker for local PaddleOCR 2.x / 3.x runtimes.

The FastAPI process uses Python 3.13 while PaddleOCR stays in an isolated conda
environment (Python 3.10). Keeping this process boundary avoids importing
PaddlePaddle into the API environment and loads the OCR model only once per
worker for the whole extraction job.

Protocol (unchanged): stdin one JSON request per line with image_path; stdout
one UTF-8 JSON response with blocks [{text, confidence, bbox_px}, ...].
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def _box(points: Any) -> list[int] | None:
    if hasattr(points, "tolist"):
        points = points.tolist()
    if not isinstance(points, (list, tuple)) or len(points) < 4:
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


@contextlib.contextmanager
def _silence_stdio() -> Any:
    """Mute Python and native C++ chatter that would corrupt JSON-lines stdout."""
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        saved_out = os.dup(1)
        saved_err = os.dup(2)
        try:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)


def _major_version(version: str) -> int:
    head = version.strip().split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0


def _detect_api_version(requested: str, package_version: str) -> str:
    if requested in {"2", "3"}:
        return requested
    return "3" if _major_version(package_version) >= 3 else "2"


def _result_mapping(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        if "rec_texts" in item or "dt_polys" in item:
            return item
        nested = item.get("res")
        if isinstance(nested, dict):
            return nested
        return item

    json_payload = getattr(item, "json", None)
    if callable(json_payload):
        with contextlib.suppress(Exception):
            json_payload = json_payload()
    if isinstance(json_payload, dict):
        return _result_mapping(json_payload)

    mapped: dict[str, Any] = {}
    for key in ("rec_texts", "rec_scores", "dt_polys", "texts", "scores", "boxes"):
        value = getattr(item, key, None)
        if value is not None:
            mapped[key] = value
    return mapped


def _recognize_v2(ocr: Any, image_path: str, use_angle_cls: bool) -> list[dict[str, Any]]:
    # PaddleOCR 2.x may emit locale-specific progress text to stdout even with
    # show_log=False. Keep the JSON-lines protocol on stdout clean.
    with _silence_stdio():
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


def _recognize_v3(ocr: Any, image_path: str) -> list[dict[str, Any]]:
    with _silence_stdio():
        result = ocr.predict(image_path)
    if not result:
        return []
    pages = result if isinstance(result, list) else [result]
    blocks: list[dict[str, Any]] = []
    for page in pages:
        mapping = _result_mapping(page)
        texts = mapping.get("rec_texts") or mapping.get("texts") or []
        scores = mapping.get("rec_scores") or mapping.get("scores") or []
        polys = mapping.get("dt_polys") or mapping.get("boxes") or []
        if hasattr(texts, "tolist"):
            texts = texts.tolist()
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if hasattr(polys, "tolist"):
            polys = polys.tolist()
        if not isinstance(texts, list):
            continue
        for index, raw_text in enumerate(texts):
            text = str(raw_text or "").strip()
            if not text:
                continue
            confidence = -1.0
            if isinstance(scores, list) and index < len(scores):
                with contextlib.suppress(TypeError, ValueError):
                    confidence = float(scores[index])
            bbox = None
            if isinstance(polys, list) and index < len(polys):
                bbox = _box(polys[index])
            if bbox is None or confidence < 0:
                continue
            blocks.append(
                {
                    "text": text,
                    "confidence": confidence,
                    "bbox_px": bbox,
                }
            )
    blocks.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
    return blocks


def _prepare_ocr_image(image_path: Path, max_side: Any) -> tuple[str, float, Path | None]:
    try:
        limit = int(max_side) if max_side is not None else 0
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return str(image_path), 1.0, None
    try:
        from PIL import Image
    except ImportError:
        return str(image_path), 1.0, None
    with Image.open(image_path) as source:
        image = source.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        if longest <= limit:
            return str(image_path), 1.0, None
        scale = limit / longest
        resized = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.BILINEAR,
        )
        temporary = image_path.with_name(f".{image_path.stem}.ocr{limit}.png")
        resized.save(temporary, format="PNG")
    return str(temporary), scale, temporary


def _scale_blocks(blocks: list[dict[str, Any]], scale: float) -> list[dict[str, Any]]:
    if scale <= 0 or scale == 1.0:
        return blocks
    inverse = 1.0 / scale
    scaled: list[dict[str, Any]] = []
    for block in blocks:
        bbox = block.get("bbox_px")
        if not isinstance(bbox, list) or len(bbox) != 4:
            scaled.append(block)
            continue
        scaled.append(
            {
                **block,
                "bbox_px": [int(round(float(value) * inverse)) for value in bbox],
            }
        )
    return scaled


def _resolve_v3_models(model: str | None) -> tuple[str | None, str | None]:
    compact = "".join(char for char in (model or "").casefold() if char.isalnum())
    if not compact:
        return None, None
    if "tiny" in compact:
        return "PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec"
    if "small" in compact:
        return "PP-OCRv6_small_det", "PP-OCRv6_small_rec"
    if "medium" in compact:
        return "PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"
    if "v4" in compact or "mobile" in compact:
        return "PP-OCRv4_mobile_det", "PP-OCRv4_mobile_rec"
    if "v5" in compact:
        return "PP-OCRv5_server_det", "PP-OCRv5_server_rec"
    if compact in {"ppocrv6", "v6"}:
        return "PP-OCRv6_small_det", "PP-OCRv6_small_rec"
    return None, None


def _server_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bundled_ocr_model_dir(model_name: str, root: Path | None = None) -> Path | None:
    """Use project-local PaddleX inference dirs when they already contain weights."""
    if not model_name:
        return None
    candidate = (root or _server_root()) / "models" / "paddleocr" / model_name
    if not candidate.is_dir():
        return None
    has_weights = any(
        path.suffix.lower() in {".pdiparams", ".pdmodel", ".json", ".safetensors"}
        for path in candidate.iterdir()
        if path.is_file()
    )
    return candidate if has_weights else None


def _v3_ocr_kwargs(
    *,
    language: str,
    use_angle_cls: bool,
    model: str | None = None,
    det_model_dir: str | None = None,
    rec_model_dir: str | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "lang": language,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": use_angle_cls,
    }
    det_model, rec_model = _resolve_v3_models(model)
    if det_model and rec_model:
        kwargs["text_detection_model_name"] = det_model
        kwargs["text_recognition_model_name"] = rec_model
        local_det = det_model_dir or (
            str(path) if (path := bundled_ocr_model_dir(det_model)) is not None else None
        )
        local_rec = rec_model_dir or (
            str(path) if (path := bundled_ocr_model_dir(rec_model)) is not None else None
        )
        if local_det:
            kwargs["text_detection_model_dir"] = local_det
        if local_rec:
            kwargs["text_recognition_model_dir"] = local_rec
    return kwargs


def _build_ocr(
    api_version: str,
    *,
    language: str,
    use_angle_cls: bool,
    model: str | None = None,
    det_model_dir: str | None = None,
    rec_model_dir: str | None = None,
) -> Any:
    from paddleocr import PaddleOCR

    if api_version == "3":
        return PaddleOCR(
            **_v3_ocr_kwargs(
                language=language,
                use_angle_cls=use_angle_cls,
                model=model,
                det_model_dir=det_model_dir,
                rec_model_dir=rec_model_dir,
            )
        )
    return PaddleOCR(
        use_angle_cls=use_angle_cls,
        lang=language,
        show_log=False,
    )


def main() -> None:
    # Windows may default stdout to a local code page (for example cp936).
    # The API process always reads UTF-8 JSON lines.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="ch")
    parser.add_argument("--use-angle-cls", action="store_true")
    parser.add_argument(
        "--api-version",
        choices=("auto", "2", "3"),
        default="auto",
        help="PaddleOCR API family. auto selects from paddleocr.__version__.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="3.x det/rec pair label, e.g. PP-OCRv6_small or PP-OCRv6_medium.",
    )
    parser.add_argument("--det-model-dir", default="", help="Optional local detection weights.")
    parser.add_argument("--rec-model-dir", default="", help="Optional local recognition weights.")
    args = parser.parse_args()

    # Import only inside the worker so the API process never needs PaddlePaddle.
    with _silence_stdio():
        import paddleocr

        package_version = str(getattr(paddleocr, "__version__", "") or "")
        api_version = _detect_api_version(args.api_version, package_version)
        ocr = _build_ocr(
            api_version,
            language=args.language,
            use_angle_cls=args.use_angle_cls,
            model=args.model,
            det_model_dir=args.det_model_dir or None,
            rec_model_dir=args.rec_model_dir or None,
        )
    sys.stdout.write(json.dumps({"ok": True, "ready": True}, ensure_ascii=False) + "\n")
    sys.stdout.flush()

    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            image_path = Path(str(request.get("image_path") or ""))
            if not image_path.is_file():
                raise FileNotFoundError(f"OCR image does not exist: {image_path}")
            ocr_path, scale, temporary = _prepare_ocr_image(image_path, request.get("max_side"))
            try:
                if api_version == "3":
                    blocks = _recognize_v3(ocr, ocr_path)
                else:
                    use_angle_cls = bool(request.get("use_angle_cls", args.use_angle_cls))
                    blocks = _recognize_v2(ocr, ocr_path, use_angle_cls)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            blocks = _scale_blocks(blocks, scale)
            response = {"ok": True, "page_no": request.get("page_no"), "blocks": blocks}
        except Exception as exc:  # keep the worker alive for the next page
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
