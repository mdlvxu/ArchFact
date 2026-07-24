import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.core.config import Settings
from app.core.errors import DomainError


@dataclass(slots=True)
class ParsedPdf:
    page_count: int
    pages: list[dict[str, Any]]


class PdfParser:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def parse(self, path: Path, selected_pages: list[int] | None) -> ParsedPdf:
        return await asyncio.to_thread(self._parse_sync, path, selected_pages)

    def _parse_sync(self, path: Path, selected_pages: list[int] | None) -> ParsedPdf:
        try:
            document = fitz.open(path)
        except Exception as exc:  # PyMuPDF exposes several format-specific exceptions.
            raise DomainError("PDF 文件损坏或无法解析", code=4221, status_code=422) from exc

        with document:
            page_count = document.page_count
            if page_count == 0:
                raise DomainError("PDF 没有可解析页面", code=4222, status_code=422)
            if page_count > self._settings.max_pdf_pages:
                raise DomainError(
                    f"PDF 页数不能超过 {self._settings.max_pdf_pages} 页",
                    code=4223,
                    status_code=422,
                )

            pages = selected_pages or list(range(1, page_count + 1))
            if pages[-1] > page_count:
                raise DomainError("选择的页码超出 PDF 总页数", code=4224, status_code=422)

            parsed_pages = []
            for page_no in pages:
                try:
                    page = document.load_page(page_no - 1)
                    page_width = max(float(page.rect.width), 1.0)
                    page_height = max(float(page.rect.height), 1.0)
                    text = page.get_text("text", sort=True).strip()
                    blocks = []
                    for block in page.get_text("blocks", sort=True):
                        block_text = str(block[4]).strip()
                        if not block_text:
                            continue
                        blocks.append(
                            {
                                "text": block_text,
                                "bbox": [
                                    max(0.0, min(1.0, float(block[0]) / page_width)),
                                    max(0.0, min(1.0, float(block[1]) / page_height)),
                                    max(0.0, min(1.0, float(block[2]) / page_width)),
                                    max(0.0, min(1.0, float(block[3]) / page_height)),
                                ],
                            }
                        )
                    needs_ocr = not bool(text)
                    parsed_pages.append(
                        {
                            "page_no": page_no,
                            "page_width": page_width,
                            "page_height": page_height,
                            "text": text,
                            "pdf_text": text,
                            "ocr_text": "",
                            "text_char_count": len(text),
                            "blocks": blocks,
                            "pdf_blocks": blocks,
                            "ocr_blocks": [],
                            "effective_text_source": "pdf_text_layer" if text else "none",
                            "parse_method": "text" if text else "no_text_layer",
                            "needs_ocr": needs_ocr,
                            "status": "needs_ocr" if needs_ocr else "ready",
                            "error": None,
                        }
                    )
                except Exception as exc:
                    parsed_pages.append(
                        {
                            "page_no": page_no,
                            "page_width": None,
                            "page_height": None,
                            "text": "",
                            "pdf_text": "",
                            "ocr_text": "",
                            "text_char_count": 0,
                            "blocks": [],
                            "pdf_blocks": [],
                            "ocr_blocks": [],
                            "effective_text_source": "none",
                            "parse_method": "failed",
                            "needs_ocr": False,
                            "status": "failed",
                            "error": f"PDF 页面解析失败：{exc}",
                        }
                    )

        return ParsedPdf(page_count=page_count, pages=parsed_pages)
