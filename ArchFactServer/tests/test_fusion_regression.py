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


def test_regression_dropped_circled_unit_number_binds_crop_then_plate() -> None:
    """Body keeps NFKC ID; crop OCR drops ①; still number → text → plate."""

    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="category", label="Category", type="string"),
            ExtractionFieldSpec(
                key="figure_caption", label="Figure Caption", type="string"
            ),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [269],
            "link_hints": {
                "artifact_ids": ["T0302①：01", "T03021:01"],
                "figure_refs": ["图5"],
                "figure_item_nos": [],
                "plate_refs": ["彩版九五,1"],
                "caption_texts": ["图5;彩版九五,1"],
                "aliases": ["T0302①：01"],
            },
            "fields": {
                "artifact_id": {
                    "value": "T03021:01",
                    "evidence": [
                        {
                            "page": 269,
                            "bbox": [0.1, 0.7, 0.3, 0.74],
                            "region_id": "text-269",
                            "quote": "T0302①：01，唐代酱釉双耳罐",
                        }
                    ],
                },
                "category": {"value": "酱釉双耳罐", "evidence": []},
                "figure_caption": {
                    "value": "图5；彩版九五，1",
                    "evidence": [
                        {
                            "page": 269,
                            "bbox": [0.1, 0.74, 0.5, 0.78],
                            "region_id": "text-269",
                            "quote": "（图5；彩版九五，1）",
                        }
                    ],
                },
            },
            "region_ids": ["text-269"],
            "relation_ids": [],
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "text-269",
            "page": 269,
            "kind": "text",
            "bbox": [0.1, 0.7, 0.5, 0.78],
            "text": "T0302①：01，唐代酱釉双耳罐。（图5；彩版九五，1）",
        },
        {
            "id": "caption-270",
            "page": 270,
            "kind": "caption",
            "bbox": [0.2, 0.9, 0.7, 0.95],
            "text": "图5文家山出土陶器",
        },
        {
            "id": "number-270",
            "page": 270,
            "kind": "number",
            "bbox": [0.22, 0.30, 0.34, 0.33],
            "text": "T0302:01",
        },
        {
            "id": "artifact-270",
            "page": 270,
            "kind": "artifact",
            "bbox": [0.18, 0.12, 0.40, 0.30],
            "crop_object_key": "documents/demo/pages/0270/crops/artifact/jar.png",
        },
        {
            "id": "number-270-02",
            "page": 270,
            "kind": "number",
            "bbox": [0.71, 0.49, 0.82, 0.51],
            "text": "T03021:02",
        },
        {
            "id": "artifact-270-02",
            "page": 270,
            "kind": "artifact",
            "bbox": [0.65, 0.35, 0.88, 0.49],
            "crop_object_key": "documents/demo/pages/0270/crops/artifact/other.png",
        },
        {
            "id": "plate-95-title",
            "page": 112,
            "kind": "text",
            "bbox": [0.3, 0.94, 0.7, 0.97],
            "text": "彩版九五 文家山出土唐宋时期瓷器",
        },
        {
            "id": "plate-95-item-1",
            "page": 112,
            "kind": "text",
            "text": "1.酱釉瓷双耳罐（T0302①：01）",
            "bbox": [0.14, 0.43, 0.40, 0.45],
            "confidence": 0.96,
        },
    ]
    relations = [
        {
            "id": "caption-number-01",
            "source_region_id": "caption-270",
            "target_region_id": "number-270",
            "relation_type": "caption_to_number",
            "score": 0.9,
        },
        {
            "id": "caption-number-02",
            "source_region_id": "caption-270",
            "target_region_id": "number-270-02",
            "relation_type": "caption_to_number",
            "score": 0.9,
        },
        {
            "id": "number-of-01",
            "source_region_id": "number-270",
            "target_region_id": "artifact-270",
            "relation_type": "number_of",
            "score": 0.92,
        },
        {
            "id": "number-of-02",
            "source_region_id": "number-270-02",
            "target_region_id": "artifact-270-02",
            "relation_type": "number_of",
            "score": 0.93,
        },
    ]

    output = service.fuse(
        job_id="job-f-unit-collapse",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-f-unit-collapse",
        page_metadata={112: {"page_type": "color_plate"}},
    )

    record = output.records[0]
    inferred_plate = next(
        region
        for region in regions
        if region.get("kind") == "color_plate"
        and region.get("source") == "ocr_identifier_inference"
    )
    assert record["fields"]["artifact_id"]["value"] == "T03021:01"
    assert record["primary_number_region_id"] == "number-270"
    assert record["primary_artifact_region_id"] == "artifact-270"
    assert "artifact-270-02" not in record["region_ids"]
    assert inferred_plate["page"] == 112
    assert inferred_plate["text"] == "1.酱釉瓷双耳罐（T0302①：01）"
    assert inferred_plate["id"] in record["region_ids"]
    assert 112 in record["associated_pages"]
    assert any(
        relation.get("relation_type") == "plate_reference_to_color"
        and relation.get("target_region_id") == inferred_plate["id"]
        for relation in output.relations
    )


