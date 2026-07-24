from app.services.artifact_entity_linker import ArtifactEntityLinker


def test_document_entity_groups_non_adjacent_text_drawing_and_color_plate() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [2],
            "linkage": {
                "identity": {"artifact_id_normalized": "H125:1"},
                "visual_link": {
                    "figure_no": "图6",
                    "figure_item_no": "3",
                    "plate_no": "彩版8",
                    "plate_item_no": "2",
                },
            },
            "link_hints": {
                "artifact_ids": ["H125:1"],
                "figure_refs": ["图6-3"],
                "plate_refs": ["彩版8-2"],
            },
            "fields": {},
            "region_ids": ["text-2", "artifact-3"],
            "relation_ids": ["evidence-drawing"],
        },
        {
            "record_type": "artifact_color_plate",
            "source_pages": [100],
            "linkage": {
                "identity": {},
                "visual_link": {
                    "plate_no": "彩版8",
                    "plate_item_no": "2",
                },
            },
            "link_hints": {
                "artifact_ids": [],
                "figure_refs": [],
                "plate_refs": ["彩版8-2"],
            },
            "fields": {},
            "region_ids": ["caption-100", "color-100"],
            "relation_ids": ["caption-color"],
        },
    ]
    regions = [
        {"id": "text-2", "page": 2, "kind": "text"},
        {"id": "artifact-3", "page": 3, "kind": "artifact"},
        {"id": "caption-100", "page": 100, "kind": "caption"},
        {"id": "color-100", "page": 100, "kind": "color_plate"},
    ]

    output = linker.link(
        job_id="job-1",
        document_id="doc-1",
        records=records,
        regions=regions,
    )

    assert len(output.entities) == 1
    entity = output.entities[0]
    assert entity["canonical_artifact_id"] == "H125:1"
    assert entity["source_pages"] == [2, 100]
    assert entity["associated_pages"] == [2, 3, 100]
    assert entity["link_status"] == "linked"
    assert set(entity["region_ids"]) == {
        "text-2",
        "artifact-3",
        "caption-100",
        "color-100",
    }
    assert {record["entity_id"] for record in output.records} == {entity["id"]}
    assert all(record["associated_pages"] == [2, 3, 100] for record in output.records)


def test_bare_plate_number_does_not_merge_unrelated_records() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [4],
            "link_hints": {"plate_refs": ["彩版8"]},
            "fields": {},
            "region_ids": [],
            "relation_ids": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [80],
            "link_hints": {"plate_refs": ["彩版8"]},
            "fields": {},
            "region_ids": [],
            "relation_ids": [],
        },
    ]

    output = linker.link(
        job_id="job-2",
        document_id="doc-2",
        records=records,
        regions=[],
    )

    assert len(output.entities) == 2
    assert len({record["entity_id"] for record in output.records}) == 2
    assert all(record["entity_match_status"] == "unlinked" for record in output.records)


def test_shared_figure_does_not_merge_different_artifact_ids() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [144],
            "linkage": {
                "identity": {"artifact_id_normalized": "M3:4"},
                "visual_link": {"figure_no": "图3-4", "figure_item_no": "C"},
            },
            "link_hints": {"artifact_ids": ["M3:4"], "figure_refs": ["图3-4:C"]},
            "fields": {},
            "region_ids": ["artifact-4"],
            "relation_ids": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [145],
            "linkage": {
                "identity": {"artifact_id_normalized": "M3:5"},
                "visual_link": {"figure_no": "图3-4", "figure_item_no": "C"},
            },
            "link_hints": {"artifact_ids": ["M3:5"], "figure_refs": ["图3-4:C"]},
            "fields": {},
            "region_ids": ["artifact-5"],
            "relation_ids": [],
        },
    ]
    regions = [
        {"id": "artifact-4", "page": 144, "kind": "artifact"},
        {"id": "artifact-5", "page": 145, "kind": "artifact"},
    ]

    output = linker.link(
        job_id="job-shared-figure",
        document_id="doc-shared-figure",
        records=records,
        regions=regions,
    )

    assert len(output.entities) == 2
    assert len({record["entity_id"] for record in output.records}) == 2
    assert all(len(entity["record_ids"]) == 1 for entity in output.entities)


def test_exact_artifact_id_still_merges_records_from_different_pages() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [46],
            "linkage": {"identity": {"artifact_id_normalized": "M3:4"}},
            "link_hints": {"artifact_ids": ["M3:4"]},
            "fields": {},
            "region_ids": ["text-46"],
            "relation_ids": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [145],
            "linkage": {"identity": {"artifact_id_normalized": "M3：4"}},
            "link_hints": {"artifact_ids": ["M3：4"]},
            "fields": {},
            "region_ids": ["artifact-145"],
            "relation_ids": [],
        },
    ]
    regions = [
        {"id": "text-46", "page": 46, "kind": "text"},
        {"id": "artifact-145", "page": 145, "kind": "artifact"},
    ]

    output = linker.link(
        job_id="job-same-id",
        document_id="doc-same-id",
        records=records,
        regions=regions,
    )

    assert len(output.entities) == 1
    assert output.entities[0]["canonical_artifact_id"] == "M3:4"
    assert output.entities[0]["associated_pages"] == [46, 145]
