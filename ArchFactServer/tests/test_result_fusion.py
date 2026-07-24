from app.models.schemas import ExtractionConfig, ExtractionFieldSpec
from app.services.result_fusion import ResultFusionService


def test_fusion_links_text_evidence_to_number_and_artifact_regions() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(
                key="artifact_id",
                label="Artifact ID",
                type="string",
                evidence_kind="number",
            )
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [4],
            "fields": {
                "artifact_id": {
                    "raw_value": "M12:3",
                    "value": "M12:3",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 4,
                            "quote": "M12:3",
                            "bbox": [0.2, 0.6, 0.35, 0.66],
                            "region_id": "text-1",
                            "relation_ids": [],
                        }
                    ],
                }
            },
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "text-1",
            "page": 4,
            "kind": "text",
            "bbox": [0.18, 0.58, 0.38, 0.68],
            "source": "pdf_text_layer",
        },
        {
            "id": "number-1",
            "page": 4,
            "kind": "number",
            "bbox": [0.21, 0.6, 0.34, 0.66],
            "source": "yolo",
        },
        {
            "id": "artifact-1",
            "page": 4,
            "kind": "artifact",
            "bbox": [0.12, 0.2, 0.42, 0.56],
            "source": "yolo",
        },
    ]
    relations = [
        {
            "id": "relation-number",
            "source_region_id": "number-1",
            "target_region_id": "artifact-1",
            "relation_type": "number_of",
            "score": 0.9,
            "method": "global_assignment",
            "version": "1",
            "review_status": "unreviewed",
        }
    ]

    output = service.fuse(
        job_id="job-1",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    evidence = record["fields"]["artifact_id"]["evidence"][0]
    assert record["fusion_status"] == "linked"
    assert set(record["region_ids"]) == {"text-1", "number-1", "artifact-1"}
    assert record["primary_number_region_id"] == "number-1"
    assert record["primary_artifact_region_id"] == "artifact-1"
    assert record["primary_relation_id"] == "relation-number"
    assert record["thumbnail_region_id"] == "artifact-1"
    assert set(evidence["linked_region_ids"]) == {"number-1", "artifact-1"}
    assert "relation-number" in evidence["relation_ids"]
    assert any(relation["relation_type"] == "evidence_for" for relation in output.relations)


def test_fusion_links_deepseek_hint_to_cross_page_caption_and_artifact() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [19],
            "link_hints": {
                "artifact_ids": ["H125:1"],
                "figure_refs": [],
                "plate_refs": [],
                "aliases": [],
            },
            "fields": {
                "artifact_id": {
                    "value": "H125:1",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 19,
                            "quote": "H125:1",
                            "bbox": [0.1, 0.7, 0.25, 0.75],
                            "region_id": "text-19",
                        }
                    ],
                }
            },
        }
    ]
    regions = [
        {
            "id": "text-19",
            "page": 19,
            "kind": "text",
            "bbox": [0.1, 0.7, 0.25, 0.75],
            "text": "H125:1",
            "source": "tesseract_ocr",
        },
        {
            "id": "caption-62",
            "page": 62,
            "kind": "caption",
            "bbox": [0.3, 0.8, 0.5, 0.86],
            "text": "H125:1",
            "source": "yolo+tesseract",
        },
        {
            "id": "artifact-62",
            "page": 62,
            "kind": "artifact",
            "bbox": [0.2, 0.2, 0.6, 0.7],
            "source": "yolo",
        },
    ]
    relations = [
        {
            "id": "caption-of-artifact",
            "source_region_id": "caption-62",
            "target_region_id": "artifact-62",
            "relation_type": "caption_of",
            "score": 0.95,
            "method": "global_assignment",
            "version": "1",
            "review_status": "unreviewed",
        }
    ]

    output = service.fuse(
        job_id="job-cross-page",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    assert record["fusion_status"] == "linked"
    assert set(record["region_ids"]) == {"text-19", "caption-62", "artifact-62"}
    assert "caption-of-artifact" in record["relation_ids"]
    evidence_relations = [
        relation for relation in output.relations if relation["relation_type"] == "evidence_for"
    ]
    assert evidence_relations[0]["source_region_id"] == "text-19"
    assert evidence_relations[0]["target_region_id"] == "caption-62"
    assert evidence_relations[0]["method"] == "global_identifier_fusion"


def test_fusion_uses_nearest_crop_when_yolo_only_detects_grave_drawings() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="figure_caption", label="Figure", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [19],
            "fields": {
                "figure_caption": {
                    "value": "图6",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 19,
                            "quote": "图六",
                            "bbox": [0.1, 0.69, 0.48, 0.72],
                            "region_id": "text-figure-6",
                        }
                    ],
                }
            },
        }
    ]
    regions = [
        {
            "id": "text-figure-6",
            "page": 19,
            "kind": "text",
            "bbox": [0.1, 0.69, 0.48, 0.72],
            "source": "tesseract_ocr",
        },
        {
            "id": "upper-drawing",
            "page": 19,
            "kind": "grave_drawing",
            "bbox": [0.13, 0.12, 0.84, 0.39],
            "crop_object_key": "pages/19/upper.png",
            "source": "yolo",
        },
        {
            "id": "lower-drawing",
            "page": 19,
            "kind": "grave_drawing",
            "bbox": [0.14, 0.41, 0.83, 0.60],
            "crop_object_key": "pages/19/lower.png",
            "source": "yolo",
        },
    ]

    output = service.fuse(
        job_id="job-grave-drawing",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    assert record["thumbnail_region_id"] == "lower-drawing"
    assert record["fusion_status"] == "linked"
    assert "lower-drawing" in record["region_ids"]
    assert any(relation["method"] == "nearest_visual_fusion" for relation in output.relations)


def test_fusion_anchors_shared_caption_to_exact_artifact_identifier() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [70],
            "link_hints": {
                "artifact_ids": ["M3:18"],
                "figure_refs": [],
                "plate_refs": [],
                "aliases": [],
            },
            "fields": {
                "artifact_id": {
                    "value": "M3:18",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 70,
                            "quote": "M3:18",
                            "bbox": [0.1, 0.7, 0.2, 0.74],
                            "region_id": "text-70",
                        }
                    ],
                }
            },
        }
    ]
    regions = [
        {
            "id": "text-70",
            "page": 70,
            "kind": "text",
            "bbox": [0.1, 0.7, 0.2, 0.74],
            "text": "M3:18",
        },
        {
            "id": "caption-70",
            "page": 70,
            "kind": "caption",
            "bbox": [0.1, 0.5, 0.5, 0.56],
            "text": "图六一 M3:18",
        },
        {
            "id": "number-70",
            "page": 70,
            "kind": "number",
            "bbox": [0.2, 0.4, 0.25, 0.43],
            "text": "M3:18",
        },
        {
            "id": "artifact-70",
            "page": 70,
            "kind": "artifact",
            "bbox": [0.15, 0.1, 0.3, 0.39],
        },
        {
            "id": "group-70",
            "page": 70,
            "kind": "group",
            "bbox": [0.05, 0.05, 0.95, 0.65],
        },
    ]
    relations = [
        {
            "id": "caption-number",
            "source_region_id": "caption-70",
            "target_region_id": "number-70",
            "relation_type": "caption_to_number",
            "score": 0.95,
            "method": "caption_scope",
            "version": "4",
            "review_status": "unreviewed",
        },
        {
            "id": "number-artifact",
            "source_region_id": "number-70",
            "target_region_id": "artifact-70",
            "relation_type": "number_of",
            "score": 0.92,
            "method": "caption_constrained_assignment",
            "version": "4",
            "review_status": "unreviewed",
        },
        {
            "id": "caption-group",
            "source_region_id": "caption-70",
            "target_region_id": "group-70",
            "relation_type": "caption_of_group",
            "score": 1.0,
            "method": "group_containment",
            "version": "4",
            "review_status": "unreviewed",
        },
    ]

    output = service.fuse(
        job_id="job-chain",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    assert set(record["region_ids"]) == {
        "text-70",
        "caption-70",
        "number-70",
        "artifact-70",
    }
    assert "group-70" not in record["region_ids"]
    evidence_relation = next(
        relation for relation in output.relations if relation["relation_type"] == "evidence_for"
    )
    assert evidence_relation["target_region_id"] == "number-70"
    assert evidence_relation["method"] == "caption_item_identifier_fusion"
    assert record["primary_number_region_id"] == "number-70"
    assert record["primary_artifact_region_id"] == "artifact-70"
    assert record["thumbnail_region_id"] == "artifact-70"


def test_fusion_does_not_show_m3_11_crop_for_m3_4_under_shared_caption() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [144],
            "link_hints": {
                "artifact_ids": ["M3：4", "M3:4"],
                "figure_refs": ["图3-4C"],
                "figure_item_nos": [],
                "caption_texts": ["图3-4C M3出土石锛"],
                "plate_refs": [],
                "aliases": [],
            },
            "fields": {
                "artifact_id": {
                    "value": "M3:4",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 144,
                            "quote": "M3：4，石锛",
                            "bbox": [0.5, 0.3, 0.8, 0.35],
                            "region_id": "text-m3-4",
                        }
                    ],
                }
            },
        }
    ]
    regions = [
        {
            "id": "text-m3-4",
            "page": 144,
            "kind": "text",
            "bbox": [0.5, 0.3, 0.8, 0.35],
            "text": "M3：4，石锛",
        },
        {
            "id": "caption-145",
            "page": 145,
            "kind": "caption",
            "bbox": [0.55, 0.9, 0.86, 0.95],
            "text": "图3-4C M3出土石锛",
        },
        {
            "id": "number-m3-4",
            "page": 145,
            "kind": "number",
            "bbox": [0.67, 0.61, 0.75, 0.64],
            "text": "M3:4",
        },
        {
            "id": "artifact-m3-4",
            "page": 145,
            "kind": "artifact",
            "bbox": [0.52, 0.39, 0.91, 0.64],
            "crop_object_key": "pages/145/m3-4.png",
        },
        {
            "id": "number-m3-11",
            "page": 145,
            "kind": "number",
            "bbox": [0.67, 0.87, 0.74, 0.90],
            "text": "M3:11",
        },
        {
            "id": "artifact-m3-11",
            "page": 145,
            "kind": "artifact",
            "bbox": [0.53, 0.64, 0.89, 0.91],
            "crop_object_key": "pages/145/m3-11.png",
        },
    ]
    relations = [
        {
            "id": "caption-number-m3-4",
            "source_region_id": "caption-145",
            "target_region_id": "number-m3-4",
            "relation_type": "caption_to_number",
            "score": 0.86,
        },
        {
            "id": "caption-number-m3-11",
            "source_region_id": "caption-145",
            "target_region_id": "number-m3-11",
            "relation_type": "caption_to_number",
            "score": 0.98,
        },
        {
            "id": "number-artifact-m3-4",
            "source_region_id": "number-m3-4",
            "target_region_id": "artifact-m3-4",
            "relation_type": "number_of",
            "score": 0.959,
        },
        {
            "id": "number-artifact-m3-11",
            "source_region_id": "number-m3-11",
            "target_region_id": "artifact-m3-11",
            "relation_type": "number_of",
            "score": 0.955,
        },
    ]

    output = service.fuse(
        job_id="job-m3-shared-caption",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-m3-shared-caption",
    )

    record = output.records[0]
    assert record["primary_number_region_id"] == "number-m3-4"
    assert record["primary_artifact_region_id"] == "artifact-m3-4"
    assert record["primary_relation_id"] == "number-artifact-m3-4"
    assert record["thumbnail_region_id"] == "artifact-m3-4"
    assert "number-m3-11" not in record["region_ids"]
    assert "artifact-m3-11" not in record["region_ids"]


