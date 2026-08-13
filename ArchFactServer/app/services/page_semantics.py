import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

REFERENCE_INDEX_TITLE_PATTERN = (
    r"(?:插图|附图|图版|彩图|彩版|图片|图表|照片)\s*"
    r"(?:目录|目次|索引)"
    r"|(?:list|table)\s+of\s+(?:figures|plates|illustrations)"
    r"|(?:figure|plate|illustration)\s+index"
)

_reference_index_title = re.compile(REFERENCE_INDEX_TITLE_PATTERN, re.IGNORECASE)
_reference_entry = re.compile(
    r"^\s*(?:附?图|彩图|彩版|图版|"
    r"fig(?:ure)?\.?|plate|illustration)\s*"
    r"[一二三四五六七八九十百零〇\d]+"
    r"(?:\s*[-–—:：.]\s*[A-Za-z一二三四五六七八九十百零〇\d]+)*",
    re.IGNORECASE,
)
_page_locator = re.compile(
    r"^[\s.·…\-–—]*(?:\d{1,4}|[一二三四五六七八九十百零〇]{1,8})[\s.·…]*$"
)
_visual_kinds = {
    "artifact",
    "line_drawing",
    "grave_drawing",
    "color_plate",
    "group",
}


class PageSemantics:
    """Recognize reference-only pages that must not create artifact records."""

    @classmethod
    def has_reference_index_title(cls, text: str) -> bool:
        normalized = unicodedata.normalize("NFKC", text or "")
        compact = re.sub(r"\s+", "", normalized)
        return bool(
            _reference_index_title.search(normalized)
            or _reference_index_title.search(compact)
        )

    @classmethod
    def is_reference_index_text(cls, text: str) -> bool:
        normalized = unicodedata.normalize("NFKC", text or "")
        if cls.has_reference_index_title(normalized):
            return True

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        reference_lines = [line for line in lines if _reference_entry.match(line)]
        if len(reference_lines) < 8:
            return False

        # Continuation pages sometimes omit the "插图目录" heading. A dense run
        # of short figure/plate entries is still an index, while ordinary prose
        # may contain only one or two "见图..." references.
        short_reference_lines = [line for line in reference_lines if len(line) <= 120]
        return len(short_reference_lines) >= 8

    @classmethod
    def mark_prepared_pages(cls, pages: Iterable[dict[str, Any]]) -> set[int]:
        result: set[int] = set()
        for page in pages:
            if cls.is_reference_index_text(str(page.get("text") or "")):
                page["reference_index"] = True
                page["semantic_text_source"] = False
                page["linkage_ocr_enabled"] = True
                result.add(int(page["page_no"]))
        return result

    @classmethod
    def reference_index_pages_from_regions(
        cls,
        regions: Iterable[dict[str, Any]],
    ) -> set[int]:
        by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for region in regions:
            page = region.get("page")
            if isinstance(page, int):
                by_page[page].append(region)

        result: set[int] = set()
        for page, page_regions in by_page.items():
            texts = [
                cls._region_text(region)
                for region in page_regions
                if cls._region_text(region)
            ]
            if any(cls.has_reference_index_title(text) for text in texts):
                result.add(page)
                continue

            reference_entries = [text for text in texts if _reference_entry.match(text)]
            page_locators = [
                region
                for region in page_regions
                if _page_locator.match(cls._region_text(region))
                and cls._left(region.get("bbox")) >= 0.72
            ]
            visual_count = sum(
                str(region.get("kind") or "") in _visual_kinds for region in page_regions
            )
            if (
                len(reference_entries) >= 6
                and len(page_locators) >= 3
                and visual_count == 0
            ):
                result.add(page)
        return result

    @staticmethod
    def _region_text(region: dict[str, Any]) -> str:
        return str(region.get("text") or region.get("ocr_raw_text") or "").strip()

    @staticmethod
    def _left(bbox: Any) -> float:
        if (
            isinstance(bbox, (list, tuple))
            and len(bbox) == 4
            and isinstance(bbox[0], (int, float))
        ):
            return float(bbox[0])
        return 0.0
