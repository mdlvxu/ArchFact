from app.main import app
from app.models.schemas import RematchChangesView, RematchCreate, VerificationItemUpdate
from app.repositories.mongo_repository import MongoRepository
from app.services.rematch_service import RematchService


def test_rematch_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]
    base = "/api/v1/extraction-jobs/{job_id}/rematches"

    assert "post" in paths[base]
    assert "get" in paths[f"{base}/{{rematch_id}}"]
    assert "get" in paths[f"{base}/{{rematch_id}}/report"]
    assert "get" in paths[f"{base}/{{rematch_id}}/changes"]
    assert "post" in paths[f"{base}/{{rematch_id}}/apply"]
    assert "post" in paths[f"{base}/{{rematch_id}}/cancel"]


def test_rematch_defaults_to_preview_and_preserves_reviewed_links() -> None:
    payload = RematchCreate()

    assert payload.apply_immediately is False
    assert payload.preserve_reviewed is True


def test_verification_failure_code_distinguishes_relation_errors() -> None:
    item = VerificationItemUpdate(
        verdict="failed",
        failure_code="caption_match_error",
        failure_reason="图注指向了错误的序号",
    )

    assert item.failure_code == "caption_match_error"


def test_only_relation_failures_release_the_existing_chain_for_rematching() -> None:
    assert MongoRepository._verification_protects_chain("passed", None)
    assert MongoRepository._verification_protects_chain("failed", "field_error")
    assert MongoRepository._verification_protects_chain("failed", "other")
    assert not MongoRepository._verification_protects_chain(
        "failed",
        "caption_match_error",
    )
    assert not MongoRepository._verification_protects_chain(
        "failed",
        "number_match_error",
    )


def test_rematch_report_contains_relation_delta_and_quality_counts() -> None:
    report = RematchService._build_report(
        baseline_relations=[
            {
                "_id": "rel-same",
                "source_region_id": "caption-1",
                "target_region_id": "number-1",
                "method": "caption_scope_fallback",
                "score": 0.62,
            },
            {
                "_id": "rel-removed",
                "source_region_id": "number-2",
                "target_region_id": "artifact-2",
                "method": "directional_assignment",
                "score": 0.6,
            },
        ],
        candidate_relations=[
            {
                "id": "rel-same",
                "source_region_id": "caption-1",
                "target_region_id": "number-1",
                "method": "caption_ocr_scope",
                "score": 0.95,
            },
            {
                "id": "rel-added",
                "source_region_id": "number-1",
                "target_region_id": "artifact-1",
                "method": "caption_ocr_constrained_assignment",
                "score": 0.9,
            },
        ],
        candidate_records=[
            {
                "fusion_status": "linked",
                "region_ids": ["caption-1", "number-1", "artifact-1"],
            }
        ],
        regions=[
            {"id": "caption-1", "kind": "caption"},
            {"id": "number-1", "kind": "number"},
            {"id": "artifact-1", "kind": "artifact"},
        ],
        protection={
            "accepted_relation_ids": {"rel-manual"},
            "rejected_relation_keys": {("caption-x", "number-x", "caption_to_number")},
            "passed_record_ids": {"record-1"},
            "protected_relation_ids": {"rel-manual", "rel-pass"},
        },
        conflict_relations=1,
    )

    assert report["complete_chains"] == 1
    assert report["delta"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 0}
    assert report["confidence"]["high"] == 2
    assert report["protection"]["passed_records"] == 1
    assert report["conflict_relations"] == 1


def test_rematch_change_contract_exposes_auditable_relation_details() -> None:
    result = RematchChangesView.model_validate(
        {
            "total": 1,
            "items": [
                {
                    "change": "changed",
                    "relation_id": "rel-1",
                    "relation_type": "number_to_artifact",
                    "source_region_id": "number-1",
                    "target_region_id": "artifact-1",
                    "before_method": "nearest",
                    "after_method": "caption_ocr_constrained_assignment",
                    "before_score": 0.61,
                    "after_score": 0.94,
                    "protected": False,
                }
            ],
        }
    )

    assert result.items[0].change == "changed"
    assert result.items[0].after_score == 0.94


def test_clean_record_removes_only_previous_matching_state() -> None:
    record = {
        "_id": "record-1",
        "job_id": "job-1",
        "fields": {
            "category": {
                "value": "陶器",
                "evidence": [
                    {
                        "page": 1,
                        "quote": "陶器",
                        "region_id": "text-1",
                        "linked_region_ids": ["artifact-old"],
                        "relation_ids": ["rel-old"],
                    }
                ],
            }
        },
        "region_ids": ["artifact-old"],
        "relation_ids": ["rel-old"],
        "fusion_status": "linked",
    }

    cleaned = RematchService._clean_record(record)

    assert cleaned["id"] == "record-1"
    assert cleaned["fields"]["category"]["value"] == "陶器"
    assert cleaned["fields"]["category"]["evidence"][0]["region_id"] == "text-1"
    assert "linked_region_ids" not in cleaned["fields"]["category"]["evidence"][0]
    assert "relation_ids" not in cleaned
