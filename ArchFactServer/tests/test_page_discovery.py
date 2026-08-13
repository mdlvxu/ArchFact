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


def test_scan_promotes_sustained_color_run_to_linkage_only_plates(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "color-plates.pdf"
    document = fitz.open()
    for page_no in range(1, 6):
        page = document.new_page()
        if 2 <= page_no <= 4:
            page.draw_rect(
                fitz.Rect(40, 60, 555, 760),
                color=(0.1, 0.2, 0.7),
                fill=(0.3, 0.65, 0.85),
            )
        else:
            page.insert_text((72, 72), f"M{page_no}:1 ordinary body text")
    document.save(pdf_path)
    document.close()
    service = PageDiscoveryService(
        Settings(
            _env_file=None,
            discovery_thumbnail_scale=0.15,
            discovery_color_run_min_pages=3,
        ),
        DisabledOcrEngine(),
    )

    result = asyncio.run(service.scan(pdf_path))

    assert [result.pages[index]["page_type"] for index in (1, 2, 3)] == [
        "color_plate",
        "color_plate",
        "color_plate",
    ]
    assert all(
        result.pages[index]["semantic_text_source"] is False
        for index in (1, 2, 3)
    )
    assert result.pages[0]["semantic_text_source"] is True
    assert result.pages[4]["semantic_text_source"] is True
    assert result.pages[2]["classification_reason"] == "sustained_color_plate_sequence"


def test_small_color_stamp_does_not_disable_body_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "body-with-stamp.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "M12:3 ordinary archaeological body text")
    page.draw_rect(
        fitz.Rect(500, 760, 530, 790),
        color=(0.8, 0.0, 0.0),
        fill=(0.9, 0.1, 0.1),
    )
    document.save(pdf_path)
    document.close()
    service = PageDiscoveryService(
        Settings(_env_file=None, discovery_thumbnail_scale=0.2),
        DisabledOcrEngine(),
    )

    page_index = asyncio.run(service.scan(pdf_path)).pages[0]

    assert page_index["page_type"] not in {"color_plate", "color_visual"}
    assert page_index["semantic_text_source"] is True