def test_regression_missing_colon_number_binds_m02_crop_and_plate() -> None:
    """OCR ``M022`` must bind ``M02:2`` without enabling unsafe compact IDs."""

    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(
                key="figure_caption", label="Figure Caption", type="string"
            ),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [268],
            "link_hints": {
                "artifact_ids": ["M02：2", "M02:2"],
                "figure_refs": ["图3B"],
                "figure_item_nos": [],
                "plate_refs": ["彩版九三,3"],
                "caption_texts": ["图3B;彩版九三,3"],
                "aliases": ["M02：2"],
            },
            "fields": {
                "artifact_id": {
                    "value": "M02:2",
                    "evidence": [
                        {
                            "page": 268,
                            "bbox": [0.15, 0.32, 0.48, 0.35],
                            "region_id": "text-268",
                            "quote": "M02：2，青瓷碗",
                        }
                    ],
                },
                "figure_caption": {
                    "value": "图3B;彩版九三,3",
                    "evidence": [
                        {
                            "page": 268,
                            "bbox": [0.11, 0.39, 0.49, 0.44],
                            "region_id": "text-268",
                            "quote": "（图3B；彩版九三，3）",
                        }
                    ],
                },
            },
            "region_ids": ["text-268"],
            "relation_ids": [],
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "text-268",
            "page": 268,
            "kind": "text",
            "bbox": [0.11, 0.32, 0.49, 0.44],
            "text": "M02：2，青瓷碗。（图3B；彩版九三，3）",
        },
        {
            "id": "caption-268",
            "page": 268,
            "kind": "caption",
            "bbox": [0.68, 0.47, 0.94, 0.51],
            "text": "图3B M02出土器物（1/4）",
        },
        {
            "id": "number-m02-2",
            "page": 268,
            "kind": "number",
            "bbox": [0.78, 0.35, 0.87, 0.38],
            "text": "M022",
        },
        {
            "id": "artifact-m02-2",
            "page": 268,
            "kind": "artifact",
            "bbox": [0.70, 0.28, 0.94, 0.35],
            "crop_object_key": "documents/demo/pages/0268/crops/artifact/m02-2.png",
        },
        {
            "id": "number-m02-1",
            "page": 268,
            "kind": "number",
            "bbox": [0.55, 0.48, 0.64, 0.50],
            "text": "M02:1",
        },
        {
            "id": "artifact-m02-1",
            "page": 268,
            "kind": "artifact",
            "bbox": [0.50, 0.41, 0.69, 0.48],
            "crop_object_key": "documents/demo/pages/0268/crops/artifact/m02-1.png",
        },
        {
            "id": "plate-93-title",
            "page": 110,
            "kind": "text",
            "bbox": [0.3, 0.94, 0.7, 0.97],
            "text": "彩版九三 M02出土瓷器",
        },
        {
            "id": "plate-93-item-3",
            "page": 110,
            "kind": "text",
            "bbox": [0.66, 0.89, 0.83, 0.91],
            "text": "3.青釉碗（M02：2）",
        },
    ]
    relations = [
        {
            "id": "caption-number-m02-2",
            "source_region_id": "caption-268",
            "target_region_id": "number-m02-2",
            "relation_type": "caption_to_number",
            "score": 0.92,
        },
        {
            "id": "number-artifact-m02-2",
            "source_region_id": "number-m02-2",
            "target_region_id": "artifact-m02-2",
            "relation_type": "number_of",
            "score": 0.91,
        },
        {
            "id": "caption-number-m02-1",
            "source_region_id": "caption-268",
            "target_region_id": "number-m02-1",
            "relation_type": "caption_to_number",
            "score": 0.90,
        },
        {
            "id": "number-artifact-m02-1",
            "source_region_id": "number-m02-1",
            "target_region_id": "artifact-m02-1",
            "relation_type": "number_of",
            "score": 0.92,
        },
    ]

    output = service.fuse(
        job_id="job-f-missing-colon",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-f-missing-colon",
        page_metadata={110: {"page_type": "color_plate"}},
    )

    record = output.records[0]
    inferred_plate = next(
        region
        for region in regions
        if region.get("kind") == "color_plate"
        and region.get("match_key") == "artifact:m02:2"
    )
    assert record["primary_number_region_id"] == "number-m02-2"
    assert record["primary_artifact_region_id"] == "artifact-m02-2"
    assert record["thumbnail_region_id"] == "artifact-m02-2"
    assert "artifact-m02-1" not in record["region_ids"]
    assert inferred_plate["id"] in record["region_ids"]
    assert record["associated_pages"] == [110, 268]


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


