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


def test_fusion_completes_wrapped_figure_caption_without_consuming_next_artifact() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="figure_caption", label="Figure Caption", type="string"),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [160],
            "fields": {
                "artifact_id": {
                    "value": "M13:8",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 160,
                            "quote": "M13：8",
                            "bbox": [0.1013, 0.5697, 0.8780, 0.5887],
                            "region_id": "text-main",
                            "kind": "text",
                        }
                    ],
                },
                "figure_caption": {
                    "raw_value": "图3-14B",
                    "value": "图3-14B",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 160,
                            "quote": "图3-14B",
                            "bbox": [0.1013, 0.5697, 0.8780, 0.5887],
                            "region_id": "text-main",
                            "kind": "text",
                        }
                    ],
                },
            },
            "link_hints": {"caption_texts": ["图3-14B"]},
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "text-main",
            "page": 160,
            "kind": "text",
            "text": "M13：8，玉珠。白色闪玉。腰鼓形，一端破损。高1.8、直径1.4厘米。（图3-14B；",
            "bbox": [0.1013, 0.5697, 0.8780, 0.5887],
            "source": "paddleocr",
        },
        {
            "id": "text-continuation",
            "page": 160,
            "kind": "text",
            "text": "彩版四九，2）",
            "bbox": [0.0607, 0.5916, 0.1940, 0.6116],
            "source": "paddleocr",
        },
        {
            "id": "text-next-artifact",
            "page": 160,
            "kind": "text",
            "text": "M13：9，玉镯。乳白色闪玉。",
            "bbox": [0.1027, 0.6188, 0.8887, 0.6331],
            "source": "paddleocr",
        },
    ]

    output = service.fuse(
        job_id="job-m13-8",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-fusion",
    )

    caption = output.records[0]["fields"]["figure_caption"]
    assert caption["raw_value"] == "（图3-14B；彩版四九，2）"
    assert caption["value"] == "图3-14B;彩版四九,2"
    assert [evidence["region_id"] for evidence in caption["evidence"]] == [
        "text-main",
        "text-continuation",
    ]
    assert "text-next-artifact" not in {
        evidence["region_id"] for evidence in caption["evidence"]
    }
    assert "text-continuation" in output.records[0]["region_ids"]
    assert "text-next-artifact" not in output.records[0]["region_ids"]
    assert [
        evidence["region_id"] for evidence in output.records[0]["text_evidence"]
    ] == ["text-main", "text-continuation"]
    assert "图3-14B;彩版四九,2" in output.records[0]["link_hints"]["caption_texts"]


