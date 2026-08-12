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
    # Page-level records keep provenance regions; entity owns the cross-page union.
    assert set(output.records[0]["region_ids"]) == {"text-2", "artifact-3"}
    assert set(output.records[1]["region_ids"]) == {"caption-100", "color-100"}
    assert output.records[0]["associated_pages"] == [2, 3]
    assert output.records[1]["associated_pages"] == [100]
    assert output.records[0]["relation_ids"] == ["evidence-drawing"]
    assert output.records[1]["relation_ids"] == ["caption-color"]


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
    assert set(output.entities[0]["region_ids"]) == {"text-46", "artifact-145"}
    # Merging identity must not copy the drawing onto the text-only page record.
    assert output.records[0]["region_ids"] == ["text-46"]
    assert output.records[1]["region_ids"] == ["artifact-145"]
    assert "artifact-145" not in output.records[0]["region_ids"]


def test_artifact_id_segments_prevent_m13_9_and_m1_39_collision() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [160],
            "linkage": {"identity": {"artifact_id_normalized": "M13:9"}},
            "link_hints": {
                "artifact_ids": ["M13:9"],
                "figure_refs": ["图3-14B:1"],
            },
            "fields": {},
            "region_ids": ["artifact-m13-9"],
            "relation_ids": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [138],
            "linkage": {"identity": {"artifact_id_normalized": "M1:39"}},
            "link_hints": {
                "artifact_ids": ["M1:39"],
                "figure_refs": ["图3-14B:1"],
            },
            "fields": {},
            "region_ids": ["text-m1-39"],
            "relation_ids": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [183],
            "linkage": {"identity": {"artifact_id_normalized": "M13：9"}},
            "link_hints": {"artifact_ids": ["M13 ： 9"]},
            "fields": {},
            "region_ids": ["text-m13-9"],
            "relation_ids": [],
        },
    ]
    regions = [
        {"id": "artifact-m13-9", "page": 160, "kind": "artifact"},
        {"id": "text-m1-39", "page": 138, "kind": "text"},
        {"id": "text-m13-9", "page": 183, "kind": "text"},
    ]

    output = linker.link(
        job_id="job-structured-id",
        document_id="doc-structured-id",
        records=records,
        regions=regions,
    )

    assert len(output.entities) == 2
    assert output.records[0]["entity_id"] == output.records[2]["entity_id"]
    assert output.records[0]["entity_id"] != output.records[1]["entity_id"]
    assert {
        token
        for entity in output.entities
        for token in entity["match_keys"]
        if token.startswith("artifact:")
    } == {"artifact:m13:9", "artifact:m1:39"}


def test_entity_thumbnail_skips_approximate_plate_and_clears_bad_record_thumb() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [29],
            "linkage": {"identity": {"artifact_id_normalized": "M1:98"}},
            "link_hints": {"artifact_ids": ["M1:98"]},
            "fields": {},
            "region_ids": ["approx-plate", "artifact-29"],
            "relation_ids": [],
            "thumbnail_region_id": "approx-plate",
        }
    ]
    regions = [
        {
            "id": "approx-plate",
            "page": 29,
            "kind": "color_plate",
            "approximate": True,
            "crop_object_key": None,
        },
        {
            "id": "artifact-29",
            "page": 29,
            "kind": "artifact",
            "crop_object_key": "documents/demo/pages/0029/crops/artifact/m1-98.png",
        },
    ]

    output = linker.link(
        job_id="job-thumb-entity",
        document_id="doc-thumb-entity",
        records=records,
        regions=regions,
    )

    assert output.entities[0]["thumbnail_region_id"] == "artifact-29"
    assert output.records[0]["thumbnail_region_id"] == "artifact-29"


def test_tomb_unit_prefix_ids_link_to_plain_artifact_ids() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [146],
            "link_hints": {"artifact_ids": ["M4:3"]},
            "fields": {
                "artifact_id": {"value": "M4:3", "raw_value": "M4:3"},
            },
            "region_ids": ["text-146"],
            "relation_ids": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [104],
            "link_hints": {"artifact_ids": ["仲M4:3"]},
            "fields": {
                "artifact_id": {"value": "仲M4:3", "raw_value": "仲M4:3"},
            },
            "region_ids": ["caption-104"],
            "relation_ids": [],
        },
    ]

    output = linker.link(
        job_id="job-zhong",
        document_id="doc-zhong",
        records=records,
        regions=[
            {"id": "text-146", "page": 146, "kind": "text"},
            {"id": "caption-104", "page": 104, "kind": "caption"},
        ],
    )

    assert len(output.entities) == 1
    assert output.entities[0]["canonical_artifact_id"] == "M4:3"
    assert {record["entity_id"] for record in output.records} == {
        output.entities[0]["id"]
    }