def test_fusion_uses_fixed_linkage_when_dynamic_template_has_no_anchor_field() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="custom",
        template_name="Custom",
        fields=[ExtractionFieldSpec(key="category", label="Category", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [20],
            "fields": {
                "category": {
                    "raw_value": "陶鬲足",
                    "value": "陶鬲足",
                    "status": "valid",
                    "evidence": [],
                }
            },
            "linkage": {
                "identity": {
                    "artifact_id_raw": "T3:3",
                    "artifact_id_normalized": "T3:3",
                },
                "visual_link": {
                    "figure_no": "图6",
                    "figure_item_no": "3",
                    "plate_no": None,
                    "caption_raw": "图6 1—4为陶鬲足",
                    "evidence_block_ids": ["text-20"],
                    "evidence": [
                        {
                            "page": 20,
                            "quote": "T3:3见图6，图中序号3。",
                            "bbox": [0.1, 0.7, 0.5, 0.75],
                            "region_id": "text-20",
                        }
                    ],
                },
            },
            "link_hints": {
                "artifact_ids": ["T3:3"],
                "figure_refs": ["图6", "图6-3"],
                "figure_item_nos": ["3"],
                "caption_texts": ["图6 1—4为陶鬲足"],
                "plate_refs": [],
                "aliases": [],
            },
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "text-20",
            "page": 20,
            "kind": "text",
            "bbox": [0.1, 0.7, 0.5, 0.75],
            "text": "T3:3见图6，图中序号3。",
        },
        {
            "id": "caption-20",
            "page": 20,
            "kind": "caption",
            "bbox": [0.1, 0.5, 0.7, 0.56],
            "text": "图6 1—4为陶鬲足",
        },
        {
            "id": "number-20",
            "page": 20,
            "kind": "number",
            "bbox": [0.3, 0.4, 0.34, 0.43],
            "text": "3",
        },
        {
            "id": "artifact-20",
            "page": 20,
            "kind": "artifact",
            "bbox": [0.24, 0.1, 0.4, 0.38],
            "crop_object_key": "pages/20/artifact.png",
        },
    ]
    relations = [
        {
            "id": "caption-number-20",
            "source_region_id": "caption-20",
            "target_region_id": "number-20",
            "relation_type": "caption_to_number",
        },
        {
            "id": "number-artifact-20",
            "source_region_id": "number-20",
            "target_region_id": "artifact-20",
            "relation_type": "number_of",
        },
    ]

    output = service.fuse(
        job_id="job-fixed-linkage",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    assert record["fusion_status"] == "linked"
    assert record["thumbnail_region_id"] == "artifact-20"
    assert {"caption-20", "number-20", "artifact-20"} <= set(record["region_ids"])
    assert any(
        relation.get("method") == "caption_item_identifier_fusion"
        and relation.get("target_region_id") == "number-20"
        for relation in output.relations
    )


def test_fusion_uses_item_number_as_anchor_without_linking_sibling_artifacts() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="custom",
        template_name="Custom",
        fields=[ExtractionFieldSpec(key="category", label="Category", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [2],
            "fields": {"category": {"value": "陶鬲足", "status": "valid", "evidence": []}},
            "linkage": {
                "identity": {},
                "visual_link": {
                    "figure_no": "图6",
                    "figure_item_no": "2",
                    "caption_raw": "图6 1—2为陶鬲足",
                    "evidence": [
                        {
                            "page": 2,
                            "quote": "图6中序号2",
                            "bbox": [0.1, 0.7, 0.3, 0.75],
                            "region_id": "text-2",
                        }
                    ],
                },
            },
            "link_hints": {
                "figure_refs": ["图6"],
                "figure_item_nos": ["2"],
                "caption_texts": ["图6 1—2为陶鬲足"],
                "artifact_ids": [],
                "plate_refs": [],
                "aliases": [],
            },
        }
    ]
    regions = [
        {"id": "text-2", "page": 2, "kind": "text", "bbox": [0.1, 0.7, 0.3, 0.75]},
        {
            "id": "caption-20",
            "page": 20,
            "kind": "caption",
            "bbox": [0.1, 0.8, 0.9, 0.86],
            "text": "图6 1—2为陶鬲足",
        },
        {
            "id": "number-1",
            "page": 20,
            "kind": "number",
            "bbox": [0.2, 0.5, 0.24, 0.54],
            "text": "1",
        },
        {
            "id": "number-2",
            "page": 20,
            "kind": "number",
            "bbox": [0.6, 0.5, 0.64, 0.54],
            "text": "2",
        },
        {"id": "artifact-1", "page": 20, "kind": "artifact", "bbox": [0.12, 0.1, 0.32, 0.48]},
        {"id": "artifact-2", "page": 20, "kind": "artifact", "bbox": [0.52, 0.1, 0.72, 0.48]},
    ]
    relations = [
        {
            "id": "caption-number-1",
            "source_region_id": "caption-20",
            "target_region_id": "number-1",
            "relation_type": "caption_to_number",
        },
        {
            "id": "caption-number-2",
            "source_region_id": "caption-20",
            "target_region_id": "number-2",
            "relation_type": "caption_to_number",
        },
        {
            "id": "number-artifact-1",
            "source_region_id": "number-1",
            "target_region_id": "artifact-1",
            "relation_type": "number_of",
        },
        {
            "id": "number-artifact-2",
            "source_region_id": "number-2",
            "target_region_id": "artifact-2",
            "relation_type": "number_of",
        },
    ]

    output = service.fuse(
        job_id="job-item-anchor",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-item-anchor",
    )

    record = output.records[0]
    assert {"text-2", "caption-20", "number-2", "artifact-2"} <= set(record["region_ids"])
    assert "number-1" not in record["region_ids"]
    assert "artifact-1" not in record["region_ids"]
    assert record["thumbnail_region_id"] == "artifact-2"
    evidence_relation = next(
        relation for relation in output.relations if relation.get("relation_type") == "evidence_for"
    )
    assert evidence_relation["target_region_id"] == "number-2"
    assert evidence_relation["method"] == "global_caption_item_fusion"


def test_fusion_does_not_treat_bare_list_number_as_artifact_identifier() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="custom",
        template_name="Custom",
        fields=[ExtractionFieldSpec(key="category", label="Category", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [43],
            "fields": {
                "category": {
                    "value": "玉管、珠和隧孔珠",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 43,
                            "quote": "4.玉管、珠和隧孔珠",
                            "bbox": [0.1, 0.7, 0.4, 0.75],
                            "region_id": "text-43",
                        }
                    ],
                },
                "artifact_id": {
                    "value": None,
                    "raw_value": None,
                    "status": "missing",
                    "evidence": [],
                },
            },
            "link_hints": {
                "artifact_ids": [],
                "figure_refs": [],
                "figure_item_nos": ["4"],
                "caption_texts": ["4.玉管、珠和隧孔珠"],
                "plate_refs": ["彩版二六"],
                "aliases": [],
            },
        }
    ]
    regions = [
        {"id": "text-43", "page": 43, "kind": "text", "bbox": [0.1, 0.7, 0.4, 0.75]},
        {"id": "number-m3-4", "page": 145, "kind": "number", "text": "M3:4"},
        {"id": "artifact-m3-4", "page": 145, "kind": "artifact"},
    ]
    relations = [
        {
            "id": "number-artifact-m3-4",
            "source_region_id": "number-m3-4",
            "target_region_id": "artifact-m3-4",
            "relation_type": "number_of",
            "score": 0.99,
        }
    ]

    output = service.fuse(
        job_id="job-list-number",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-list-number",
    )

    record = output.records[0]
    assert record["primary_number_region_id"] is None
    assert record["primary_artifact_region_id"] is None
    assert record["thumbnail_region_id"] is None
    assert "number-m3-4" not in record["region_ids"]
    assert "artifact-m3-4" not in record["region_ids"]