def test_fusion_builds_complete_artifact_paragraph_until_next_identifier() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="measurements", label="Measurements", type="string"),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [160],
            "fields": {
                "artifact_id": {
                    "value": "M13:15",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 160,
                            "quote": "M13：15",
                            "bbox": [0.104, 0.8540, 0.8767, 0.8688],
                            "region_id": "m13-15-main",
                            "kind": "text",
                        }
                    ],
                },
                "measurements": {
                    "value": "口径 9 cm",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 160,
                            "quote": "口径9、",
                            "bbox": [0.104, 0.8540, 0.8767, 0.8688],
                            "region_id": "m13-15-main",
                            "kind": "text",
                        }
                    ],
                },
            },
            "link_hints": {"artifact_ids": ["M13:15"]},
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "m13-15-main",
            "page": 160,
            "kind": "text",
            "text": (
                "M13：15，陶尊。泥质灰陶。直口略卷，尖唇，直腹微鼓，"
                "底近平，圈足残。口径9、"
            ),
            "bbox": [0.104, 0.8540, 0.8767, 0.8688],
            "source": "paddleocr",
        },
        {
            "id": "m13-15-continuation",
            "page": 160,
            "kind": "text",
            "text": "残高12厘米。（图3-14B；彩版五O，4）",
            "bbox": [0.0627, 0.8783, 0.4447, 0.8927],
            "source": "paddleocr",
        },
        {
            "id": "m13-16",
            "page": 160,
            "kind": "text",
            "text": "M13：16，玉珠。白色闪玉。矮腰鼓形。",
            "bbox": [0.1027, 0.9012, 0.8033, 0.9156],
            "source": "paddleocr",
        },
    ]

    output = service.fuse(
        job_id="job-m13-15",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    assert [evidence["region_id"] for evidence in record["text_evidence"]] == [
        "m13-15-main",
        "m13-15-continuation",
    ]
    assert [evidence["quote"] for evidence in record["text_evidence"]] == [
        (
            "M13：15，陶尊。泥质灰陶。直口略卷，尖唇，直腹微鼓，"
            "底近平，圈足残。口径9、"
        ),
        "残高12厘米。（图3-14B；彩版五O，4）",
    ]
    assert "m13-15-continuation" in record["region_ids"]
    assert "m13-16" not in record["region_ids"]
    assert record["fields"]["measurements"]["raw_value"] == "口径9、残高12厘米"
    assert record["fields"]["measurements"]["value"] == "口径 9 cm；残高 12 cm"
    assert [
        evidence["region_id"]
        for evidence in record["fields"]["measurements"]["evidence"]
    ] == ["m13-15-main", "m13-15-continuation"]
    assert record["fields"]["category"]["value"] == "陶尊"
    assert record["fields"]["texture"]["value"] == "泥质灰陶"
    assert "直口略卷" in record["fields"]["morphological_description"]["value"]
    assert "圈足残" in record["fields"]["morphological_description"]["value"]


def test_fusion_upgrades_truncated_morphology_and_stops_at_mashed_next_id() -> None:
    """LLM often keeps only ``片状`` while OCR continues across wrapped lines.

    Also stop at OCR-collapsed next IDs such as ``M138.`` so measurements from the
    following artifact are not absorbed.
    """

    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="ID", type="string"),
            ExtractionFieldSpec(key="category", label="Category", type="string"),
            ExtractionFieldSpec(key="texture", label="Texture", type="string"),
            ExtractionFieldSpec(
                key="morphological_description",
                label="Morphology",
                type="string",
            ),
            ExtractionFieldSpec(key="measurements", label="Measurements", type="string"),
            ExtractionFieldSpec(key="figure_caption", label="Figure", type="string"),
        ],
    )
    missing = {"raw_value": None, "value": None, "status": "missing", "evidence": []}
    records = [
        {
            "record_type": "artifact",
            "source_pages": [137],
            "fields": {
                "artifact_id": {
                    "raw_value": "M1：37",
                    "value": "M1:37",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 137,
                            "quote": "M1：37",
                            "bbox": [0.1367, 0.1169, 0.9167, 0.1312],
                            "region_id": "m1-37-l1",
                            "kind": "text",
                        }
                    ],
                },
                "category": {
                    "raw_value": "玉梳背",
                    "value": "玉梳背",
                    "status": "valid",
                    "evidence": [],
                },
                "texture": {
                    "raw_value": "南瓜黄闪玉",
                    "value": "南瓜黄闪玉",
                    "status": "valid",
                    "evidence": [],
                },
                "morphological_description": {
                    "raw_value": "片状",
                    "value": "片状",
                    "status": "valid",
                    "evidence": [],
                },
                "measurements": {
                    "raw_value": "宽4.9、高3.7、厚0.4、长8、直径1.1厘米",
                    "value": "宽 4.9 cm；高 3.7 cm；厚 0.4 cm；长 8 cm；直径 1.1 cm",
                    "status": "valid",
                    "evidence": [],
                },
                "figure_caption": dict(missing),
            },
            "link_hints": {"artifact_ids": ["M1:37"]},
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "m1-37-l1",
            "page": 137,
            "kind": "text",
            "text": "M1：37、玉梳背。南瓜黄闪玉，强风化，大部分受沁变白，局部尚存黄褐色原质。片状，",
            "bbox": [0.1367, 0.1169, 0.9167, 0.1312],
            "source": "paddleocr",
        },
        {
            "id": "m1-37-l2",
            "page": 137,
            "kind": "text",
            "text": "倒梯形、一面有线切割凹痕。顶端中央凹缺，缺口内作“弓”字形凸起，凸块下方有穿孔，",
            "bbox": [0.12, 0.1374, 0.92, 0.152],
            "source": "paddleocr",
        },
        {
            "id": "m1-37-l3",
            "page": 137,
            "kind": "text",
            "text": "双面钻。两侧边斜直，两下角弧切，底端有较宽的凸桦，上无穿孔。宽4.9、高3.7、厚0.4~0.6",
            "bbox": [0.12, 0.1627, 0.92, 0.177],
            "source": "paddleocr",
        },
        {
            "id": "m1-37-l4",
            "page": 137,
            "kind": "text",
            "text": "厘米。（图3-2D：彩版一二.8）",
            "bbox": [0.12, 0.1875, 0.55, 0.202],
            "source": "paddleocr",
        },
        {
            "id": "m1-38-mashed",
            "page": 137,
            "kind": "text",
            "text": "M138.玉锥形饰。南瓜黄玉，强风化。圆柱体较粗。长8、直径1.1厘米。（图",
            "bbox": [0.134, 0.2085, 0.921, 0.227],
            "source": "paddleocr",
        },
    ]

    output = service.fuse(
        job_id="job-m1-37-truncated",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    morphology = record["fields"]["morphological_description"]["value"]
    assert morphology.startswith("强风化") or "片状" in morphology
    assert "倒梯形" in morphology
    assert "弓" in morphology
    assert "双面钻" in morphology
    assert len(morphology) > 20
    assert [evidence["region_id"] for evidence in record["text_evidence"]] == [
        "m1-37-l1",
        "m1-37-l2",
        "m1-37-l3",
        "m1-37-l4",
    ]
    assert "m1-38-mashed" not in record["region_ids"]
    measurements = record["fields"]["measurements"]["value"]
    assert "宽 4.9 cm" in measurements
    assert "高 3.7 cm" in measurements
    assert "长 8" not in measurements
    assert "直径 1.1" not in measurements


def test_fusion_backfills_descriptive_fields_when_llm_only_keeps_artifact_id() -> None:
    """Sparse LLM output like M1:37 must not leave cards empty when OCR has prose."""

    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="ID", type="string"),
            ExtractionFieldSpec(key="category", label="Category", type="string"),
            ExtractionFieldSpec(key="texture", label="Texture", type="string"),
            ExtractionFieldSpec(
                key="morphological_description",
                label="Morphology",
                type="string",
            ),
            ExtractionFieldSpec(key="measurements", label="Measurements", type="string"),
        ],
    )
    missing = {"raw_value": None, "value": None, "status": "missing", "evidence": []}
    records = [
        {
            "record_type": "artifact",
            "source_pages": [88],
            "fields": {
                "artifact_id": {
                    "raw_value": "M1：37",
                    "value": "M1:37",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 88,
                            "quote": "M1：37",
                            "bbox": [0.1, 0.2, 0.9, 0.22],
                            "region_id": "m1-37-main",
                            "kind": "text",
                        }
                    ],
                },
                "category": dict(missing),
                "texture": dict(missing),
                "morphological_description": dict(missing),
                "measurements": dict(missing),
            },
            "link_hints": {"artifact_ids": ["M1:37"]},
            "warnings": [],
        }
    ]
    regions = [
        {
            "id": "m1-37-main",
            "page": 88,
            "kind": "text",
            "text": "M1：37，陶尊。泥质黑皮陶。高领，折沿，圜底，喇叭形圈足较高。",
            "bbox": [0.1, 0.2, 0.9, 0.22],
            "source": "paddleocr",
        },
        {
            "id": "m1-37-continuation",
            "page": 88,
            "kind": "text",
            "text": "口径13.4、高22厘米。（图3-2C）",
            "bbox": [0.08, 0.23, 0.6, 0.25],
            "source": "paddleocr",
        },
        {
            "id": "m1-38",
            "page": 88,
            "kind": "text",
            "text": "M1：38，玉珠。白色闪玉。矮腰鼓形。",
            "bbox": [0.1, 0.27, 0.8, 0.29],
            "source": "paddleocr",
        },
    ]

    output = service.fuse(
        job_id="job-m1-37-sparse",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-fusion",
    )

    record = output.records[0]
    assert record["fields"]["artifact_id"]["value"] == "M1:37"
    assert record["fields"]["category"]["value"] == "陶尊"
    assert record["fields"]["texture"]["value"] == "泥质黑皮陶"
    morphology = record["fields"]["morphological_description"]["value"]
    assert "高领" in morphology
    assert "喇叭形圈足较高" in morphology
    assert "M1:37" not in morphology
    assert "口径" not in morphology
    assert record["fields"]["measurements"]["value"] == "口径 13.4 cm；高 22 cm"
    assert "m1-38" not in record["region_ids"]


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