def test_regression_visual_only_card_recovers_body_owner_and_crop() -> None:
    service = ResultFusionService()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [85],
            "associated_pages": [85],
            "fields": {
                "artifact_id": {
                    "value": "T03022:3",
                    "evidence": [
                        {
                            "page": 85,
                            "bbox": [0.70, 0.27, 0.92, 0.30],
                            "region_id": "color-text",
                        }
                    ],
                },
                "category": {"value": "有段石", "evidence": []},
                "measurements": {
                    "value": "长 4.2 cm；宽 2.1 cm；厚 0.7 cm；长 3.1 cm；宽 1.9 cm；厚 0.5 cm",
                    "evidence": [],
                },
                "morphological_description": {
                    "value": (
                        "浅灰色粉砂质泥岩。通体打磨,刃角稍残。，。10件。"
                        "T02037):19,青灰色粉砂质泥岩。截面呈菱形，（图4-17、3"
                    ),
                    "evidence": [],
                },
                "figure_caption": {"value": None, "status": "missing", "evidence": []},
            },
            "link_hints": {"artifact_ids": ["T03022:3"]},
            "region_ids": ["color-text"],
            "relation_ids": [],
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "color-text",
            "page": 85,
            "kind": "text",
            "bbox": [0.70, 0.27, 0.92, 0.30],
            "text": "4.有段石（T03022：3）",
        },
        {
            "id": "body-text",
            "page": 214,
            "kind": "text",
            "bbox": [0.10, 0.20, 0.93, 0.23],
            "text": (
                "刃角残。长4.2、宽2.1、厚0.7厘米（图4-16，4；彩版六八，3）。"
                "T0302②：3，浅灰色粉砂质泥岩。通体打磨，刃角稍残。"
                "长3.1、宽1.9、厚0.5厘米（图4-16，5；彩版六八，4）。"
            ),
        },
        {
            "id": "next-artifact-text",
            "page": 214,
            "kind": "text",
            "bbox": [0.10, 0.23, 0.93, 0.26],
            "text": "10件。T02037）：19，青灰色粉砂质泥岩。截面呈菱形。",
        },
        {
            "id": "figure-label-text",
            "page": 215,
            "kind": "text",
            "bbox": [0.42, 0.29, 0.58, 0.32],
            "text": "5.T0302②:3",
        },
        {
            "id": "figure-label-caption",
            "page": 215,
            "kind": "caption",
            "bbox": [0.40, 0.28, 0.60, 0.33],
            "text": "5.T0302②:3",
        },
        {
            "id": "artifact-crop",
            "page": 215,
            "kind": "artifact",
            "bbox": [0.40, 0.10, 0.60, 0.30],
            "crop_object_key": "documents/demo/pages/0215/crops/artifact/t03022-3.png",
        },
    ]

    output = service.fuse(
        job_id="job-visual-owner",
        records=records,
        regions=regions,
        relations=[],
        config=_basic_config(),
        model_run_id="run-visual-owner",
        page_metadata={
            85: {
                "page_type": "color_plate",
                "semantic_text_source": False,
            }
        },
    )

    assert len(output.records) == 1
    record = output.records[0]
    assert record["source_pages"] == [214]
    assert record["text_evidence"][0]["region_id"] == "body-text"
    assert "next-artifact-text" not in {
        item["region_id"] for item in record["text_evidence"]
    }
    fields = record.get("fields", {})
    description = str(fields.get("morphological_description", {}).get("value") or "")
    texture = str(fields.get("texture", {}).get("value") or "")
    measurements = str(fields.get("measurements", {}).get("value") or "")
    caption = str(fields.get("figure_caption", {}).get("value") or "")
    quotes = " ".join(
        str(item.get("quote") or "")
        for item in record.get("text_evidence") or []
        if isinstance(item, dict)
    )
    assert "T02037" not in description
    assert "图4-17" not in description
    assert "T02037" not in texture
    assert "4.2" not in measurements
    assert "3.1" in measurements
    assert "图4-16,5" in caption.replace(" ", "").replace("，", ",")
    assert "图4-16,4" not in caption.replace(" ", "").replace("，", ",")
    assert "长4.2" not in quotes
    assert 85 in record["associated_pages"]
    assert record["primary_artifact_region_id"] == "artifact-crop"
    assert record["thumbnail_region_id"] == "artifact-crop"


