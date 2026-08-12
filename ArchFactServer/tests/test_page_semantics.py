from app.services.page_semantics import PageSemantics


def _text(region_id: str, page: int, text: str, bbox: list[float]) -> dict:
    return {
        "id": region_id,
        "page": page,
        "kind": "text",
        "text": text,
        "bbox": bbox,
    }


def test_detects_illustration_catalog_by_title() -> None:
    regions = [
        _text("title", 9, "插 图 目 录", [0.39, 0.16, 0.54, 0.19]),
        _text("entry", 9, "图3-2C M1出土陶器", [0.05, 0.47, 0.23, 0.49]),
    ]

    assert PageSemantics.reference_index_pages_from_regions(regions) == {9}
    assert PageSemantics.is_reference_index_text(
        "插图目录\n图3-2C M1出土陶器........14"
    )


def test_detects_headingless_catalog_continuation_by_density_and_page_locators() -> None:
    regions = []
    for index in range(8):
        y = 0.2 + index * 0.05
        regions.extend(
            [
                _text(
                    f"entry-{index}",
                    10,
                    f"图3-{index + 1} M{index + 1}出土器物",
                    [0.05, y, 0.4, y + 0.02],
                ),
                _text(
                    f"page-{index}",
                    10,
                    str(20 + index),
                    [0.84, y, 0.88, y + 0.02],
                ),
            ]
        )

    assert PageSemantics.reference_index_pages_from_regions(regions) == {10}


def test_does_not_treat_normal_figure_page_as_catalog() -> None:
    regions = [
        _text("prose", 126, "M1:5，陶豆。见图3-2C。", [0.05, 0.1, 0.8, 0.14]),
        {
            "id": "artifact",
            "page": 126,
            "kind": "artifact",
            "bbox": [0.3, 0.2, 0.6, 0.7],
        },
        {
            "id": "caption",
            "page": 126,
            "kind": "caption",
            "text": "图3-2C M1出土陶器（1/4）",
            "bbox": [0.2, 0.8, 0.7, 0.84],
        },
    ]

    assert PageSemantics.reference_index_pages_from_regions(regions) == set()