def test_fusion_corrects_ocr_identifier_and_merges_wrapped_artifact_record() -> None:
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
            ),
            ExtractionFieldSpec(key="figure_caption", label="Figure", type="string"),
            ExtractionFieldSpec(key="measurements", label="Measurements", type="string"),
            ExtractionFieldSpec(
                key="morphological_description",
                label="Description",
                type="string",
            ),
        ],
    )
    missing = {
        "raw_value": None,
        "value": None,
        "status": "missing",
        "evidence": [],
    }
    records = [
        {
            "record_type": "artifact",
            "source_pages": [132],
            "warnings": [],
            "fields": {
                "artifact_id": {
                    "raw_value": "MI：19",
                    "value": "MI:19",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "MI：19",
                            "bbox": [0.098, 0.29, 0.88, 0.305],
                            "region_id": "body-line",
                        }
                    ],
                },
                "figure_caption": dict(missing),
                "measurements": dict(missing),
                "morphological_description": {
                    "raw_value": "高领，折沿，圜底，喇",
                    "value": "高领,折沿,圜底,喇",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "高领，折沿，圜底，喇",
                            "bbox": [0.098, 0.29, 0.88, 0.305],
                            "region_id": "body-line",
                        }
                    ],
                },
            },
            "link_hints": {
                "artifact_ids": ["MI:19"],
                "figure_refs": [],
                "figure_item_nos": [],
                "plate_refs": [],
                "caption_texts": [],
                "aliases": [],
            },
        },
        {
            "record_type": "artifact",
            "source_pages": [132],
            "warnings": [],
            "fields": {
                "artifact_id": dict(missing),
                "figure_caption": {
                    "raw_value": "图3-2C；彩版九，3、4",
                    "value": "图3-2C;彩版9,3、4",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "图3-2C；彩版九，3、4",
                            "bbox": [0.056, 0.314, 0.664, 0.329],
                            "region_id": "body-continuation",
                        }
                    ],
                },
                "measurements": {
                    "raw_value": "口径13.4、高22厘米",
                    "value": "口径 13.4 cm;高 22 cm",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "口径13.4、高22厘米",
                            "bbox": [0.056, 0.314, 0.664, 0.329],
                            "region_id": "body-continuation",
                        }
                    ],
                },
                "morphological_description": {
                    "raw_value": "叭形圈足较高。",
                    "value": "叭形圈足较高.",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "叭形圈足较高。",
                            "bbox": [0.056, 0.314, 0.664, 0.329],
                            "region_id": "body-continuation",
                        }
                    ],
                },
            },
            "link_hints": {
                "artifact_ids": [],
                "figure_refs": ["图3-2C"],
                "figure_item_nos": [],
                "plate_refs": ["彩版九-3、4"],
                "caption_texts": ["图3-2C；彩版九，3、4"],
                "aliases": [],
            },
        },
        {
            "record_type": "artifact_color_plate",
            "source_pages": [26],
            "warnings": [],
            "fields": {
                "artifact_id": {
                    "raw_value": "M1：19",
                    "value": "M1:19",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 26,
                            "quote": "M1：19",
                            "bbox": [0.2, 0.89, 0.35, 0.91],
                            "region_id": "color-caption",
                        }
                    ],
                },
                "figure_caption": {
                    "raw_value": "3.陶尊（M1：19）",
                    "value": "3.陶尊(M1:19)",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 26,
                            "quote": "3.陶尊（M1：19）",
                            "bbox": [0.2, 0.89, 0.35, 0.91],
                            "region_id": "color-caption",
                        }
                    ],
                },
                "measurements": dict(missing),
                "morphological_description": dict(missing),
            },
            "link_hints": {
                "artifact_ids": ["M1:19"],
                "figure_refs": [],
                "figure_item_nos": ["3"],
                "plate_refs": ["彩版九"],
                "caption_texts": ["3.陶尊（M1：19）"],
                "aliases": [],
            },
        },
    ]
    regions = [
        {
            "id": "body-line",
            "document_id": "doc-1",
            "page": 132,
            "kind": "text",
            "bbox": [0.098, 0.29, 0.88, 0.305],
            "text": "MI：19、陶尊。泥质黑皮陶。高领，折沿，圜底，喇",
        },
        {
            "id": "body-continuation",
            "document_id": "doc-1",
            "page": 132,
            "kind": "text",
            "bbox": [0.056, 0.314, 0.664, 0.329],
            "text": "叭形圈足较高。口径13.4、高22厘米。（图3-2C；彩版九，3、4）",
        },
        {
            "id": "color-caption",
            "document_id": "doc-1",
            "page": 26,
            "kind": "text",
            "bbox": [0.2, 0.89, 0.35, 0.91],
            "text": "3.陶尊（M1：19）",
        },
        {
            "id": "number-126",
            "document_id": "doc-1",
            "page": 126,
            "kind": "number",
            "bbox": [0.45, 0.8, 0.55, 0.84],
            "text": "M1:19",
        },
        {
            "id": "artifact-126",
            "document_id": "doc-1",
            "page": 126,
            "kind": "artifact",
            "bbox": [0.3, 0.2, 0.7, 0.75],
        },
    ]
    relations = [
        {
            "id": "number-of-artifact",
            "source_region_id": "number-126",
            "target_region_id": "artifact-126",
            "relation_type": "number_of",
            "score": 0.95,
        }
    ]

    output = service.fuse(
        job_id="job-color-inferred",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-color-inferred",
        page_metadata={
            26: {"page_type": "color_plate"},
            126: {"page_type": "monochrome_visual"},
            132: {"page_type": "document"},
        },
    )

    body = next(record for record in output.records if 132 in record.get("source_pages", []))
    assert body["fields"]["artifact_id"]["value"] == "M1:19"
    assert body["fields"]["measurements"]["value"] == "口径 13.4 cm；高 22 cm"
    assert body["fields"]["figure_caption"]["value"] == "图3-2C;彩版九,3、4"
    assert "叭形圈足较高" in body["fields"]["morphological_description"]["value"]
    # Color-plate caption cards are linkage-only and must not remain as empty catalog rows.
    assert len(output.records) == 1
    assert 26 in body.get("associated_pages", [])
    assert "color-caption" in body.get("region_ids", [])

    inferred = [
        region
        for region in regions
        if region.get("kind") == "color_plate"
        and region.get("source") == "ocr_identifier_inference"
    ]
    assert len(inferred) == 1
    assert inferred[0]["page"] == 26
    assert inferred[0]["approximate"] is True
    assert any(
        relation.get("relation_type") == "color_plate_of"
        and relation.get("source_region_id") == inferred[0]["id"]
        and relation.get("target_region_id") == "artifact-126"
        for relation in output.relations
    )


