"""Phase-A fusion/entity regression cases for matching correctness."""

from app.models.schemas import ExtractionConfig, ExtractionFieldSpec
from app.services.artifact_entity_linker import ArtifactEntityLinker
from app.services.result_fusion import ResultFusionService


def _basic_config() -> ExtractionConfig:
    return ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="category", label="Category", type="string"),
        ],
    )


def test_regression_ocr_t_prefix_binds_numbered_crop() -> None:
    service = ResultFusionService()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [270],
            "fields": {
                "artifact_id": {
                    "value": "T03021:03",
                    "evidence": [
                        {
                            "page": 270,
                            "bbox": [0.45, 0.72, 0.58, 0.76],
                            "region_id": "text-270",
                        }
                    ],
                },
                "category": {"value": "青瓷碗", "evidence": []},
            },
            "region_ids": ["text-270"],
            "relation_ids": [],
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "text-270",
            "page": 270,
            "kind": "text",
            "bbox": [0.45, 0.72, 0.58, 0.76],
            "text": "T0302①：03，宋代青瓷碗。",
        },
        {
            "id": "number-270",
            "page": 270,
            "kind": "number",
            "text": "10302①:03",
            "bbox": [0.47, 0.29, 0.56, 0.31],
        },
        {
            "id": "artifact-270",
            "page": 270,
            "kind": "artifact",
            "bbox": [0.40, 0.18, 0.63, 0.29],
            "crop_object_key": "documents/demo/pages/0270/crops/artifact/bowl.png",
        },
    ]
    relations = [
        {
            "id": "number-of-270",
            "source_region_id": "number-270",
            "target_region_id": "artifact-270",
            "relation_type": "number_of",
            "score": 0.9,
            "method": "caption_constrained_assignment",
            "version": "1",
            "review_status": "unreviewed",
        }
    ]

    output = service.fuse(
        job_id="job-f01",
        records=records,
        regions=regions,
        relations=relations,
        config=_basic_config(),
        model_run_id="run-f01",
    )

    assert output.records[0]["primary_artifact_region_id"] == "artifact-270"
    assert output.records[0]["thumbnail_region_id"] == "artifact-270"


def test_regression_sparse_idless_record_cannot_claim_numbered_page_crop() -> None:
    service = ResultFusionService()
    records = [
        {
            "fields": {
                "artifact_id": {"value": None, "evidence": []},
                "category": {
                    "value": "韩瓶",
                    "evidence": [
                        {
                            "page": 267,
                            "bbox": [0.40, 0.70, 0.60, 0.75],
                            "region_id": "text-267",
                        }
                    ],
                },
                "completeness": {"value": "完整", "evidence": []},
            },
            "region_ids": ["text-267"],
            "relation_ids": [],
            "source_pages": [267],
            "warnings": [],
        },
        {
            "fields": {
                "artifact_id": {
                    "value": "T03021:03",
                    "evidence": [
                        {
                            "page": 270,
                            "bbox": [0.45, 0.72, 0.58, 0.76],
                            "region_id": "text-270",
                        }
                    ],
                },
                "category": {"value": "青瓷碗", "evidence": []},
            },
            "region_ids": ["text-270"],
            "relation_ids": [],
            "source_pages": [270],
            "warnings": [],
        },
    ]
    regions = [
        {
            "id": "text-267",
            "page": 267,
            "kind": "text",
            "text": "韩瓶。完整。",
            "bbox": [0.40, 0.70, 0.60, 0.75],
        },
        {
            "id": "text-270",
            "page": 270,
            "kind": "text",
            "text": "T0302①：03",
            "bbox": [0.45, 0.72, 0.58, 0.76],
        },
        {
            "id": "number-270",
            "page": 270,
            "kind": "number",
            "text": "10302①:03",
            "bbox": [0.47, 0.29, 0.56, 0.31],
        },
        {
            "id": "artifact-270",
            "page": 270,
            "kind": "artifact",
            "bbox": [0.40, 0.18, 0.63, 0.29],
            "crop_object_key": "documents/demo/pages/0270/crops/artifact/bowl.png",
        },
    ]
    relation_by_id = {
        "number-of-270": {
            "id": "number-of-270",
            "source_region_id": "number-270",
            "target_region_id": "artifact-270",
            "relation_type": "number_of",
        }
    }
    region_by_id = {region["id"]: region for region in regions}

    matched = service._fuse_nearest_visual_regions(
        job_id="job-f02",
        records=records,
        regions=regions,
        region_by_id=region_by_id,
        relation_by_id=relation_by_id,
        model_run_id="run-f02",
    )

    assert 0 not in matched
    assert "artifact-270" not in records[0]["region_ids"]


def test_regression_approximate_plate_is_never_thumbnail() -> None:
    service = ResultFusionService()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [29],
            "fields": {
                "artifact_id": {
                    "value": "M1:98",
                    "evidence": [
                        {
                            "page": 29,
                            "bbox": [0.2, 0.5, 0.4, 0.55],
                            "region_id": "text-29",
                        }
                    ],
                }
            },
            "region_ids": ["text-29", "approx-plate"],
            "relation_ids": [],
            "warnings": [],
            "thumbnail_region_id": "approx-plate",
        }
    ]
    regions = [
        {
            "id": "text-29",
            "page": 29,
            "kind": "text",
            "bbox": [0.2, 0.5, 0.4, 0.55],
            "text": "M1:98",
        },
        {
            "id": "approx-plate",
            "page": 29,
            "kind": "color_plate",
            "bbox": [0.2, 0.5, 0.4, 0.55],
            "approximate": True,
            "crop_object_key": None,
        },
    ]
    region_by_id = {region["id"]: region for region in regions}
    service._sanitize_thumbnail_region_ids(records, region_by_id)
    assert records[0]["thumbnail_region_id"] is None


def test_regression_entity_merge_keeps_page_level_provenance() -> None:
    linker = ArtifactEntityLinker()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [46],
            "linkage": {"identity": {"artifact_id_normalized": "M3:4"}},
            "link_hints": {"artifact_ids": ["M3:4"]},
            "fields": {},
            "region_ids": ["text-46"],
            "relation_ids": ["rel-text"],
        },
        {
            "record_type": "artifact",
            "source_pages": [145],
            "linkage": {"identity": {"artifact_id_normalized": "M3:4"}},
            "link_hints": {"artifact_ids": ["M3:4"]},
            "fields": {},
            "region_ids": ["artifact-145"],
            "relation_ids": ["rel-drawing"],
            "thumbnail_region_id": "artifact-145",
            "primary_artifact_region_id": "artifact-145",
        },
    ]
    regions = [
        {"id": "text-46", "page": 46, "kind": "text"},
        {
            "id": "artifact-145",
            "page": 145,
            "kind": "artifact",
            "crop_object_key": "documents/demo/pages/0145/crops/artifact/m3-4.png",
        },
    ]

    output = linker.link(
        job_id="job-f06",
        document_id="doc-f06",
        records=records,
        regions=regions,
    )

    assert len(output.entities) == 1
    assert set(output.entities[0]["region_ids"]) == {"text-46", "artifact-145"}
    assert output.records[0]["region_ids"] == ["text-46"]
    assert output.records[0]["relation_ids"] == ["rel-text"]
    assert output.records[1]["region_ids"] == ["artifact-145"]
    assert output.records[1]["thumbnail_region_id"] == "artifact-145"
    assert output.records[0]["thumbnail_region_id"] is None
