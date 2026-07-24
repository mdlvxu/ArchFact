from app.main import app
from app.services.quality_evaluation_service import (
    QualityEvaluationService,
    compare_field,
    detection_metric,
)


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