def test_fusion_drops_records_extracted_from_illustration_catalog() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="figure_caption", label="Figure", type="string")
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [9],
            "link_hints": {
                "artifact_ids": [],
                "figure_refs": ["图3-2"],
                "figure_item_nos": [],
                "caption_texts": ["图3-2C M1出土陶器"],
                "plate_refs": [],
                "aliases": [],
            },
            "fields": {
                "figure_caption": {
                    "value": "图3-2C M1出土陶器",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 9,
                            "quote": "图3-2C M1出土陶器",
                            "bbox": [0.05, 0.47, 0.23, 0.49],
                            "region_id": "catalog-entry",
                        }
                    ],
                }
            },
        }
    ]
    regions = [
        {
            "id": "catalog-title",
            "page": 9,
            "kind": "text",
            "bbox": [0.39, 0.16, 0.54, 0.19],
            "text": "插图目录",
        },
        {
            "id": "catalog-entry",
            "page": 9,
            "kind": "text",
            "bbox": [0.05, 0.47, 0.23, 0.49],
            "text": "图3-2C M1出土陶器",
        },
        {
            "id": "caption-126",
            "page": 126,
            "kind": "caption",
            "bbox": [0.2, 0.8, 0.7, 0.84],
            "text": "图3-2C M1出土陶器（1/4）",
        },
        {
            "id": "artifact-126",
            "page": 126,
            "kind": "artifact",
            "bbox": [0.3, 0.2, 0.6, 0.7],
        },
    ]
    relations = [
        {
            "id": "caption-of-artifact",
            "source_region_id": "caption-126",
            "target_region_id": "artifact-126",
            "relation_type": "caption_of",
            "score": 0.95,
            "method": "global_assignment",
            "version": "1",
            "review_status": "unreviewed",
        }
    ]

    output = service.fuse(
        job_id="job-catalog",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-fusion",
    )

    assert output.records == []
    assert not any(
        relation.get("relation_type") == "evidence_for"
        and relation.get("source_region_id") == "catalog-entry"
        for relation in output.relations
    )


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


def test_identifier_score_preserves_artifact_number_segments() -> None:
    service = ResultFusionService()

    assert service._artifact_identifier_from_text("10302\u2460:03") == "T03021:03"
    assert (
        service._identifier_text_score("T0302\u2460:03", "10302\u2460:03") == 1.0
    )
    assert service._identifier_text_score("M13:9", "M13：9") == 1.0
    assert service._identifier_text_score("M13 ： 9", "M13:9 玉镯") == 1.0
    assert service._identifier_text_score("M13:9", "M1:39") == 0.0
    assert service._identifier_text_score("M13:9", "M1:40") == 0.0
    assert service._identifier_text_score("M1:5", "M1:59") == 0.0
    assert service._identifier_text_score("M1:6", "MI：6，石钺") == 1.0
    assert service._hint_text_score("artifact_ids", "M1:5", "M1:59") == 0.0
    assert service._hint_text_score("aliases", "M1：5", "M1:59") == 0.0


