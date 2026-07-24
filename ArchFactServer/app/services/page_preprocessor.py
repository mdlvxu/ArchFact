import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.core.config import Settings
from app.infrastructure.local_image_storage import LocalImageStorage
from app.repositories.mongo_repository import MongoRepository
from app.services.ocr_engine import OcrEngine, OcrPageInput
from app.services.pdf_parser import PdfParser

PreparationProgress = Callable[[int, int, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class PreparedPdf:
    page_count: int
    pages: list[dict[str, Any]]


class PagePreprocessor:
    """Builds reusable page units for OCR, YOLO and semantic extraction."""

    def __init__(
        self,
        *,
        settings: Settings,
        parser: PdfParser,
        repository: MongoRepository,
        image_storage: LocalImageStorage,
        ocr_engine: OcrEngine,
    ) -> None:
        self._settings = settings
        self._parser = parser
        self._repository = repository
        self._image_storage = image_storage
        self.ocr_engine = ocr_engine
        self._ocr_config_hash = hashlib.sha256(
            json.dumps(ocr_engine.config, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    async def prepare(
        self,
        *,
        pdf_path: Path,
        document_id: str,
        selected_pages: list[int] | None,
        on_progress: PreparationProgress | None = None,
    ) -> PreparedPdf:
        parsed = await self._parser.parse(pdf_path, selected_pages)
        total = len(parsed.pages)
        processed = 0
        batch_size = max(1, self._settings.page_preparation_batch_size)
        cached_pages, cached_images = await self._load_cache(document_id)

        for start in range(0, total, batch_size):
            batch = parsed.pages[start : start + batch_size]
            pages_to_render: list[dict[str, Any]] = []
            for page in batch:
                page_no = int(page["page_no"])
                restored = self._restore_cached_preparation(
                    page,
                    cached_pages.get(page_no),
                    cached_images.get(page_no),
                )
                if not restored and page.get("status") != "failed":
                    pages_to_render.append(page)

            render_results = (
                await asyncio.to_thread(
                    self._render_batch_sync,
                    pdf_path,
                    [int(page["page_no"]) for page in pages_to_render],
                )
                if pages_to_render
                else {}
            )
            persist_tasks: list[Awaitable[None]] = []
            for page in pages_to_render:
                page_no = int(page["page_no"])
                render = render_results[page_no]
                if render.get("error"):
                    page["status"] = "failed"
                    page["error"] = render["error"]
                else:
                    persist_tasks.append(self._persist_render(document_id, page, render))
            if persist_tasks:
                await asyncio.gather(*persist_tasks)

            ocr_tasks = [
                self._apply_ocr(page)
                for page in batch
                if page.get("status") != "failed"
                and self._should_apply_ocr(page)
                and not page.get("ocr_cache_hit")
            ]
            if ocr_tasks:
                await asyncio.gather(*ocr_tasks)

            for page in batch:
                processed += 1
                if on_progress is not None:
                    await on_progress(processed, total, page)

        return PreparedPdf(page_count=parsed.page_count, pages=parsed.pages)

    async def _load_cache(
        self,
        document_id: str,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        try:
            pages, images = await asyncio.gather(
                self._repository.list_document_pages(document_id),
                self._repository.list_document_images(document_id),
            )
        except Exception:
            return {}, {}
        return (
            {int(page["page_no"]): page for page in pages},
            {
                int(image["page_no"]): image
                for image in images
                if image.get("image_type") == "page_render"
            },
        )

    def _restore_cached_preparation(
        self,
        page: dict[str, Any],
        cached_page: dict[str, Any] | None,
        cached_image: dict[str, Any] | None,
    ) -> bool:
        if not cached_image or page.get("status") == "failed":
            return False
        if float(cached_image.get("render_scale") or 0) != self._settings.page_render_scale:
            return False
        object_key = str(cached_image.get("storage", {}).get("object_key") or "")
        if not object_key:
            return False
        image_path = self._image_storage.resolve(object_key)
        if not image_path.is_file():
            return False

        page.update(
            image_id=cached_image.get("_id") or cached_image.get("id"),
            image_object_key=object_key,
            image_width=cached_image.get("width"),
            image_height=cached_image.get("height"),
            image_path=str(image_path),
            render_scale=self._settings.page_render_scale,
            render_cache_hit=True,
        )
        if not self._should_apply_ocr(page):
            return True
        if not self._cached_ocr_matches(cached_page):
            return True

        assert cached_page is not None
        ocr_text = str(cached_page.get("ocr_text") or "").strip()
        ocr_blocks = cached_page.get("ocr_blocks") or []
        page.update(
            text=ocr_text,
            ocr_text=ocr_text,
            text_char_count=len(ocr_text),
            blocks=ocr_blocks,
            ocr_blocks=ocr_blocks,
            parse_method="ocr",
            effective_text_source="ocr",
            needs_ocr=False,
            status="ready",
            error=None,
            ocr_attempted=True,
            ocr_status="completed",
            ocr_error=None,
            ocr_provider=self.ocr_engine.provider,
            ocr_model=self.ocr_engine.model,
            ocr_version=self.ocr_engine.version,
            ocr_config_hash=self._ocr_config_hash,
            ocr_cache_hit=True,
            ocr_ms=0,
        )
        return True

    def _cached_ocr_matches(self, cached_page: dict[str, Any] | None) -> bool:
        return bool(
            cached_page
            and cached_page.get("ocr_status") == "completed"
            and cached_page.get("ocr_provider") == self.ocr_engine.provider
            and cached_page.get("ocr_model") == self.ocr_engine.model
            and cached_page.get("ocr_version") == self.ocr_engine.version
            and cached_page.get("ocr_config_hash") == self._ocr_config_hash
            and str(cached_page.get("ocr_text") or "").strip()
            and cached_page.get("ocr_blocks")
        )

    def _should_apply_ocr(self, page: dict[str, Any]) -> bool:
        if not self.ocr_engine.enabled or self._settings.ocr_policy == "disabled":
            return False
        return self._settings.ocr_policy == "all" or bool(page.get("needs_ocr"))

    async def _apply_ocr(self, page: dict[str, Any]) -> None:
        if not self.ocr_engine.enabled:
            return

        started = time.perf_counter()
        page["ocr_attempted"] = True
        page["ocr_status"] = "running"
        try:
            result = await self.ocr_engine.recognize(
                OcrPageInput(
                    page_no=int(page["page_no"]),
                    image_path=Path(page["image_path"]),
                    width=int(page["image_width"]),
                    height=int(page["image_height"]),
                )
            )
        except Exception as exc:
            page["ocr_error"] = str(exc)
            page["ocr_status"] = "failed"
            self._restore_pdf_text_fallback(page)
            return
        finally:
            page["ocr_ms"] = round((time.perf_counter() - started) * 1000)

        if not result.text.strip():
            page["ocr_status"] = "failed"
            self._restore_pdf_text_fallback(page)
            page["ocr_error"] = "OCR 未识别到有效文字"
            return

        ocr_text = result.text.strip()
        page.update(
            text=ocr_text,
            pdf_text=page.get("pdf_text", page.get("text", "")),
            ocr_text=ocr_text,
            text_char_count=len(ocr_text),
            blocks=result.blocks,
            pdf_blocks=page.get("pdf_blocks", []),
            ocr_blocks=result.blocks,
            parse_method="ocr",
            effective_text_source="ocr",
            needs_ocr=False,
            status="ready",
            error=None,
            ocr_error=None,
            ocr_status="completed",
            ocr_provider=self.ocr_engine.provider,
            ocr_model=self.ocr_engine.model,
            ocr_version=self.ocr_engine.version,
            ocr_config_hash=self._ocr_config_hash,
            ocr_cache_hit=False,
        )

    @staticmethod
    def _restore_pdf_text_fallback(page: dict[str, Any]) -> None:
        pdf_text = str(page.get("pdf_text") or "").strip()
        if not pdf_text:
            return
        page.update(
            text=pdf_text,
            blocks=page.get("pdf_blocks", []),
            text_char_count=len(pdf_text),
            parse_method="text",
            effective_text_source="pdf_text_layer",
            needs_ocr=False,
            status="ready",
            error=None,
        )

    async def _persist_render(
        self,
        document_id: str,
        page: dict[str, Any],
        render: dict[str, Any],
    ) -> None:
        page_no = int(page["page_no"])
        content = render["content"]
        image_id = (
            "img_"
            + hashlib.sha256(f"{document_id}:page:{page_no}:render".encode()).hexdigest()[:24]
        )
        object_key = f"documents/{document_id}/pages/{page_no:04d}/rendered/page.png"
        try:
            image_path = await self._image_storage.write(object_key, content)
            image = await self._repository.upsert_document_image(
                {
                    "id": image_id,
                    "document_id": document_id,
                    "page_no": page_no,
                    "image_type": "page_render",
                    "content_type": "image/png",
                    "width": render["width"],
                    "height": render["height"],
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "render_scale": self._settings.page_render_scale,
                    "storage": {"type": "local", "object_key": object_key},
                }
            )
        except Exception as exc:
            page["status"] = "failed"
            page["error"] = f"分页图片保存失败：{exc}"
            return

        page["image_id"] = image["_id"]
        page["image_object_key"] = object_key
        page["image_width"] = render["width"]
        page["image_height"] = render["height"]
        page["image_path"] = str(image_path)
        page["render_scale"] = self._settings.page_render_scale
        page["render_cache_hit"] = False

    def _render_batch_sync(
        self,
        pdf_path: Path,
        page_numbers: list[int],
    ) -> dict[int, dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        try:
            document = fitz.open(pdf_path)
        except Exception as exc:
            return {page_no: {"error": f"分页图片渲染失败：{exc}"} for page_no in page_numbers}

        with document:
            for page_no in page_numbers:
                try:
                    page = document.load_page(page_no - 1)
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(
                            self._settings.page_render_scale,
                            self._settings.page_render_scale,
                        ),
                        alpha=False,
                    )
                    results[page_no] = {
                        "content": pixmap.tobytes("png"),
                        "width": pixmap.width,
                        "height": pixmap.height,
                        "error": None,
                    }
                except Exception as exc:
                    results[page_no] = {"error": f"分页图片渲染失败：{exc}"}
        return results
