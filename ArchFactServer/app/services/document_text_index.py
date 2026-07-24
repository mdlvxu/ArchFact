import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.page_discovery import PageDiscoveryService


@dataclass(slots=True)
class DocumentTextIndex:
    """Lossless logical view of the OCR blocks belonging to one extraction job."""

    chunks: list[dict[str, Any]]
    artifact_mentions: dict[str, list[str]]


class DocumentTextIndexer:
    """Build an artifact-aware index without replacing the raw OCR source data."""

    provider = "archfact"
    model = "document-ocr-logical-index"
    version = "1"

    _artifact_pattern = re.compile(
        r"(?<![A-Za-z0-9])"
        r"([A-Za-z]{1,6}\s*\d+[A-Za-z]?"
        r"(?:\s*[:\uff1a]\s*[A-Za-z0-9]+)+)",
        re.IGNORECASE,
    )
    _visual_reference_pattern = re.compile(
        r"(?:\u56fe|\u5716|\u5f69\u7248|\u5f69\u56fe|\u5f69\u5716|"
        r"\u56fe\u7248|\u5716\u7248|fig(?:ure)?|plate)"
        r"\s*[\u4e00-\u9fffA-Za-z0-9\-]+"
        r"(?:\s*[-\u2014:：,，、]\s*[\u4e00-\u9fffA-Za-z0-9\-]+)?",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        max_chunk_chars: int = 8000,
        cross_page_prefix_blocks: int = 8,
    ) -> None:
        self._max_chunk_chars = max(500, max_chunk_chars)
        self._cross_page_prefix_blocks = max(0, cross_page_prefix_blocks)

    def build(
        self,
        *,
        job_id: str,
        document_id: str,
        pages: list[dict[str, Any]],
    ) -> DocumentTextIndex:
        groups = self._logical_groups(self._ordered_blocks(pages))
        chunks: list[dict[str, Any]] = []
        mentions: dict[str, list[str]] = {}

        for ordinal, group in enumerate(groups, start=1):
            raw_text = "\n".join(str(block["text"]) for block in group)
            artifact_ids = self._unique(
                artifact_id
                for block in group
                for artifact_id in block.get("artifact_ids", [])
            )
            reference_tokens = sorted(
                {
                    reference
                    for block in group
                    for reference in block.get("reference_tokens", [])
                }
            )
            visual_references = self._unique(
                reference
                for block in group
                for reference in block.get("visual_references", [])
            )
            source_pages = sorted({int(block["page"]) for block in group})
            region_ids = self._unique(
                block.get("region_id") for block in group if block.get("region_id")
            )
            digest = hashlib.sha256(
                (
                    f"{job_id}:{ordinal}:"
                    + "|".join(region_ids)
                    + ":"
                    + self._normalize_text(raw_text)
                ).encode("utf-8")
            ).hexdigest()[:24]
            chunk_id = f"txtchunk_{digest}"
            chunk = {
                "id": chunk_id,
                "ordinal": ordinal,
                "kind": "artifact" if artifact_ids else "context",
                "artifact_ids": artifact_ids,
                "reference_tokens": reference_tokens,
                "visual_references": visual_references,
                "source_pages": source_pages,
                "start_page": source_pages[0],
                "end_page": source_pages[-1],
                "region_ids": region_ids,
                "raw_text": raw_text,
                "normalized_text": self._normalize_text(raw_text),
                "content_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "index_version": self.version,
                "job_id": job_id,
                "document_id": document_id,
            }
            chunks.append(chunk)
            for artifact_id in artifact_ids:
                key = self.normalize_artifact_id(artifact_id)
                mentions.setdefault(key, []).append(chunk_id)

        return DocumentTextIndex(chunks=chunks, artifact_mentions=mentions)

    def enrich_records(
        self,
        records: list[dict[str, Any]],
        index: DocumentTextIndex,
    ) -> list[dict[str, Any]]:
        """Attach only exact-ID document context and its grounded visual references."""

        chunk_by_id = {str(chunk["id"]): chunk for chunk in index.chunks}
        for record in records:
            candidate_ids = self._unique(
                chunk_id
                for artifact_id in self._record_artifact_ids(record)
                for chunk_id in index.artifact_mentions.get(artifact_id, [])
            )
            if not candidate_ids:
                continue

            source_pages = [
                int(page)
                for page in record.get("source_pages", [])
                if isinstance(page, int) or str(page).isdigit()
            ]
            candidates = [chunk_by_id[chunk_id] for chunk_id in candidate_ids]
            nearest = min(
                self._page_distance(source_pages, chunk["source_pages"])
                for chunk in candidates
            )
            selected = [
                chunk
                for chunk in candidates
                if self._page_distance(source_pages, chunk["source_pages"]) == nearest
            ]
            selected.sort(key=lambda item: (int(item["start_page"]), int(item["ordinal"])))

            references = self._unique(
                reference
                for chunk in selected
                for reference in chunk.get("visual_references", [])
            )
            region_ids = self._unique(
                region_id
                for chunk in selected
                for region_id in chunk.get("region_ids", [])
            )
            context_pages = sorted(
                {
                    int(page)
                    for chunk in selected
                    for page in chunk.get("source_pages", [])
                }
            )
            chunk_ids = [str(chunk["id"]) for chunk in selected]
            record["document_context"] = {
                "index_version": self.version,
                "chunk_ids": chunk_ids,
                "source_pages": context_pages,
                "region_ids": region_ids,
                "references": references,
            }
            record["document_chunk_ids"] = chunk_ids
            record["document_context_pages"] = context_pages
            self._merge_visual_hints(record, references)
        return records

    def _ordered_blocks(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in sorted(pages, key=lambda item: int(item["page_no"])):
            page_no = int(page["page_no"])
            for reading_order, source in enumerate(page.get("blocks", [])):
                text = str(source.get("text") or "").strip()
                if not text:
                    continue
                normalized = unicodedata.normalize("NFKC", text)
                result.append(
                    {
                        "page": page_no,
                        "reading_order": reading_order,
                        "region_id": source.get("region_id"),
                        "text": text,
                        "artifact_ids": self.extract_artifact_ids(normalized),
                        "reference_tokens": sorted(
                            PageDiscoveryService.extract_references(text)
                        ),
                        "visual_references": [
                            match.group(0).strip()
                            for match in self._visual_reference_pattern.finditer(normalized)
                        ],
                    }
                )
        return result

    def _logical_groups(self, blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        active_ids: list[str] = []
        current_chars = 0
        cross_page_blocks = 0

        def flush() -> None:
            nonlocal current, active_ids, current_chars, cross_page_blocks
            if current:
                groups.append(current)
            current = []
            active_ids = []
            current_chars = 0
            cross_page_blocks = 0

        for source in blocks:
            block = dict(source)
            artifact_ids = list(block.get("artifact_ids", []))
            block_chars = len(str(block["text"])) + 1
            page_changed = bool(current) and int(block["page"]) != int(current[-1]["page"])

            if artifact_ids:
                flush()
                current = [block]
                active_ids = artifact_ids
                current_chars = block_chars
                continue
            if not current:
                current = [block]
                current_chars = block_chars
                continue
            if page_changed and not active_ids:
                flush()
                current = [block]
                current_chars = block_chars
                continue
            if page_changed:
                cross_page_blocks += 1
                if cross_page_blocks > self._cross_page_prefix_blocks:
                    flush()
                    current = [block]
                    current_chars = block_chars
                    continue
            if current_chars + block_chars > self._max_chunk_chars:
                inherited_ids = list(active_ids)
                flush()
                if inherited_ids:
                    block["artifact_ids"] = inherited_ids
                current = [block]
                active_ids = inherited_ids
                current_chars = block_chars
                continue
            current.append(block)
            current_chars += block_chars

        flush()
        return groups

    @classmethod
    def extract_artifact_ids(cls, text: str) -> list[str]:
        return cls._unique(
            cls.normalize_artifact_id(match.group(1))
            for match in cls._artifact_pattern.finditer(
                unicodedata.normalize("NFKC", text)
            )
        )

    @staticmethod
    def normalize_artifact_id(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).upper()
        normalized = re.sub(r"\s+", "", normalized).replace("\uff1a", ":")
        return normalized.strip(",.;:()[]{}")

    @classmethod
    def _record_artifact_ids(cls, record: dict[str, Any]) -> list[str]:
        linkage = record.get("linkage", {})
        identity = linkage.get("identity", {}) if isinstance(linkage, dict) else {}
        hints = record.get("link_hints", {})
        field = record.get("fields", {}).get("artifact_id", {})
        values: list[Any] = [
            identity.get("artifact_id_normalized") if isinstance(identity, dict) else None,
            identity.get("artifact_id_raw") if isinstance(identity, dict) else None,
            field.get("value") if isinstance(field, dict) else None,
            field.get("raw_value") if isinstance(field, dict) else None,
        ]
        if isinstance(hints, dict):
            values.extend(hints.get("artifact_ids", []))
        return cls._unique(
            cls.normalize_artifact_id(value)
            for value in values
            if cls.normalize_artifact_id(value)
        )

    @staticmethod
    def _merge_visual_hints(record: dict[str, Any], references: list[str]) -> None:
        hints = record.setdefault("link_hints", {})
        if not isinstance(hints, dict):
            return
        figure_refs = list(hints.get("figure_refs", []))
        plate_refs = list(hints.get("plate_refs", []))
        for reference in references:
            normalized = unicodedata.normalize("NFKC", reference).casefold()
            if normalized.startswith(("\u56fe", "\u5716", "fig")):
                figure_refs.append(reference)
            elif normalized.startswith(
                (
                    "\u5f69\u7248",
                    "\u5f69\u56fe",
                    "\u5f69\u5716",
                    "\u56fe\u7248",
                    "\u5716\u7248",
                    "plate",
                )
            ):
                plate_refs.append(reference)
        hints["figure_refs"] = DocumentTextIndexer._unique(figure_refs)
        hints["plate_refs"] = DocumentTextIndexer._unique(plate_refs)

    @staticmethod
    def _page_distance(left: list[int], right: list[int]) -> int:
        if not left or not right:
            return 0
        return min(abs(a - b) for a in left for b in right)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()

    @staticmethod
    def _unique(values: Iterable[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result