def test_regression_rematch_replaces_polluted_paragraph_fields() -> None:
    service = ResultFusionService()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [214],
            "associated_pages": [85, 213, 214],
            "fields": {
                "artifact_id": {
                    "value": "T03022:3",
                    "evidence": [
                        {
                            "page": 214,
                            "bbox": [0.10, 0.20, 0.93, 0.23],
                            "region_id": "body-text",
                        }
                    ],
                },
                "category": {"value": "有段石", "evidence": []},
                "measurements": {
                    "value": "长 4.2 cm；宽 2.1 cm；厚 0.7 cm；长 3.1 cm；宽 1.9 cm；厚 0.5 cm",
                    "evidence": [],
                },
                "morphological_description": {
                    "value": (
                        "浅灰色粉砂质泥岩。通体打磨,刃角稍残。，。10件。"
                        "T02037):19,青灰色粉砂质泥岩。（图4-17、3"
                    ),
                    "evidence": [],
                },
                "figure_caption": {"value": "图4-16,4", "evidence": []},
            },
            "link_hints": {"artifact_ids": ["T03022:3"]},
            "region_ids": ["body-text"],
            "relation_ids": [],
            "warnings": ["已从非彩图正文 OCR 恢复器物卡文本来源"],
        }
    ]
    regions = [
        {
            "id": "body-text",
            "page": 214,
            "kind": "text",
            "bbox": [0.10, 0.20, 0.93, 0.23],
            "text": (
                "刃角残。长4.2、宽2.1、厚0.7厘米（图4-16，4；彩版六八，3）。"
                "T0302②：3，浅灰色粉砂质泥岩。通体打磨，刃角稍残。"
                "长3.1、宽1.9、厚0.5厘米（图4-16，5；彩版六八，4）。"
            ),
        },
        {
            "id": "next-artifact-text",
            "page": 214,
            "kind": "text",
            "bbox": [0.10, 0.23, 0.93, 0.26],
            "text": "10件。T02037）：19，青灰色粉砂质泥岩。截面呈菱形。",
        },
    ]

    output = service.fuse(
        job_id="job-rematch-pollution",
        records=records,
        regions=regions,
        relations=[],
        config=_basic_config(),
        model_run_id="run-rematch-pollution",
    )

    assert len(output.records) == 1
    record = output.records[0]
    fields = record.get("fields", {})
    description = str(fields.get("morphological_description", {}).get("value") or "")
    measurements = str(fields.get("measurements", {}).get("value") or "")
    caption = str(fields.get("figure_caption", {}).get("value") or "")
    assert "T02037" not in description
    assert "图4-17" not in description
    assert "通体打磨" in description
    assert "4.2" not in measurements
    assert "3.1" in measurements
    assert "图4-16,5" in caption.replace(" ", "").replace("，", ",")
    assert "图4-16,4" not in caption.replace(" ", "").replace("，", ",")


