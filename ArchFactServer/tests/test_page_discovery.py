import asyncio
from pathlib import Path

import fitz

from app.core.config import Settings
from app.services.ocr_engine import DisabledOcrEngine
from app.services.page_discovery import PageDiscoveryService


def test_reference_extraction_normalizes_figure_plate_and_artifact_ids() -> None:
    references = PageDiscoveryService.extract_references("H125:1 见图六-3，并参见彩版八：2。")

    assert references == {
        "artifact:h1251",
        "figure:6:3",
        "plate:8:2",
    }


def test_recall_matches_references_without_page_distance_limit() -> None:
    service = PageDiscoveryService(
        Settings(_env_file=None, discovery_max_recalled_pages=10),
        DisabledOcrEngine(),
    )
    pages = [
        {
            "page_no": 3,
            "references": ["figure:6"],
            "page_type": "monochrome_visual",
        },
        {
            "page_no": 286,
            "references": ["plate:8:2"],
            "page_type": "color_plate",
        },
        {
            "page_no": 300,
            "references": ["plate:9:1"],
            "page_type": "color_plate",
        },
    ]

    recall = service.recall(
        pages=pages,
        requested_references={"figure:6:3", "plate:8:2"},
        requested_pages={2},
    )

    assert recall.pages == [3, 286]
    assert recall.matched_references == ["figure:6:3", "plate:8:2"]
    assert recall.unresolved_references == []


def test_scan_indexes_text_layer_for_every_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "whole-report.pdf"
    document = fitz.open()
    for page_no in range(1, 13):
        page = document.new_page()
        text = "ordinary report text"
        if page_no == 2:
            text = "H125:1 see Figure6-3"
        elif page_no == 12:
            text = "Figure6 line drawing"
        page.insert_text((72, 72), text)
    document.save(pdf_path)
    document.close()
    service = PageDiscoveryService(
        Settings(_env_file=None, discovery_thumbnail_scale=0.1),
        DisabledOcrEngine(),
    )

    result = asyncio.run(service.scan(pdf_path))

    assert result.page_count == 12
    assert len(result.pages) == 12
    assert "artifact:h1251" in result.pages[1]["references"]
    assert "figure:6:3" in result.pages[1]["references"]
    assert "figure:6" in result.pages[11]["references"]
