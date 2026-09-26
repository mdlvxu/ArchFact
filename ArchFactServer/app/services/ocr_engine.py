import asyncio
import csv
import io
import json
import os
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings
from app.core.errors import DomainError


@dataclass(frozen=True, slots=True)
class OcrPageInput:
    page_no: int
    image_path: Path
    width: int
    height: int
    segmentation_mode: int | None = None
    timeout_seconds: float | None = None
    max_side: int | None = None


@dataclass(slots=True)
class OcrPageResult:
    text: str
    blocks: list[dict[str, Any]]


class OcrEngine(Protocol):
    enabled: bool
    provider: str
    model: str
    version: str
    config: dict[str, Any]

    async def recognize(self, page: OcrPageInput) -> OcrPageResult: ...


class DisabledOcrEngine:
    enabled = False
    provider = "archfact"
    model = "disabled-ocr"
    version = "1"
    config: dict[str, Any] = {"adapter": "disabled"}

    async def recognize(self, page: OcrPageInput) -> OcrPageResult:
        del page
        return OcrPageResult(text="", blocks=[])


class TesseractOcrEngine:
    """Runs the locally installed Tesseract CLI behind the stable OCR boundary."""

    enabled = True
    provider = "tesseract"
    model = "tesseract-ocr"
    version = "5"

    def __init__(
        self,
        *,
        command: str,
        languages: str,
        page_segmentation_mode: int,
        min_confidence: float,
    ) -> None:
        executable = shutil.which(command)
        if executable is None:
            candidate = Path(command)
            if not candidate.is_file():
                raise ValueError(f"Tesseract 可执行文件不存在：{command}")
            executable = str(candidate)

        self._command = executable
        self._languages = languages
        self._page_segmentation_mode = page_segmentation_mode
        self._min_confidence = min_confidence
        self.config = {
            "adapter": "tesseract",
            "languages": languages,
            "page_segmentation_mode": page_segmentation_mode,
            "min_confidence": min_confidence,
        }

    async def recognize(self, page: OcrPageInput) -> OcrPageResult:
        if not page.image_path.is_file():
            raise DomainError(
                f"第 {page.page_no} 页 OCR 输入图片不存在：{page.image_path}",
                code=5044,
                status_code=500,
            )
        try:
            process = await asyncio.create_subprocess_exec(
                self._command,
                str(page.image_path),
                "stdout",
                "-l",
                self._languages,
                "--psm",
                str(
                    self._page_segmentation_mode
                    if page.segmentation_mode is None
                    else page.segmentation_mode
                ),
                "tsv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                ),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=180 if page.timeout_seconds is None else page.timeout_seconds,
                )
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            except TimeoutError as exc:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise DomainError(
                    f"第 {page.page_no} 页 Tesseract 执行超时",
                    code=5046,
                    status_code=500,
                ) from exc
            if process.returncode != 0:
                error = stderr.decode("utf-8", errors="replace").strip()
                raise DomainError(
                    f"第 {page.page_no} 页 Tesseract 执行失败：{error or process.returncode}",
                    code=5046,
                    status_code=500,
                )
            output = stdout.decode("utf-8-sig", errors="replace")
            return self._parse_tsv(output, page.width, page.height)
        except DomainError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise DomainError(
                f"第 {page.page_no} 页 OCR 识别失败：{exc}",
                code=5045,
                status_code=500,
            ) from exc

    def _parse_tsv(self, content: str, width: int, height: int) -> OcrPageResult:
        grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
        reader = csv.DictReader(io.StringIO(content), delimiter="\t")
        for row in reader:
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            confidence = self._to_float(row.get("conf"), default=-1.0)
            if confidence < self._min_confidence * 100:
                continue
            try:
                key = (
                    int(row.get("block_num") or 0),
                    int(row.get("par_num") or 0),
                    int(row.get("line_num") or 0),
                )
                left = int(row.get("left") or 0)
                top = int(row.get("top") or 0)
                item_width = int(row.get("width") or 0)
                item_height = int(row.get("height") or 0)
            except ValueError:
                continue
            if item_width <= 0 or item_height <= 0:
                continue
            grouped.setdefault(key, []).append(
                {
                    "text": text,
                    "confidence": confidence / 100,
                    "bbox_px": [left, top, left + item_width, top + item_height],
                }
            )

        blocks: list[dict[str, Any]] = []
        for items in grouped.values():
            items.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
            line_text = self._join_tokens([item["text"] for item in items])
            x1 = min(item["bbox_px"][0] for item in items)
            y1 = min(item["bbox_px"][1] for item in items)
            x2 = max(item["bbox_px"][2] for item in items)
            y2 = max(item["bbox_px"][3] for item in items)
            blocks.append(
                {
                    "text": line_text,
                    "bbox": [
                        self._normalize(x1, width),
                        self._normalize(y1, height),
                        self._normalize(x2, width),
                        self._normalize(y2, height),
                    ],
                    "bbox_px": [x1, y1, x2, y2],
                    "confidence": sum(item["confidence"] for item in items) / len(items),
                    "source": "tesseract_ocr",
                }
            )
        blocks.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
        return OcrPageResult(
            text="\n".join(block["text"] for block in blocks),
            blocks=blocks,
        )

    @staticmethod
    def _join_tokens(tokens: list[str]) -> str:
        result = ""
        for token in tokens:
            if result and result[-1].isascii() and token[0].isascii():
                result += " "
            result += token
        return result

    @staticmethod
    def _to_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize(value: int, dimension: int) -> float:
        return max(0.0, min(1.0, value / max(dimension, 1)))


