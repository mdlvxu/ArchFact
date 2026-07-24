from pathlib import Path

import fitz

from app.core.config import Settings
from app.services.pdf_parser import PdfParser


def create_pdf(path: Path) -> None:
    document = fitz.open()
    first = document.new_page()
    first.insert_text((72, 72), "First archaeological page")
    second = document.new_page()
    second.insert_text((72, 72), "M12:3 gray pottery jar")
    document.save(path)
    document.close()


def test_parse_selected_pdf_page(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    create_pdf(path)

    parser = PdfParser(Settings(app_env="test"))
    import asyncio

    result = asyncio.run(parser.parse(path, [2]))

    assert result.page_count == 2
    assert len(result.pages) == 1
    assert result.pages[0]["page_no"] == 2
    assert "M12:3" in result.pages[0]["text"]
    assert result.pages[0]["blocks"]
    assert result.pages[0]["status"] == "ready"
    assert result.pages[0]["needs_ocr"] is False