def test_numbered_crop_rejects_idless_nearest_record_after_t_prefix_recovery() -> None:
    service = ResultFusionService()
    number = {
        "id": "number-t03021-03",
        "page": 270,
        "kind": "number",
        "text": "10302\u2460:03",
        "bbox": [0.47, 0.29, 0.56, 0.31],
    }
    artifact = {
        "id": "artifact-t03021-03",
        "page": 270,
        "kind": "artifact",
        "bbox": [0.40, 0.18, 0.63, 0.29],
    }
    orphan_artifact = {
        "id": "artifact-orphan-270",
        "page": 270,
        "kind": "artifact",
        "bbox": [0.10, 0.18, 0.30, 0.29],
        "crop_object_key": "documents/demo/pages/0270/crops/artifact/orphan.png",
    }
    relation = {
        "id": "number-of-t03021-03",
        "source_region_id": number["id"],
        "target_region_id": artifact["id"],
        "relation_type": "number_of",
    }
    candidate_identifiers, page_identifiers = service._visual_identifier_indexes(
        regions=[number, artifact, orphan_artifact],
        region_by_id={
            number["id"]: number,
            artifact["id"]: artifact,
            orphan_artifact["id"]: orphan_artifact,
        },
        relation_by_id={relation["id"]: relation},
    )

    assert candidate_identifiers[artifact["id"]] == {"T03021:03"}
    assert service._visual_candidate_identifier_compatibility(
        record={
            "fields": {
                "artifact_id": {"value": "T03021:03", "evidence": []},
                "category": {"value": "青瓷碗", "evidence": []},
            }
        },
        candidate=artifact,
        candidate_identifiers_by_id=candidate_identifiers,
        page_number_identifiers=page_identifiers,
    )
    assert (
        service._visual_candidate_identifier_compatibility(
            record={
                "fields": {
                    "artifact_id": {"value": None, "evidence": []},
                    "category": {"value": "韩瓶", "evidence": []},
                }
            },
            candidate=artifact,
            candidate_identifiers_by_id=candidate_identifiers,
            page_number_identifiers=page_identifiers,
        )
        is False
    )
    assert (
        service._visual_candidate_identifier_compatibility(
            record={
                "fields": {
                    "artifact_id": {"value": None, "evidence": []},
                    "category": {"value": "韩瓶", "evidence": []},
                    "completeness": {"value": "完整", "evidence": []},
                }
            },
            candidate=orphan_artifact,
            candidate_identifiers_by_id=candidate_identifiers,
            page_number_identifiers=page_identifiers,
        )
        is False
    )


def test_idless_sparse_record_cannot_nearest_claim_numbered_plate_crop() -> None:
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
                "measurements": {"value": "口径 15.7 cm", "evidence": []},
            },
            "region_ids": ["text-270"],
            "relation_ids": [],
            "source_pages": [270],
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
            "text": "T0302①：03，宋代青瓷碗。",
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
    region_by_id = {region["id"]: region for region in regions}
    relation_by_id = {
        "number-of-270": {
            "id": "number-of-270",
            "source_region_id": "number-270",
            "target_region_id": "artifact-270",
            "relation_type": "number_of",
        }
    }

    matched = service._fuse_nearest_visual_regions(
        job_id="job-demo",
        records=records,
        regions=regions,
        region_by_id=region_by_id,
        relation_by_id=relation_by_id,
        model_run_id="run-demo",
    )

    assert 0 not in matched
    assert "artifact-270" not in records[0]["region_ids"]
    assert matched == {1} or "artifact-270" in records[1].get("region_ids", [])


def test_prune_cross_artifact_evidence_stops_at_next_artifact() -> None:
    record = {
        "fields": {
            "artifact_id": {
                "value": "M1:39",
                "evidence": [{"region_id": "m1-39", "page": 138}],
            },
            "measurements": {
                "value": "长 8.1 cm，直径 1.1 cm",
                "evidence": [
                    {"region_id": "m1-39", "page": 138, "quote": "长8.1，直径1.1"},
                    {"region_id": "wrapped", "page": 138, "quote": "厘米。（图3-2D）"},
                    {
                        "region_id": "m1-40-wrapped",
                        "page": 138,
                        "quote": "长9.3，直径0.6",
                    },
                ],
            },
        },
        "text_evidence": [
            {"region_id": "m1-39", "page": 138},
            {"region_id": "wrapped", "page": 138},
            {"region_id": "m1-40-wrapped", "page": 138},
        ],
    }
    regions = {
        "m1-39": {
            "id": "m1-39",
            "page": 138,
            "kind": "text",
            "text": "M1:39，玉锥形饰。长8.1，直径1.1厘米。",
            "bbox": [0.10, 0.10, 0.90, 0.13],
        },
        "wrapped": {
            "id": "wrapped",
            "page": 138,
            "kind": "text",
            "text": "（图3-2D；彩版二二，4）",
            "bbox": [0.07, 0.14, 0.40, 0.17],
        },
        "m1-40": {
            "id": "m1-40",
            "page": 138,
            "kind": "text",
            "text": "M1:40，玉锥形饰。南瓜黄玉，强风化。",
            "bbox": [0.10, 0.18, 0.90, 0.21],
        },
        "m1-40-wrapped": {
            "id": "m1-40-wrapped",
            "page": 138,
            "kind": "text",
            "text": "长9.3，直径0.6厘米。（图3-2D；彩版二二，5）",
            "bbox": [0.07, 0.22, 0.60, 0.25],
        },
    }

    ResultFusionService.prune_cross_artifact_field_evidence(
        records=[record],
        region_by_id=regions,
    )

    assert [
        evidence["region_id"]
        for evidence in record["fields"]["measurements"]["evidence"]
    ] == ["m1-39", "wrapped"]
    assert [
        evidence["region_id"] for evidence in record["text_evidence"]
    ] == ["m1-39", "wrapped"]