class PaddleOcrEngine:
    """Keeps PaddleOCR in an isolated, long-lived Python worker process."""

    enabled = True
    provider = "paddleocr"

    def __init__(
        self,
        *,
        python_command: Path,
        worker_path: Path,
        language: str,
        use_angle_cls: bool,
        api_version: str,
        model: str,
        version: str,
        min_confidence: float,
        timeout_seconds: float,
        worker_count: int,
        worker_cpu_threads: int,
        max_side: int = 1600,
        fallback_max_side: int = 960,
    ) -> None:
        if not python_command.is_file():
            raise ValueError(f"PaddleOCR Python 可执行文件不存在：{python_command}")
        if not worker_path.is_file():
            raise ValueError(f"PaddleOCR 工作进程脚本不存在：{worker_path}")
        self.model = model
        self.version = version
        self._python_command = python_command.resolve()
        self._worker_path = worker_path.resolve()
        self._language = language
        self._use_angle_cls = use_angle_cls
        self._api_version = api_version
        self._min_confidence = min_confidence
        self._timeout_seconds = timeout_seconds
        self._max_side = max(640, max_side)
        self._fallback_max_side = max(640, min(self._max_side, fallback_max_side))
        self._worker_count = max(1, worker_count)
        self._worker_cpu_threads = max(1, worker_cpu_threads)
        self._processes: list[asyncio.subprocess.Process | None] = [
            None for _ in range(self._worker_count)
        ]
        self._stderr_tasks: list[asyncio.Task[None] | None] = [
            None for _ in range(self._worker_count)
        ]
        self._stderr_tails: list[deque[str]] = [
            deque(maxlen=20) for _ in range(self._worker_count)
        ]
        self._available_workers: asyncio.Queue[int] = asyncio.Queue()
        for worker_id in range(self._worker_count):
            self._available_workers.put_nowait(worker_id)
        self._close_lock = asyncio.Lock()
        self._ensure_locks = [asyncio.Lock() for _ in range(self._worker_count)]
        self.config = {
            "adapter": "paddle",
            "language": language,
            "use_angle_cls": use_angle_cls,
            "api_version": api_version,
            "model": model,
            "version": version,
            "min_confidence": min_confidence,
            "timeout_seconds": timeout_seconds,
            "max_side": self._max_side,
            "worker_count": self._worker_count,
            "worker_cpu_threads": self._worker_cpu_threads,
        }

    async def warmup(self) -> None:
        """Load models in every worker before the first page timeout starts."""
        await asyncio.gather(
            *(self._ensure_process(worker_id) for worker_id in range(self._worker_count))
        )

    async def recognize(self, page: OcrPageInput) -> OcrPageResult:
        if not page.image_path.is_file():
            raise DomainError(
                f"第 {page.page_no} 页 OCR 输入图片不存在：{page.image_path}",
                code=5044,
                status_code=500,
            )
        last_error: DomainError | None = None
        max_sides = self._max_sides_for(page)
        for index, max_side in enumerate(max_sides):
            attempt = replace(page, max_side=max_side)
            try:
                return await self._recognize_with_crash_retry(attempt)
            except DomainError as exc:
                last_error = exc
                if exc.code != 5046 or index == len(max_sides) - 1:
                    raise
        assert last_error is not None
        raise last_error

    def _max_sides_for(self, page: OcrPageInput) -> list[int]:
        primary = self._max_side if page.max_side is None else page.max_side
        sides = [primary]
        # Crop OCR already uses a short timeout and a small image.
        if page.timeout_seconds is not None:
            return sides
        if self._fallback_max_side < primary:
            sides.append(self._fallback_max_side)
        return sides

    async def _recognize_with_crash_retry(self, page: OcrPageInput) -> OcrPageResult:
        last_error: DomainError | None = None
        for attempt in range(2):
            try:
                return await self._recognize_once(page)
            except DomainError as exc:
                last_error = exc
                # Worker crashes (OOM / killed sibling) are worth one retry on a
                # fresh process. Timeouts already spent the full budget.
                if exc.code != 5045 or attempt == 1:
                    raise
        assert last_error is not None
        raise last_error

    async def _recognize_once(self, page: OcrPageInput) -> OcrPageResult:
        worker_id = await self._available_workers.get()
        try:
            process = await self._ensure_process(worker_id)
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("PaddleOCR 工作进程通信通道不可用")
            request = {
                "page_no": page.page_no,
                "image_path": str(page.image_path.resolve()),
                "use_angle_cls": self._use_angle_cls,
                "max_side": page.max_side if page.max_side is not None else self._max_side,
            }
            process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()
            payload = await self._read_json_response(
                process,
                worker_id,
                timeout_seconds=(
                    self._timeout_seconds
                    if page.timeout_seconds is None
                    else page.timeout_seconds
                ),
            )
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("error") or "PaddleOCR 识别失败"))
            blocks = self._normalize_blocks(
                payload.get("blocks"),
                width=page.width,
                height=page.height,
            )
            return OcrPageResult(
                text="\n".join(block["text"] for block in blocks),
                blocks=blocks,
            )
        except asyncio.CancelledError:
            await self._stop_process(worker_id)
            raise
        except TimeoutError as exc:
            await self._stop_process(worker_id)
            raise DomainError(
                f"第 {page.page_no} 页 PaddleOCR 执行超时",
                code=5046,
                status_code=500,
            ) from exc
        except DomainError:
            raise
        except Exception as exc:
            await self._stop_process(worker_id)
            raise DomainError(
                f"第 {page.page_no} 页 PaddleOCR 识别失败：{exc}",
                code=5045,
                status_code=500,
            ) from exc
        finally:
            self._available_workers.put_nowait(worker_id)

    async def aclose(self) -> None:
        async with self._close_lock:
            await asyncio.gather(
                *(self._stop_process(worker_id) for worker_id in range(self._worker_count))
            )

    async def _ensure_process(self, worker_id: int) -> asyncio.subprocess.Process:
        async with self._ensure_locks[worker_id]:
            process = self._processes[worker_id]
            if process is not None and process.returncode is None:
                return process
            return await self._spawn_process(worker_id)

    async def _spawn_process(self, worker_id: int) -> asyncio.subprocess.Process:
        self._stderr_tails[worker_id].clear()
        worker_environment = dict(os.environ)
        worker_environment.update(
            OMP_NUM_THREADS=str(self._worker_cpu_threads),
            MKL_NUM_THREADS=str(self._worker_cpu_threads),
            PADDLE_PDX_MODEL_SOURCE="bos",
            PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True",
        )
        worker_command = [
            str(self._python_command),
            "-u",
            str(self._worker_path),
            "--language",
            self._language,
            "--api-version",
            self._api_version,
            "--model",
            self.model,
        ]
        if self._use_angle_cls:
            worker_command.append("--use-angle-cls")
        process = await asyncio.create_subprocess_exec(
            *worker_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=worker_environment,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            ),
        )
        self._processes[worker_id] = process
        if process.stderr is not None:
            self._stderr_tasks[worker_id] = asyncio.create_task(
                self._drain_stderr(process.stderr, self._stderr_tails[worker_id])
            )
        try:
            await self._wait_until_ready(process, worker_id)
        except Exception:
            await self._stop_process(worker_id)
            raise
        return process

    async def _wait_until_ready(
        self,
        process: asyncio.subprocess.Process,
        worker_id: int,
    ) -> None:
        payload = await self._read_json_response(
            process,
            worker_id,
            timeout_seconds=max(120.0, self._timeout_seconds),
        )
        if payload.get("ready") is True:
            return
        raise RuntimeError(
            self._worker_error(worker_id, "PaddleOCR 工作进程未进入就绪状态")
        )

    async def _stop_process(self, worker_id: int) -> None:
        process = self._processes[worker_id]
        self._processes[worker_id] = None
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        stderr_task = self._stderr_tasks[worker_id]
        self._stderr_tasks[worker_id] = None
        if stderr_task is not None:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

    async def _drain_stderr(
        self,
        stream: asyncio.StreamReader,
        tail: deque[str],
    ) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                tail.append(message)

    def _worker_error(self, worker_id: int, default: str) -> str:
        tail = self._stderr_tails[worker_id]
        return tail[-1] if tail else default

    async def _read_json_response(
        self,
        process: asyncio.subprocess.Process,
        worker_id: int,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("PaddleOCR 工作进程通信通道不可用")
        deadline = asyncio.get_running_loop().time() + (
            self._timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError()
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            if not line:
                raise RuntimeError(
                    self._worker_error(worker_id, "PaddleOCR 工作进程意外退出")
                )
            text = line.decode("utf-8", errors="replace").strip()
            if not text or text[0] not in "{[":
                # Native paddle/oneDNN noise occasionally leaks onto stdout.
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload

    def _normalize_blocks(
        self,
        raw_blocks: Any,
        *,
        width: int,
        height: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_blocks, list):
            return []
        blocks: list[dict[str, Any]] = []
        for raw in raw_blocks:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            confidence = TesseractOcrEngine._to_float(raw.get("confidence"), default=-1)
            bbox = raw.get("bbox_px")
            if (
                not text
                or confidence < self._min_confidence
                or not isinstance(bbox, list)
                or len(bbox) != 4
            ):
                continue
            try:
                x1, y1, x2, y2 = (int(round(float(value))) for value in bbox)
            except (TypeError, ValueError):
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            blocks.append(
                {
                    "text": text,
                    "bbox": [
                        TesseractOcrEngine._normalize(x1, width),
                        TesseractOcrEngine._normalize(y1, height),
                        TesseractOcrEngine._normalize(x2, width),
                        TesseractOcrEngine._normalize(y2, height),
                    ],
                    "bbox_px": [x1, y1, x2, y2],
                    "confidence": confidence,
                    "source": "paddleocr",
                }
            )
        blocks.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
        return blocks


def build_ocr_engine(settings: Settings) -> OcrEngine:
    if settings.ocr_adapter == "paddle":
        assert settings.paddle_ocr_python is not None
        return PaddleOcrEngine(
            python_command=settings.paddle_ocr_python,
            worker_path=settings.paddle_ocr_worker_path,
            language=settings.paddle_ocr_language,
            use_angle_cls=settings.paddle_ocr_use_angle_cls,
            api_version=settings.paddle_ocr_api_version,
            model=settings.paddle_ocr_model,
            version=settings.paddle_ocr_version,
            min_confidence=settings.ocr_min_confidence,
            timeout_seconds=settings.paddle_ocr_timeout_seconds,
            worker_count=settings.paddle_ocr_workers,
            worker_cpu_threads=settings.paddle_ocr_worker_threads,
            max_side=settings.paddle_ocr_max_side,
        )
    if settings.ocr_adapter == "tesseract":
        return TesseractOcrEngine(
            command=settings.tesseract_command,
            languages=settings.ocr_languages,
            page_segmentation_mode=settings.ocr_page_segmentation_mode,
            min_confidence=settings.ocr_min_confidence,
        )
    return DisabledOcrEngine()
