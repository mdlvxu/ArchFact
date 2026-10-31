import asyncio
from types import SimpleNamespace

import pytest

import app.services.quality_evaluation_service as quality_module
from app.core.errors import DomainError
from app.main import app
from app.services.quality_catalog import select_preview_cards
from app.services.quality_evaluation_service import (
    QualityEvaluationService,
    compare_field,
    detection_metric,
    linked_color_plate_region_id,
)
from app.services.quality_semantics import score_semantic_pairs


def test_quality_evaluation_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]
    base = "/api/v1/extraction-jobs/{job_id}/quality-evaluations"

    assert "post" in paths[base]
    assert "get" in paths[base]
    assert "get" in paths[f"{base}/{{evaluation_id}}"]
    assert "get" in paths[f"{base}/{{evaluation_id}}/items"]


def test_measurements_are_compared_by_normalized_numeric_anchors() -> None:
    result = compare_field("measurements", "口径10 cm，高5.0厘米", "口径10厘米 高5cm")

    assert result["verdict"] == "matched"
    assert result["score"] == 1.0
    same_numbers_with_optional_unit = compare_field(
        "measurements", "长2.3 cm；长1.1 cm", "长2.3，边长1.1cm"
    )
    assert same_numbers_with_optional_unit["verdict"] == "matched"
    assert same_numbers_with_optional_unit["score"] == 1.0
    assert compare_field("measurements", "口径1厘米", "口径10厘米")["verdict"] == "mismatched"
    assert compare_field("measurements", "口径50毫米", "口径5厘米")["verdict"] == "matched"


def test_unannotated_human_field_is_not_scored_as_machine_error() -> None:
    assert compare_field("surface_color", "红色", None)["verdict"] == "not_applicable"


def test_figure_caption_compares_structured_line_and_color_references() -> None:
    result = compare_field("figure_caption", "彩版一四,2；图3-2F", "图3-2F;彩版一四,2")
    assert result["verdict"] == "matched"


def test_color_plate_region_can_be_found_through_relation() -> None:
    region_id = linked_color_plate_region_id(
        {"region_ids": [], "relation_ids": ["link-1"]},
        {"plate-1": {"kind": "color_plate"}},
        {"link-1": {"source_region_id": "crop-1", "target_region_id": "plate-1"}},
    )
    assert region_id == "plate-1"


def test_preview_cohort_keeps_one_crop_bound_representative_per_entity() -> None:
    records = [
        {
            "_id": "r1",
            "entity_id": "e1",
            "source_pages": [1],
            "fields": {"artifact_id": {"value": "M1:1"}},
        },
        {
            "_id": "r2",
            "entity_id": "e1",
            "source_pages": [2],
            "primary_artifact_region_id": "crop",
            "fields": {"artifact_id": {"value": "M1:1"}, "measurements": {"value": "高10厘米"}},
        },
        {
            "_id": "r3",
            "entity_id": "e2",
            "source_pages": [3],
            "fields": {"artifact_id": {"value": "M2:1"}, "measurements": {"value": "高5厘米"}},
        },
    ]
    assert [record["_id"] for record in select_preview_cards(records)] == ["r2"]


def test_preview_cohort_can_use_text_sibling_when_entity_has_a_crop() -> None:
    records = [
        {"_id": "body", "entity_id": "e1", "source_pages": [1],
         "fields": {"artifact_id": {"value": "M1:1"},
                    "morphological_description": {"value": "圆口，斜腹，平底"},
                    "measurements": {"value": "口径10厘米"}}},
        {"_id": "drawing", "entity_id": "e1", "source_pages": [2],
         "primary_artifact_region_id": "crop", "fields": {"artifact_id": {"value": "M1:1"}}},
    ]
    assert [record["_id"] for record in select_preview_cards(records)] == ["body"]


def test_evaluation_is_human_left_join_and_preserves_duplicate_gold_rows() -> None:
    result = QualityEvaluationService._evaluate(
        job={"effective_pages": [1], "requested_pages": [1]},
        document={"page_count": 1},
        records=[
            {"_id": "machine-1", "source_pages": [1], "fields": {"artifact_id": {"value": "M1:1"}}}
        ],
        gold_records=[
            {"_id": "gold-1", "canonical_artifact_id": "M1,1", "fields": {"artifact_id": "M1,1"}},
            {"_id": "gold-2", "canonical_artifact_id": "M2,1", "fields": {"artifact_id": "M2,1"}},
            {"_id": "gold-3", "canonical_artifact_id": "M2,1", "fields": {"artifact_id": "M2,1"}},
        ],
        regions=[],
        relations=[],
        gold_regions=[],
        gold_links=[],
        pages=[],
    )
    assert len(result["items"]) == 3
    assert [item["match_status"] for item in result["items"]] == [
        "matched",
        "ambiguous",
        "ambiguous",
    ]
    assert result["summary"]["matched_records"] == 1


def test_semantic_comparison_never_falls_back_to_character_ratio() -> None:
    with pytest.raises(DomainError, match="LLM_API_KEY"):
        asyncio.run(
            score_semantic_pairs(
                SimpleNamespace(llm_api_key=None),
                [{"id": "g1:morphological_description", "machine": "陶罐", "human": "陶罐"}],
            )
        )