def test_regression_figure_item_recovers_garbled_identifier_label() -> None:
    service = ResultFusionService()
    records = [
        {
            "record_type": "artifact",
            "source_pages": [207],
            "fields": {
                "artifact_id": {
                    "value": "T03022:34",
                    "evidence": [
                        {
                            "page": 207,
                            "bbox": [0.06, 0.54, 0.89, 0.57],
                            "region_id": "body-207",
                        }
                    ],
                },
                "category": {"value": "标本", "evidence": []},
            },
            "link_hints": {
                "artifact_ids": ["T03022:34"],
                "figure_refs": ["图4-10"],
                "figure_item_nos": ["3"],
            },
            "region_ids": ["body-207"],
            "relation_ids": [],
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "body-207",
            "page": 207,
            "kind": "text",
            "bbox": [0.06, 0.54, 0.89, 0.57],
            "text": (
                "标本T0302（②：34，夹砂红陶。素面，截面略呈高方形。"
                "高10.4、厚1.9厘米（图4-10，3）。"
            ),
        },
        {
            "id": "garbled-label-text",
            "page": 208,
            "kind": "text",
            "bbox": [0.40, 0.28, 0.51, 0.30],
            "text": "3.102022:34",
        },
        {
            "id": "garbled-label-caption",
            "page": 208,
            "kind": "caption",
            "bbox": [0.39, 0.27, 0.66, 0.31],
            "text": "4.T04022:43 3.102022:34",
        },
        {
            "id": "figure-title",
            "page": 208,
            "kind": "caption",
            "bbox": [0.30, 0.53, 0.72, 0.57],
            "text": "图4-10 地层堆积内出土陶侧扁鼎足",
        },
        {
            "id": "artifact-208-3",
            "page": 208,
            "kind": "artifact",
            "bbox": [0.39, 0.10, 0.52, 0.29],
            "crop_object_key": "documents/demo/pages/0208/crops/artifact/item-3.png",
        },
    ]

    output = service.fuse(
        job_id="job-garbled-label",
        records=records,
        regions=regions,
        relations=[],
        config=_basic_config(),
        model_run_id="run-garbled-label",
    )

    record = output.records[0]
    assert ResultFusionService._artifact_identifier_from_text(
        "标本T0302（②：34，夹砂红陶"
    ) == "T03022:34"
    assert record["fields"].get("category", {}).get("value") not in {"标本", "标本T03022:34"}
    assert record["text_evidence"][0]["region_id"] == "body-207"
    assert record["primary_artifact_region_id"] == "artifact-208-3"
    assert record["thumbnail_region_id"] == "artifact-208-3"
    assert any(
        region.get("source") == "ocr_label_inference"
        and region.get("sequence_no") == 3
        for region in regions
    )