def test_split_record_merge_rejects_fragment_whose_ocr_starts_new_artifact() -> None:
    records = [
        {
            "source_pages": [138],
            "fields": {
                "artifact_id": {
                    "value": "M1:39",
                    "evidence": [{"region_id": "m1-39", "page": 138}],
                }
            },
        },
        {
            "source_pages": [138],
            "fields": {
                "artifact_id": {"value": None, "evidence": []},
                "measurements": {
                    "value": "长 9.3 cm",
                    "evidence": [{"region_id": "m1-40", "page": 138}],
                },
            },
        },
    ]
    regions = [
        {
            "id": "m1-39",
            "page": 138,
            "kind": "text",
            "text": "M1:39，玉锥形饰。长8.1厘米",
            "bbox": [0.1, 0.30, 0.9, 0.33],
        },
        {
            "id": "m1-40",
            "page": 138,
            "kind": "text",
            "text": "M1:40，玉锥形饰。长9.3厘米",
            "bbox": [0.1, 0.34, 0.9, 0.37],
        },
    ]

    merged = ResultFusionService._merge_split_artifact_records(
        records=records,
        regions=regions,
    )

    assert len(merged) == 2
    assert merged[0]["fields"]["artifact_id"]["value"] == "M1:39"
    assert "measurements" not in merged[0]["fields"]


def test_ocr_confused_next_identifier_owns_its_wrapped_measurement_line() -> None:
    record = {
        "fields": {
            "artifact_id": {
                "value": "M1:5",
                "evidence": [{"region_id": "m1-5", "page": 126}],
            },
            "measurements": {
                "value": "口径 19.2 cm；高 16 cm；刃宽 13.3 cm",
                "evidence": [
                    {"region_id": "m1-5-wrapped", "page": 126, "quote": "口径19.2、高16"},
                    {"region_id": "mi-6-wrapped", "page": 126, "quote": "刃宽13.3"},
                ],
            },
        },
    }
    regions = {
        "m1-5": {
            "id": "m1-5",
            "page": 126,
            "kind": "text",
            "text": "M1：5，陶豆。泥质灰陶。",
            "bbox": [0.10, 0.38, 0.88, 0.40],
        },
        "m1-5-wrapped": {
            "id": "m1-5-wrapped",
            "page": 126,
            "kind": "text",
            "text": "口径19.2、高16厘米。（图3-2C）",
            "bbox": [0.06, 0.41, 0.62, 0.43],
        },
        "mi-6": {
            "id": "mi-6",
            "page": 126,
            "kind": "text",
            "text": "MI：6，石钺。浅灰紫色安山岩。",
            "bbox": [0.10, 0.44, 0.88, 0.46],
        },
        "mi-6-wrapped": {
            "id": "mi-6-wrapped",
            "page": 126,
            "kind": "text",
            "text": "长19、刃宽13.3、厚1.4厘米。",
            "bbox": [0.06, 0.47, 0.68, 0.49],
        },
    }

    ResultFusionService.prune_cross_artifact_field_evidence(
        records=[record],
        region_by_id=regions,
    )

    assert ResultFusionService._region_artifact_identifier(regions["mi-6"]) == "M1:6"
    assert [
        evidence["region_id"]
        for evidence in record["fields"]["measurements"]["evidence"]
    ] == ["m1-5-wrapped"]


def test_explicit_short_identifier_cannot_inherit_long_identifier_crop() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [126],
            "fields": {
                "artifact_id": {
                    "value": "M1:5",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 126,
                            "quote": "M1:5",
                            "bbox": [0.1, 0.4, 0.2, 0.42],
                            "region_id": "text-m1-5",
                        }
                    ],
                }
            },
            "link_hints": {
                "artifact_ids": ["M1:5"],
                "aliases": ["M1：5"],
            },
        },
        {
            "record_type": "artifact",
            "source_pages": [187],
            "fields": {
                "artifact_id": {
                    "value": "M1:59",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 187,
                            "quote": "M1:59",
                            "bbox": [0.1, 0.4, 0.2, 0.42],
                            "region_id": "text-m1-59",
                        }
                    ],
                }
            },
            "link_hints": {"artifact_ids": ["M1:59"], "aliases": []},
        },
    ]
    regions = [
        {
            "id": "text-m1-5",
            "page": 126,
            "kind": "text",
            "text": "M1:5，陶豆。",
            "bbox": [0.1, 0.4, 0.2, 0.42],
        },
        {
            "id": "text-m1-59",
            "page": 187,
            "kind": "text",
            "text": "M1:59，陶罐。",
            "bbox": [0.1, 0.4, 0.2, 0.42],
        },
        {
            "id": "number-m1-59",
            "page": 128,
            "kind": "number",
            "text": "M1:59",
            "bbox": [0.45, 0.75, 0.55, 0.80],
        },
        {
            "id": "artifact-m1-59",
            "page": 128,
            "kind": "artifact",
            "bbox": [0.35, 0.2, 0.65, 0.70],
            "crop_object_key": "crops/artifact-m1-59.png",
        },
    ]
    relations = [
        {
            "id": "number-of-m1-59",
            "source_region_id": "number-m1-59",
            "target_region_id": "artifact-m1-59",
            "relation_type": "number_of",
            "score": 0.96,
            "method": "caption_constrained_assignment",
            "review_status": "unreviewed",
        }
    ]

    output = service.fuse(
        job_id="job-prefix-id",
        records=records,
        regions=regions,
        relations=relations,
        config=config,
        model_run_id="run-prefix-id",
    )
    short_record = next(
        record
        for record in output.records
        if record["fields"]["artifact_id"]["value"] == "M1:5"
    )
    long_record = next(
        record
        for record in output.records
        if record["fields"]["artifact_id"]["value"] == "M1:59"
    )

    assert short_record["primary_artifact_region_id"] is None
    assert short_record["thumbnail_region_id"] is None
    assert "artifact-m1-59" not in short_record["region_ids"]
    assert long_record["primary_number_region_id"] == "number-m1-59"
    assert long_record["primary_artifact_region_id"] == "artifact-m1-59"