def test_llm_judgment_replaces_character_score_in_saved_result(monkeypatch) -> None:
    result = QualityEvaluationService._evaluate(
        job={"effective_pages": [1]},
        document={"page_count": 1},
        records=[
            {
                "_id": "m1",
                "source_pages": [1],
                "fields": {
                    "artifact_id": {"value": "M1:1"},
                    "morphological_description": {"value": "圆底，弧腹"},
                },
            }
        ],
        gold_records=[
            {
                "_id": "g1",
                "canonical_artifact_id": "M1,1",
                "fields": {
                    "artifact_id": "M1,1",
                    "morphological_description": "弧形腹部，底部为圆形",
                },
            }
        ],
        regions=[],
        relations=[],
        gold_regions=[],
        gold_links=[],
        pages=[],
    )

    async def fake_score(_settings, pairs):
        assert pairs[0]["id"] == "g1:morphological_description"
        return {pairs[0]["id"]: {"matched": True, "score": 0.93, "reason": "含义一致"}}

    monkeypatch.setattr(quality_module, "score_semantic_pairs", fake_score)
    service = QualityEvaluationService(repository=None, dispatcher=None, settings=SimpleNamespace())
    asyncio.run(service._score_free_text(result))
    field = next(
        field
        for field in result["items"][0]["field_results"]
        if field["key"] == "morphological_description"
    )
    assert field["score"] == 0.93
    assert field["method"] == "semantic_llm"
    assert field["reason"] == "含义一致"


def test_semantic_score_ignores_unmatched_gold_fields(monkeypatch) -> None:
    result = {
        "items": [
            {
                "gold_record_id": "g1",
                "match_status": "matched",
                "field_results": [
                    {
                        "key": "morphological_description",
                        "verdict": "mismatched",
                        "score": 0.2,
                        "actual": "弧腹",
                        "expected": "弧形腹部",
                    }
                ],
            },
            {
                "gold_record_id": "g2",
                "match_status": "not_found",
                "field_results": [
                    {
                        "key": "morphological_description",
                        "verdict": "not_evaluated",
                        "score": None,
                        "actual": None,
                        "expected": "弧腹",
                    }
                ],
            },
        ],
        "field_metrics": [{"key": "morphological_description"}],
        "summary": {},
    }

    async def fake_score(_settings, pairs):
        assert [pair["id"] for pair in pairs] == ["g1:morphological_description"]
        return {pairs[0]["id"]: {"matched": True, "score": 0.93, "reason": "含义一致"}}

    monkeypatch.setattr(quality_module, "score_semantic_pairs", fake_score)
    service = QualityEvaluationService(repository=None, dispatcher=None, settings=SimpleNamespace())

    asyncio.run(service._score_free_text(result))

    metric = result["field_metrics"][0]
    assert metric["evaluated"] == 1
    assert metric["matched"] == 1
    assert metric["score"] == 0.93


def test_detection_metric_uses_one_to_one_iou_matching() -> None:
    metric = detection_metric(
        kind="artifact",
        predicted=[{"page": 1, "bbox": [0.1, 0.1, 0.3, 0.3]}],
        gold=[{"page": 1, "bbox": [0.1, 0.1, 0.3, 0.3]}],
    )

    assert metric["matched_count"] == 1
    assert metric["precision"] == 1.0
    assert metric["recall"] == 1.0


def test_quality_report_separates_field_ocr_detection_and_relation_metrics() -> None:
    result = QualityEvaluationService._evaluate(
        job={"effective_pages": [1], "requested_pages": [1]},
        document={"page_count": 2},
        records=[
            {
                "_id": "record-1",
                "evaluation_crop_region_id": "artifact-1",
                "source_pages": [1],
                "associated_pages": [1],
                "linkage": {"identity": {"artifact_id_normalized": "M1,1"}},
                "fields": {
                    "artifact_id": {"value": "M1:1"},
                    "measurements": {"value": "口径10 cm"},
                    "figure_caption": {"value": "图1"},
                    "category": {"value": "陶器"},
                },
                "region_ids": ["artifact-1", "plate-1"],
                "relation_ids": ["relation-1"],
            }
        ],
        gold_records=[
            {
                "_id": "gold-1",
                "canonical_artifact_id": "M1,1",
                "fields": {
                    "artifact_id": "M1,1",
                    "measurements": "口径10厘米",
                    "figure_caption": "图1",
                    "category": "陶器",
                },
            }
        ],
        regions=[
            {"_id": "artifact-1", "page": 1, "kind": "artifact", "bbox": [0.1, 0.1, 0.3, 0.3]},
            {"_id": "plate-1", "page": 1, "kind": "color_plate", "bbox": [0.5, 0.5, 0.7, 0.7]},
        ],
        relations=[{"_id": "relation-1"}],
        gold_regions=[
            {"_id": "gold-region-1", "page": 1, "kind": "artifact", "bbox": [0.1, 0.1, 0.3, 0.3]}
        ],
        gold_links=[
            {"record_id": "gold-1", "link_type": "artifact_crop"},
            {"record_id": "gold-1", "link_type": "color_plate"},
        ],
        pages=[{"page_no": 1, "ocr_text": "M1:1，口径10厘米，图1"}],
    )

    assert result["summary"]["matched_records"] == 1
    assert result["summary"]["artifact_id_precision"] == 1.0
    assert result["summary"]["artifact_id_recall"] is None
    assert result["summary"]["ocr_anchor_score"] == 1.0
    assert result["summary"]["relation_score"] == 1.0
    assert result["summary"]["detection_macro_f1"] == 1.0
    assert result["items"][0]["match_status"] == "matched"
    assert result["items"][0]["crop_region_id"] == "artifact-1"
