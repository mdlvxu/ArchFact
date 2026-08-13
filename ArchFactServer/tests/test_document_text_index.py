from app.services.document_text_index import DocumentTextIndexer


def _block(text: str, region_id: str) -> dict[str, object]:
    return {
        "text": text,
        "region_id": region_id,
        "bbox": [0.1, 0.1, 0.8, 0.2],
    }


def test_builds_traceable_cross_page_artifact_chunks() -> None:
    indexer = DocumentTextIndexer(cross_page_prefix_blocks=2)
    index = indexer.build(
        job_id="job_1",
        document_id="doc_1",
        pages=[
            {
                "page_no": 161,
                "blocks": [
                    _block(
                        "M13:20 \u77f3\u94f2\uff0c\u957f12\u5398\u7c73"
                        "\uff08\u56fe3-14B\uff1b\u5f69\u7248\u56db\u4e5d\uff0c6\uff09",
                        "reg_161_1",
                    )
                ],
            },
            {
                "page_no": 162,
                "blocks": [
                    _block("\u5668\u8eab\u7ec6\u957f\uff0c\u53cc\u9762\u94bb\u5b54", "reg_162_1"),
                    _block("M13:21 \u7389\u73e0", "reg_162_2"),
                ],
            },
        ],
    )

    m1320_chunk_id = index.artifact_mentions["M13:20"][0]
    chunk = next(item for item in index.chunks if item["id"] == m1320_chunk_id)
    assert chunk["source_pages"] == [161, 162]
    assert chunk["region_ids"] == ["reg_161_1", "reg_162_1"]
    assert any(reference.startswith("\u56fe3-14B") for reference in chunk["visual_references"])
    assert any(
        reference.startswith("\u5f69\u7248\u56db\u4e5d")
        for reference in chunk["visual_references"]
    )


def test_enriches_record_using_exact_artifact_id_and_visual_references() -> None:
    indexer = DocumentTextIndexer()
    index = indexer.build(
        job_id="job_1",
        document_id="doc_1",
        pages=[
            {
                "page_no": 10,
                "blocks": [
                    _block(
                        "M13:17 \u7389\u73e0\uff08\u56fe3-14B\uff09",
                        "reg_10_1",
                    )
                ],
            },
            {
                "page_no": 161,
                "blocks": [
                    _block(
                        "M13:20 \u77f3\u94f2\uff08\u56fe3-14B\uff1b"
                        "\u5f69\u7248\u56db\u4e5d\uff0c6\uff09",
                        "reg_161_1",
                    )
                ],
            },
        ],
    )
    records = [
        {
            "source_pages": [161],
            "fields": {"artifact_id": {"value": "M13:20"}},
            "link_hints": {"artifact_ids": ["M13:20"]},
        }
    ]

    enriched = indexer.enrich_records(records, index)[0]

    assert enriched["document_context_pages"] == [161]
    assert enriched["document_context"]["region_ids"] == ["reg_161_1"]
    assert any(ref.startswith("\u56fe3-14B") for ref in enriched["link_hints"]["figure_refs"])
    assert any(
        ref.startswith("\u5f69\u7248\u56db\u4e5d")
        for ref in enriched["link_hints"]["plate_refs"]
    )


def test_duplicate_id_prefers_nearest_document_context() -> None:
    indexer = DocumentTextIndexer()
    index = indexer.build(
        job_id="job_1",
        document_id="doc_1",
        pages=[
            {
                "page_no": 12,
                "blocks": [_block("M3:4 \u65e9\u671f\u76ee\u5f55\u6761\u76ee", "reg_12")],
            },
            {
                "page_no": 145,
                "blocks": [
                    _block(
                        "M3:4 \u77f3\u94f2\uff0c\u6b8b\u957f12.3\u5398\u7c73",
                        "reg_145",
                    )
                ],
            },
        ],
    )
    records = [
        {
            "source_pages": [145],
            "fields": {"artifact_id": {"value": "M3:4"}},
            "link_hints": {},
        }
    ]

    enriched = indexer.enrich_records(records, index)[0]

    assert enriched["document_context_pages"] == [145]
    assert enriched["document_context"]["region_ids"] == ["reg_145"]


def test_linkage_only_color_page_is_excluded_from_body_text_index() -> None:
    indexer = DocumentTextIndexer()

    index = indexer.build(
        job_id="job_1",
        document_id="doc_1",
        pages=[
            {
                "page_no": 26,
                "page_type": "color_plate",
                "semantic_text_source": False,
                "blocks": [_block("3.陶尊（M1:19）", "color-caption")],
            },
            {
                "page_no": 132,
                "page_type": "document",
                "semantic_text_source": True,
                "blocks": [_block("M1:19 陶尊，高领，折沿。", "body-text")],
            },
        ],
    )

    assert index.artifact_mentions["M1:19"]
    assert len(index.chunks) == 1
    assert index.chunks[0]["source_pages"] == [132]
    assert index.chunks[0]["region_ids"] == ["body-text"]