def test_plate_item_reference_links_body_text_to_color_page_without_artifact_id() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(
                key="figure_caption",
                label="Figure Caption",
                type="string",
            ),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [132],
            "fields": {
                "artifact_id": {
                    "value": "M1:22",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "M1:22",
                            "bbox": [0.1, 0.4, 0.2, 0.42],
                            "region_id": "body-m1-22",
                        }
                    ],
                },
                "figure_caption": {
                    "value": "图3-2J；彩版一八，4",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 132,
                            "quote": "（图3-2J；彩版一八，4）",
                            "bbox": [0.1, 0.43, 0.6, 0.45],
                            "region_id": "body-m1-22-wrapped",
                        }
                    ],
                },
            },
            "link_hints": {
                "artifact_ids": ["M1:22"],
                "plate_refs": ["彩版一八，4"],
            },
        }
    ]
    regions = [
        {
            "id": "body-m1-22",
            "page": 132,
            "kind": "text",
            "text": "M1:22，石钺。青黑色晶屑凝灰岩。",
            "bbox": [0.1, 0.4, 0.8, 0.42],
        },
        {
            "id": "body-m1-22-wrapped",
            "page": 132,
            "kind": "text",
            "text": "长19.4厘米。（图3-2J；彩版一八，4）",
            "bbox": [0.1, 0.43, 0.7, 0.45],
        },
        {
            "id": "plate-18-title",
            "page": 35,
            "kind": "text",
            "text": "彩版一八 M1出土石钺",
            "bbox": [0.3, 0.94, 0.7, 0.97],
        },
        {
            "id": "plate-18-item-4",
            "page": 35,
            "kind": "color_plate",
            "text": "4.石钺",
            "bbox": [0.64, 0.90, 0.79, 0.92],
            "confidence": 0.96,
            "approximate": True,
        },
    ]

    output = service.fuse(
        job_id="job-plate-reference",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-plate-reference",
        page_metadata={35: {"page_type": "color_plate"}},
    )

    record = output.records[0]
    caption_evidence = record["fields"]["figure_caption"]["evidence"][0]
    plate_relations = [
        relation
        for relation in output.relations
        if relation.get("relation_type") == "plate_reference_to_color"
    ]
    assert ResultFusionService._plate_references("彩版一八，4") == {(18, 4)}
    assert "plate-18-item-4" in record["region_ids"]
    assert caption_evidence["linked_region_ids"] == ["plate-18-item-4"]
    assert len(plate_relations) == 1
    assert plate_relations[0]["source_region_id"] == "body-m1-22-wrapped"
    assert plate_relations[0]["target_region_id"] == "plate-18-item-4"
    assert plate_relations[0]["method"] == "exact_plate_item_reference"


def test_plate_item_reference_rejects_conflicting_color_caption_identifier() -> None:
    record = {
        "source_pages": [132],
        "fields": {
            "artifact_id": {
                "value": "M1:22",
                "evidence": [{"region_id": "body", "page": 132}],
            },
            "figure_caption": {
                "value": "彩版一八，4",
                "evidence": [
                    {
                        "region_id": "body",
                        "page": 132,
                        "quote": "彩版一八，4",
                    }
                ],
            },
        },
        "link_hints": {"plate_refs": ["彩版一八，4"]},
    }
    regions = [
        {
            "id": "body",
            "page": 132,
            "kind": "text",
            "text": "M1:22（彩版一八，4）",
            "bbox": [0.1, 0.4, 0.7, 0.43],
        },
        {
            "id": "plate-title",
            "page": 35,
            "kind": "text",
            "text": "彩版一八",
            "bbox": [0.3, 0.94, 0.7, 0.97],
        },
        {
            "id": "wrong-item",
            "page": 35,
            "kind": "color_plate",
            "text": "4.石钺（M1:99）",
            "bbox": [0.64, 0.90, 0.79, 0.92],
        },
    ]
    region_by_id = {region["id"]: region for region in regions}
    relations: dict[str, dict] = {}

    matched = ResultFusionService._fuse_plate_reference_regions(
        job_id="job-conflicting-plate",
        records=[record],
        regions=regions,
        region_by_id=region_by_id,
        relation_by_id=relations,
        model_run_id="run-conflicting-plate",
    )

    assert matched == set()
    assert "wrong-item" not in record.get("region_ids", [])
    assert relations == {}


def test_thumbnail_skips_approximate_color_plate_without_crop() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")
        ],
    )
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
                },
                "category": {"value": "玉锥形饰", "evidence": []},
            },
            "region_ids": ["text-29"],
            "relation_ids": [],
            "warnings": [],
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
        {
            "id": "artifact-crop",
            "page": 29,
            "kind": "artifact",
            "bbox": [0.1, 0.1, 0.4, 0.4],
            "crop_object_key": "documents/demo/pages/0029/crops/artifact/m1-98.png",
        },
    ]

    output = service.fuse(
        job_id="job-thumb",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-thumb",
    )

    assert output.records[0]["thumbnail_region_id"] == "artifact-crop"


def test_thumbnail_is_none_when_only_approximate_plate_exists() -> None:
    service = ResultFusionService()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")
        ],
    )
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
            "region_ids": ["text-29"],
            "relation_ids": [],
            "warnings": [],
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

    output = service.fuse(
        job_id="job-thumb-empty",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-thumb-empty",
    )

    assert output.records[0]["thumbnail_region_id"] is None


def test_normalize_strips_tomb_unit_prefix_from_plate_caption_ids() -> None:
    assert ResultFusionService._normalize_artifact_identifier("仲M4:3") == "M4:3"
    assert ResultFusionService._normalize_artifact_identifier("仲M4：3") == "M4:3"
    assert ResultFusionService._normalize_artifact_identifier("M4:3") == "M4:3"