def test_fusion_keeps_multiple_strong_matches_across_distant_pages() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [2],
            "link_hints": {
                "artifact_ids": ["H125:1"],
                "figure_refs": ["图6-3"],
                "figure_item_nos": ["3"],
                "plate_refs": ["彩版8-2"],
                "caption_texts": [],
                "aliases": [],
            },
            "fields": {
                "artifact_id": {
                    "value": "H125:1",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 2,
                            "quote": "H125:1，见图6-3、彩版8-2",
                            "bbox": [0.1, 0.7, 0.55, 0.76],
                            "region_id": "text-2",
                        }
                    ],
                }
            },
        }
    ]
    regions = [
        {
            "id": "text-2",
            "page": 2,
            "kind": "text",
            "bbox": [0.1, 0.7, 0.55, 0.76],
            "text": "H125:1，见图6-3、彩版8-2",
        },
        {
            "id": "caption-3",
            "page": 3,
            "kind": "caption",
            "bbox": [0.2, 0.8, 0.5, 0.86],
            "text": "H125:1 图6-3",
        },
        {
            "id": "artifact-3",
            "page": 3,
            "kind": "artifact",
            "bbox": [0.2, 0.2, 0.5, 0.7],
        },
        {
            "id": "caption-100",
            "page": 100,
            "kind": "caption",
            "bbox": [0.2, 0.8, 0.5, 0.86],
            "text": "彩版8-2",
        },
        {
            "id": "color-100",
            "page": 100,
            "kind": "color_plate",
            "bbox": [0.2, 0.2, 0.5, 0.7],
        },
    ]
    relations = [
        {
            "id": "caption-artifact-3",
            "source_region_id": "caption-3",
            "target_region_id": "artifact-3",
            "relation_type": "caption_of",
        },
        {
            "id": "caption-color-100",
            "source_region_id": "caption-100",
            "target_region_id": "color-100",
            "relation_type": "color_plate_of",
        },
    ]

    output = service.fuse(
        job_id="job-global",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-global",
    )

    record = output.records[0]
    assert set(record["region_ids"]) == {
        "text-2",
        "caption-3",
        "artifact-3",
        "caption-100",
        "color-100",
    }
    assert record["associated_pages"] == [2, 3, 100]
    assert any(
        relation.get("target_region_id") == "caption-100"
        and relation.get("method") == "global_identifier_fusion"
        for relation in output.relations
    )
