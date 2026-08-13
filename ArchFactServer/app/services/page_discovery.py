import asyncio
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageFilter

from app.core.config import Settings
from app.core.errors import DomainError
from app.services.ocr_engine import OcrEngine, OcrPageInput


@dataclass(slots=True)
class PageDiscoveryResult:
    page_count: int
    pages: list[dict[str, Any]]
    elapsed_ms: int


@dataclass(slots=True)
class CandidateRecall:
    pages: list[int]
    requested_references: list[str]
    matched_references: list[str]
    unresolved_references: list[str]


class PageDiscoveryService:
    """Build a low-cost whole-document index before expensive extraction."""

    provider = "archfact"
    model = "whole-document-page-discovery"
    version = "2"
    _artifact_pattern = re.compile(
        r"(?<![A-Za-z0-9])([A-Za-z]{1,5}\s*\d+[A-Za-z]?(?:\s*[:：]\s*[A-Za-z0-9]+)+)"
    )
    _figure_pattern = re.compile(
        r"(?:图|圖|fig(?:ure)?)\s*([一二三四五六七八九十百零〇两兩0-9]+)"
        r"(?:\s*[-—–:：]\s*([A-Za-z0-9一二三四五六七八九十]+))?",
        re.IGNORECASE,
    )
    _plate_pattern = re.compile(
        r"(?:彩版|彩图|彩圖|图版|圖版|plate)\s*"
        r"([一二三四五六七八九十百零〇两兩0-9]+)"
        r"(?:\s*[-—–:：]\s*([A-Za-z0-9一二三四五六七八九十]+))?",
        re.IGNORECASE,
    )

    def __init__(self, settings: Settings, ocr_engine: OcrEngine) -> None:
        self.enabled = settings.discovery_enabled
        self._settings = settings
        self._ocr_engine = ocr_engine

    async def scan(self, pdf_path: Path) -> PageDiscoveryResult:
        started = time.perf_counter()
        page_count, pages = await asyncio.to_thread(self._scan_sync, pdf_path)
        return PageDiscoveryResult(
            page_count=page_count,
            pages=pages,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )

    async def enrich_references(
        self,
        *,
        pdf_path: Path,
        pages: list[dict[str, Any]],
        requested_references: set[str],
        requested_pages: set[int],
    ) -> list[dict[str, Any]]:
        unresolved = self._unresolved_references(
            pages,
            requested_references,
            requested_pages,
        )
        if not unresolved or not self._ocr_engine.enabled:
            return pages

        candidates = self._ocr_candidates(pages, unresolved, requested_pages)
        max_pages = self._settings.discovery_ocr_max_pages
        if max_pages > 0:
            candidates = candidates[:max_pages]
        batch_size = max(1, self._settings.discovery_ocr_concurrency)

        with tempfile.TemporaryDirectory(prefix="archfact-discovery-") as temp_dir:
            root = Path(temp_dir)
            for start in range(0, len(candidates), batch_size):
                batch = candidates[start : start + batch_size]
                rendered = await asyncio.to_thread(
                    self._render_discovery_pages_sync,
                    pdf_path,
                    [int(page["page_no"]) for page in batch],
                    root,
                )
                await asyncio.gather(
                    *(
                        self._ocr_discovery_page(page, rendered.get(int(page["page_no"])))
                        for page in batch
                    )
                )
                unresolved = self._unresolved_references(
                    pages,
                    requested_references,
                    requested_pages,
                )
                if not unresolved:
                    break
        self._refine_page_types(pages)
        return pages

    def references_from_pages(self, pages: list[dict[str, Any]]) -> set[str]:
        references: set[str] = set()
        for page in pages:
            references.update(self.extract_references(str(page.get("text") or "")))
        return references

    def recall(
        self,
        *,
        pages: list[dict[str, Any]],
        requested_references: set[str],
        requested_pages: set[int],
    ) -> CandidateRecall:
        scored: list[tuple[float, int, set[str]]] = []
        matched_references: set[str] = set()
        for page in pages:
            page_no = int(page["page_no"])
            if page_no in requested_pages:
                continue
            page_references = set(page.get("references", []))
            matches = {
                reference
                for reference in requested_references
                if self._reference_candidate_allowed(reference, page)
                if any(
                    self._references_compatible(reference, candidate)
                    for candidate in page_references
                )
            }
            if not matches:
                continue
            page_type = str(page.get("page_type", "document"))
            type_bonus = {
                "color_plate": 0.35,
                "color_visual": 0.3,
                "monochrome_visual": 0.25,
                "mixed_visual": 0.2,
            }.get(page_type, 0.0)
            exact_count = sum(reference in page_references for reference in matches)
            score = len(matches) + exact_count * 0.5 + type_bonus
            scored.append((score, page_no, matches))

        scored.sort(key=lambda item: (-item[0], item[1]))
        recalled_pages: list[int] = []
        for _, page_no, matches in scored[: self._settings.discovery_max_recalled_pages]:
            recalled_pages.append(page_no)
            matched_references.update(matches)
        return CandidateRecall(
            pages=sorted(recalled_pages),
            requested_references=sorted(requested_references),
            matched_references=sorted(matched_references),
            unresolved_references=sorted(requested_references - matched_references),
        )

    @classmethod
    def extract_references(cls, text: str) -> set[str]:
        references: set[str] = set()
        for match in cls._artifact_pattern.finditer(text):
            value = cls._normalize_identifier(match.group(1))
            if len(value) >= 3:
                references.add(f"artifact:{value}")
        for match in cls._figure_pattern.finditer(text):
            number = cls._normalize_number(match.group(1))
            item = cls._normalize_number(match.group(2))
            if number:
                references.add(f"figure:{number}" + (f":{item}" if item else ""))
        for match in cls._plate_pattern.finditer(text):
            number = cls._normalize_number(match.group(1))
            item = cls._normalize_number(match.group(2))
            if number:
                references.add(f"plate:{number}" + (f":{item}" if item else ""))
        return references

    def _scan_sync(self, pdf_path: Path) -> tuple[int, list[dict[str, Any]]]:
        try:
            document = fitz.open(pdf_path)
        except Exception as exc:
            raise DomainError("PDF 文件损坏或无法建立全文索引", code=5060, status_code=422) from exc

        with document:
            page_count = document.page_count
            if page_count == 0:
                raise DomainError("PDF 没有可索引页面", code=5061, status_code=422)
            if page_count > self._settings.max_pdf_pages:
                raise DomainError(
                    f"PDF 页数不能超过 {self._settings.max_pdf_pages} 页",
                    code=4223,
                    status_code=422,
                )
            pages: list[dict[str, Any]] = []
            for page_index in range(page_count):
                page_no = page_index + 1
                try:
                    page = document.load_page(page_index)
                    text = page.get_text("text", sort=True).strip()
                    image_count = len(page.get_images(full=True))
                    drawing_count = len(page.get_drawings())
                    visual = self._visual_features(page)
                    references = self.extract_references(text)
                    classification = self._classify_page(
                        text=text,
                        references=references,
                        image_count=image_count,
                        **visual,
                    )
                    pages.append(
                        {
                            "page_no": page_no,
                            "index_version": self.version,
                            "classifier_version": self.version,
                            "has_text_layer": bool(text),
                            "text_char_count": len(text),
                            "text_preview": text[: self._settings.discovery_text_preview_chars],
                            "image_count": image_count,
                            "drawing_count": drawing_count,
                            **visual,
                            **classification,
                            "references": sorted(references),
                            "discovery_ocr_attempted": False,
                            "discovery_ocr_status": "not_requested",
                            "discovery_ocr_text_preview": "",
                            "error": None,
                        }
                    )
                except Exception as exc:
                    pages.append(
                        {
                            "page_no": page_no,
                            "index_version": self.version,
                            "has_text_layer": False,
                            "text_char_count": 0,
                            "text_preview": "",
                            "image_count": 0,
                            "drawing_count": 0,
                            "color_ratio": 0.0,
                            "foreground_color_ratio": 0.0,
                            "color_tile_ratio": 0.0,
                            "chroma_p95": 0.0,
                            "dark_ratio": 0.0,
                            "edge_ratio": 0.0,
                            "visual_score": 0.0,
                            "page_type": "unknown",
                            "raw_page_type": "unknown",
                            "classification_confidence": 0.0,
                            "classification_reason": "page_scan_failed",
                            "semantic_text_source": True,
                            "linkage_ocr_enabled": True,
                            "visual_detection_enabled": True,
                            "references": [],
                            "discovery_ocr_attempted": False,
                            "discovery_ocr_status": "failed",
                            "discovery_ocr_text_preview": "",
                            "error": str(exc),
                        }
                    )
        self._refine_page_types(pages)
        return page_count, pages

    def _visual_features(self, page: fitz.Page) -> dict[str, float]:
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(
                self._settings.discovery_thumbnail_scale,
                self._settings.discovery_thumbnail_scale,
            ),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image.thumbnail((180, 240))
        pixels = list(image.get_flattened_data())
        total = max(len(pixels), 1)
        chroma_threshold = self._settings.discovery_color_chroma_threshold
        saturation_threshold = self._settings.discovery_color_saturation_threshold
        color_mask: list[bool] = []
        chroma_values: list[int] = []
        foreground_pixels = 0
        color_pixels = 0
        for pixel in pixels:
            maximum = max(pixel)
            minimum = min(pixel)
            chroma = maximum - minimum
            saturation = chroma / max(maximum, 1)
            foreground = minimum < 245 or chroma >= chroma_threshold
            is_color = (
                foreground
                and 24 <= maximum <= 252
                and chroma >= chroma_threshold
                and saturation >= saturation_threshold
            )
            foreground_pixels += int(foreground)
            color_pixels += int(is_color)
            color_mask.append(is_color)
            chroma_values.append(chroma)
        gray = image.convert("L")
        gray_pixels = list(gray.get_flattened_data())
        dark_pixels = sum(value < 220 for value in gray_pixels)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_pixels = sum(value > 28 for value in edges.get_flattened_data())
        color_ratio = color_pixels / total
        foreground_color_ratio = color_pixels / max(foreground_pixels, 1)
        sorted_chroma = sorted(chroma_values)
        chroma_p95 = sorted_chroma[min(len(sorted_chroma) - 1, int(total * 0.95))]
        tile_columns = 6
        tile_rows = 8
        colored_tiles = 0
        for tile_y in range(tile_rows):
            top = tile_y * image.height // tile_rows
            bottom = max(top + 1, (tile_y + 1) * image.height // tile_rows)
            for tile_x in range(tile_columns):
                left = tile_x * image.width // tile_columns
                right = max(left + 1, (tile_x + 1) * image.width // tile_columns)
                tile_total = max((right - left) * (bottom - top), 1)
                tile_colors = sum(
                    color_mask[y * image.width + x]
                    for y in range(top, bottom)
                    for x in range(left, right)
                )
                colored_tiles += int(tile_colors / tile_total >= 0.03)
        color_tile_ratio = colored_tiles / (tile_columns * tile_rows)
        dark_ratio = dark_pixels / total
        edge_ratio = edge_pixels / total
        return {
            "color_ratio": round(color_ratio, 5),
            "foreground_color_ratio": round(foreground_color_ratio, 5),
            "color_tile_ratio": round(color_tile_ratio, 5),
            "chroma_p95": round(float(chroma_p95), 2),
            "dark_ratio": round(dark_ratio, 5),
            "edge_ratio": round(edge_ratio, 5),
            "visual_score": round(
                color_ratio * 4
                + color_tile_ratio * 1.5
                + edge_ratio * 2
                + dark_ratio,
                5,
            ),
        }

    def _classify_page(
        self,
        *,
        text: str,
        references: set[str],
        image_count: int,
        color_ratio: float,
        foreground_color_ratio: float,
        color_tile_ratio: float,
        chroma_p95: float,
        dark_ratio: float,
        edge_ratio: float,
        visual_score: float,
    ) -> dict[str, Any]:
        del visual_score
        has_plate_reference = any(reference.startswith("plate:") for reference in references)
        has_figure_reference = any(reference.startswith("figure:") for reference in references)
        color_threshold = self._settings.discovery_color_ratio_threshold
        tile_threshold = self._settings.discovery_color_tile_ratio_threshold
        is_color_visual = (
            color_ratio >= color_threshold
            and (
                color_tile_ratio >= tile_threshold
                or foreground_color_ratio >= 0.10
                or chroma_p95 >= self._settings.discovery_color_chroma_threshold * 1.5
            )
        )
        if is_color_visual:
            page_type = (
                "color_plate"
                if has_plate_reference
                else "mixed_visual"
                if len(text) >= 500
                else "color_visual"
            )
            margin = min(
                color_ratio / max(color_threshold, 0.0001),
                max(
                    color_tile_ratio / max(tile_threshold, 0.0001),
                    foreground_color_ratio / 0.10,
                ),
            )
            confidence = min(0.99, 0.68 + max(0.0, margin - 1.0) * 0.12)
            return self._classification(
                page_type,
                confidence=confidence,
                reason=(
                    "plate_reference_and_chromatic_content"
                    if has_plate_reference
                    else "dense_text_with_chromatic_content"
                    if page_type == "mixed_visual"
                    else "distributed_chromatic_content"
                ),
            )
        if has_plate_reference and image_count:
            return self._classification(
                "mixed_visual",
                confidence=0.72,
                reason="plate_reference_with_embedded_image",
            )
        if has_figure_reference and (image_count or (len(text) <= 400 and edge_ratio >= 0.025)):
            return self._classification(
                "mixed_visual",
                confidence=0.72,
                reason="figure_reference_with_visual_content",
            )
        if image_count and not text and dark_ratio >= 0.004:
            return self._classification(
                "monochrome_visual",
                confidence=0.78,
                reason="image_only_non_chromatic_page",
            )
        if 0.004 <= dark_ratio and edge_ratio >= 0.018:
            return self._classification(
                "monochrome_visual",
                confidence=0.7,
                reason="edge_dense_non_chromatic_content",
            )
        if not text and dark_ratio < 0.004:
            return self._classification("blank", confidence=0.98, reason="near_empty_page")
        return self._classification("document", confidence=0.68, reason="textual_page")

    @staticmethod
    def _classification(
        page_type: str,
        *,
        confidence: float,
        reason: str,
    ) -> dict[str, Any]:
        semantic_text_source = page_type not in {
            "blank",
            "color_plate",
            "color_visual",
        }
        return {
            "page_type": page_type,
            "raw_page_type": page_type,
            "classification_confidence": round(confidence, 4),
            "classification_reason": reason,
            "semantic_text_source": semantic_text_source,
            "linkage_ocr_enabled": page_type != "blank",
            "visual_detection_enabled": page_type != "blank",
        }

    def _refine_page_types(self, pages: list[dict[str, Any]]) -> None:
        """Promote sustained color runs to plates without treating every color page as one."""

        run: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal run
            if len(run) >= self._settings.discovery_color_run_min_pages:
                for page in run:
                    if page.get("page_type") == "color_visual":
                        page["page_type"] = "color_plate"
                        page["classification_confidence"] = max(
                            float(page.get("classification_confidence") or 0.0),
                            0.82,
                        )
                        page["classification_reason"] = "sustained_color_plate_sequence"
                        page["semantic_text_source"] = False
            run = []

        for page in sorted(pages, key=lambda item: int(item["page_no"])):
            if page.get("page_type") in {"color_visual", "color_plate"}:
                run.append(page)
            else:
                flush()
        flush()

    def _ocr_candidates(
        self,
        pages: list[dict[str, Any]],
        unresolved: set[str],
        requested_pages: set[int],
    ) -> list[dict[str, Any]]:
        wants_plate = any(reference.startswith("plate:") for reference in unresolved)
        wants_figure = any(reference.startswith("figure:") for reference in unresolved)
        wants_artifact = any(reference.startswith("artifact:") for reference in unresolved)
        candidates: list[tuple[float, dict[str, Any]]] = []
        for page in pages:
            if int(page["page_no"]) in requested_pages or page.get("discovery_ocr_attempted"):
                continue
            page_type = str(page.get("page_type", "document"))
            priority = 0.0
            if wants_plate and page_type == "color_plate":
                priority += 8.0
            if wants_plate and page_type == "color_visual":
                priority += 5.0
            if wants_figure and page_type in {"monochrome_visual", "mixed_visual"}:
                priority += 6.0
            if wants_artifact and page_type in {
                "color_plate",
                "monochrome_visual",
                "mixed_visual",
            }:
                priority += 3.0
            if priority <= 0:
                continue
            priority += float(page.get("visual_score", 0))
            candidates.append((priority, page))
        candidates.sort(key=lambda item: (-item[0], int(item[1]["page_no"])))
        return [page for _, page in candidates]

    async def _ocr_discovery_page(
        self,
        page: dict[str, Any],
        rendered: dict[str, Any] | None,
    ) -> None:
        page["discovery_ocr_attempted"] = True
        if not rendered or rendered.get("error"):
            page["discovery_ocr_status"] = "failed"
            page["error"] = (rendered or {}).get("error", "低分辨率页面渲染失败")
            return
        try:
            result = await self._ocr_engine.recognize(
                OcrPageInput(
                    page_no=int(page["page_no"]),
                    image_path=Path(rendered["path"]),
                    width=int(rendered["width"]),
                    height=int(rendered["height"]),
                    segmentation_mode=11,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            page["discovery_ocr_status"] = "failed"
            page["error"] = str(exc)
            return
        text = result.text.strip()
        page["discovery_ocr_status"] = "completed" if text else "empty"
        page["discovery_ocr_text_preview"] = text[: self._settings.discovery_text_preview_chars]
        page["references"] = sorted(set(page.get("references", [])) | self.extract_references(text))
        if (
            page.get("page_type") == "color_visual"
            and any(
                str(reference).startswith("plate:")
                for reference in page.get("references", [])
            )
        ):
            page["page_type"] = "color_plate"
            page["classification_confidence"] = max(
                float(page.get("classification_confidence") or 0.0),
                0.9,
            )
            page["classification_reason"] = "ocr_plate_reference_on_color_page"
            page["semantic_text_source"] = False

    def _render_discovery_pages_sync(
        self,
        pdf_path: Path,
        page_numbers: list[int],
        output_root: Path,
    ) -> dict[int, dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        try:
            document = fitz.open(pdf_path)
        except Exception as exc:
            return {page_no: {"error": str(exc)} for page_no in page_numbers}
        with document:
            for page_no in page_numbers:
                try:
                    pixmap = document.load_page(page_no - 1).get_pixmap(
                        matrix=fitz.Matrix(
                            self._settings.discovery_ocr_render_scale,
                            self._settings.discovery_ocr_render_scale,
                        ),
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )
                    path = output_root / f"page-{page_no:04d}.png"
                    pixmap.save(path)
                    results[page_no] = {
                        "path": str(path),
                        "width": pixmap.width,
                        "height": pixmap.height,
                        "error": None,
                    }
                except Exception as exc:
                    results[page_no] = {"error": str(exc)}
        return results

    @classmethod
    def _unresolved_references(
        cls,
        pages: list[dict[str, Any]],
        requested_references: set[str],
        requested_pages: set[int],
    ) -> set[str]:
        candidate_references = {
            (reference, str(page.get("page_type", "document")))
            for page in pages
            if int(page["page_no"]) not in requested_pages
            for reference in page.get("references", [])
        }
        return {
            reference
            for reference in requested_references
            if not any(
                cls._reference_type_allowed(reference, page_type)
                and cls._references_compatible(reference, candidate)
                for candidate, page_type in candidate_references
            )
        }

    @classmethod
    def _reference_candidate_allowed(
        cls,
        reference: str,
        page: dict[str, Any],
    ) -> bool:
        return cls._reference_type_allowed(
            reference,
            str(page.get("page_type", "document")),
        )

    @staticmethod
    def _reference_type_allowed(reference: str, page_type: str) -> bool:
        if reference.startswith(("figure:", "plate:")):
            return page_type in {
                "color_plate",
                "color_visual",
                "monochrome_visual",
                "mixed_visual",
            }
        return True

    @staticmethod
    def _references_compatible(left: str, right: str) -> bool:
        if left == right:
            return True
        left_parts = left.split(":")
        right_parts = right.split(":")
        if len(left_parts) < 2 or len(right_parts) < 2:
            return False
        if left_parts[:2] != right_parts[:2]:
            return False
        if left_parts[0] == "artifact":
            return False
        return len(left_parts) == 2 or len(right_parts) == 2

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    @classmethod
    def _normalize_number(cls, value: str | None) -> str:
        if not value:
            return ""
        normalized = unicodedata.normalize("NFKC", value).strip().casefold()
        if normalized.isdigit():
            return str(int(normalized))
        chinese = cls._chinese_number(normalized)
        return str(chinese) if chinese is not None else cls._normalize_identifier(normalized)

    @staticmethod
    def _chinese_number(value: str) -> int | None:
        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "兩": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if all(character in digits for character in value):
            return int("".join(str(digits[character]) for character in value))
        total = 0
        current = 0
        recognized = False
        for character in value:
            if character in digits:
                current = digits[character]
                recognized = True
            elif character == "十":
                total += (current or 1) * 10
                current = 0
                recognized = True
            elif character == "百":
                total += (current or 1) * 100
                current = 0
                recognized = True
            else:
                return None
        return total + current if recognized else None