def test_absorb_drops_sparse_color_plate_caption_cards() -> None:
    """Color-plate captions link to body records; they must not become empty cards."""

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
            ),
            ExtractionFieldSpec(
                key="figure_caption",
                label="Figure caption",
                type="string",
                evidence_kind="caption",
            ),
            ExtractionFieldSpec(
                key="morphological_description",
                label="Morphology",
                type="string",
                evidence_kind="text",
            ),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [146],
            "fields": {
                "artifact_id": {
                    "raw_value": "M4:3",
                    "value": "M4:3",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 146,
                            "quote": "M4:3",
                            "bbox": [0.1, 0.2, 0.3, 0.24],
                            "region_id": "body-id",
                            "kind": "text",
                        }
                    ],
                },
                "morphological_description": {
                    "raw_value": "锥形，断面近圆形。",
                    "value": "锥形，断面近圆形。",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 146,
                            "quote": "锥形，断面近圆形。",
                            "bbox": [0.1, 0.24, 0.8, 0.28],
                            "region_id": "body-morph",
                            "kind": "text",
                        }
                    ],
                },
            },
            "link_hints": {"artifact_ids": ["M4:3"], "plate_refs": ["彩版九,4"]},
            "region_ids": ["body-id", "body-morph"],
            "warnings": [],
        },
        {
            "record_type": "artifact",
            "source_pages": [104],
            "fields": {
                "artifact_id": {
                    "raw_value": "仲M4:3",
                    "value": "仲M4:3",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 104,
                            "quote": "4.玉锥形饰（仲M4：3）",
                            "bbox": [0.2, 0.8, 0.5, 0.84],
                            "region_id": "plate-caption",
                            "kind": "text",
                        }
                    ],
                },
                "category": {
                    "raw_value": "玉锥形饰",
                    "value": "玉锥形饰",
                    "status": "valid",
                    "evidence": [],
                },
                "figure_caption": {
                    "raw_value": "4.玉锥形饰（仲M4：3）",
                    "value": "4.玉锥形饰（仲M4：3）",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 104,
                            "quote": "4.玉锥形饰（仲M4：3）",
                            "bbox": [0.2, 0.8, 0.5, 0.84],
                            "region_id": "plate-caption",
                            "kind": "text",
                        }
                    ],
                },
            },
            "link_hints": {"artifact_ids": ["仲M4:3"]},
            "region_ids": ["plate-caption", "color-plate-104"],
            "warnings": [],
        },
    ]
    regions = [
        {
            "id": "body-id",
            "page": 146,
            "kind": "text",
            "bbox": [0.1, 0.2, 0.3, 0.24],
            "text": "M4:3，玉锥形饰。锥形，断面近圆形。",
        },
        {
            "id": "body-morph",
            "page": 146,
            "kind": "text",
            "bbox": [0.1, 0.24, 0.8, 0.28],
            "text": "锥形，断面近圆形。",
        },
        {
            "id": "plate-caption",
            "page": 104,
            "kind": "text",
            "bbox": [0.2, 0.8, 0.5, 0.84],
            "text": "4.玉锥形饰（仲M4：3）",
        },
        {
            "id": "color-plate-104",
            "page": 104,
            "kind": "color_plate",
            "bbox": [0.15, 0.2, 0.55, 0.75],
        },
    ]

    output = service.fuse(
        job_id="job-m4-3",
        records=records,
        regions=regions,
        relations=[],
        config=config,
        model_run_id="run-m4-3",
        page_metadata={104: {"page_type": "color_plate"}},
    )

    assert len(output.records) == 1
    record = output.records[0]
    assert record["fields"]["artifact_id"]["value"] == "M4:3"
    assert "仲M4" not in str(record["fields"]["artifact_id"]["value"])
    assert record["source_pages"] == [146]
    assert 104 in record.get("associated_pages", [])
    assert "color-plate-104" in record.get("region_ids", [])
    text_quotes = [
        str(item.get("quote") or "")
        for item in record.get("text_evidence", [])
        if isinstance(item, dict)
    ]
    assert all("仲M4" not in quote for quote in text_quotes)
    assert all("4.玉锥形饰" not in quote for quote in text_quotes)
    hints = record.get("link_hints", {})
    assert "仲M4:3" in hints.get("aliases", []) or any(
        "仲M4" in str(alias) for alias in hints.get("aliases", [])
    )
    assert any("彩图页注记仅用于关联" in warning for warning in record.get("warnings", []))


def test_absorb_drops_orphan_color_plate_caption_without_body() -> None:
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
            ),
            ExtractionFieldSpec(
                key="figure_caption",
                label="Figure caption",
                type="string",
                evidence_kind="caption",
            ),
        ],
    )
    records = [
        {
            "record_type": "artifact",
            "source_pages": [104],
            "fields": {
                "artifact_id": {
                    "raw_value": "仲M4:3",
                    "value": "仲M4:3",
                    "status": "valid",
                    "evidence": [],
                },
                "figure_caption": {
                    "raw_value": "4.玉锥形饰（仲M4：3）",
                    "value": "4.玉锥形饰（仲M4：3）",
                    "status": "valid",
                    "evidence": [],
                },
            },
            "warnings": [],
        }
    ]

    output = service.fuse(
        job_id="job-orphan-plate",
        records=records,
        regions=[
            {
                "id": "color-plate-104",
                "page": 104,
                "kind": "color_plate",
                "bbox": [0.1, 0.1, 0.5, 0.5],
            }
        ],
        relations=[],
        config=config,
        model_run_id="run-orphan",
        page_metadata={104: {"page_type": "color_plate"}},
    )

    assert output.records == []
