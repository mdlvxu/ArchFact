import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import ConflictError
from app.repositories.verification import VerificationPersistence
from app.services.verification_service import VerificationService


def test_calibrated_report_uses_human_labels_as_reference() -> None:
    report = VerificationPersistence._build_calibrated_report(
        items=[
            {"verdict": "failed", "machine_verdict": "failed"},  # TP
            {"verdict": "passed", "machine_verdict": "passed"},  # TN
            {"verdict": "passed", "machine_verdict": "failed"},  # FP
            {"verdict": "failed", "machine_verdict": "passed"},  # FN
        ],
        total_artifacts=1200,
        machine_run={"pass_count": 900, "fail_count": 250, "uncertain_count": 50},
    )

    assert report["full_pass_count"] == 900
    assert report["full_fail_count"] == 250
    assert report["full_uncertain_count"] == 50
    assert report["true_positive_count"] == 1
    assert report["true_negative_count"] == 1
    assert report["false_positive_count"] == 1
    assert report["false_negative_count"] == 1
    assert report["error_coverage"] == 0.5
    assert report["error_precision"] == 0.5
    assert report["human_machine_alignment"] == 0.5
    assert report["review_load"] == 0.5


def test_model_service_outage_is_kept_out_of_failure_counts() -> None:
    unavailable = VerificationService._unavailable_result("HTTP 402 Insufficient Balance")
    report = VerificationPersistence._build_calibrated_report(
        items=[],
        total_artifacts=8,
        machine_run={
            "pass_count": 0,
            "fail_count": 0,
            "uncertain_count": 8,
            "model_unavailable_count": 8,
            "model_unavailable_reason": "HTTP 402 Insufficient Balance",
        },
    )

    assert unavailable["ai_verdict"] == "uncertain"
    assert unavailable["model_unavailable"] is True
    assert report["full_fail_count"] == 0
    assert report["full_uncertain_count"] == 8
    assert report["model_unavailable_count"] == 8


def test_machine_verification_uses_only_crop_bound_entity_cards() -> None:
    records = [
        # Same entity: the rich OCR record has no local crop, but its sibling does.
        {
            "_id": "record-rich",
            "entity_id": "entity-1",
            "fields": {"morphological_description": {"value": "器身残缺"}},
        },
        {
            "_id": "record-crop-sibling",
            "entity_id": "entity-1",
            "thumbnail_region_id": "crop-1",
            "fields": {"artifact_id": {"value": "M1:1"}},
        },
        # No crop anywhere in the entity: retained for provenance, excluded from validation.
        {
            "_id": "record-text-only",
            "entity_id": "entity-2",
            "fields": {"morphological_description": {"value": "仅有文本证据"}},
        },
        # An independent crop-bound card remains eligible.
        {
            "_id": "record-crop",
            "primary_artifact_region_id": "crop-2",
            "fields": {"artifact_id": {"value": "M1:2"}},
        },
    ]

    selected = VerificationService._records_for_machine_verification(records)

    assert [item["_id"] for item in selected] == ["record-rich", "record-crop"]


def test_assertion_baseline_is_snapshotted_with_selected_rules() -> None:
    baseline = VerificationService._assertion_baseline("v2")
    effective = VerificationService._effective_rules(
        baseline=baseline,
        selected_rules=[
            {
                "id": 99,
                "title": "Custom material rule",
                "description": "Material must be present.",
                "enabled": True,
            }
        ],
    )

    assert baseline["name"] == "LLM 断言 V2"
    assert len(baseline["rules"]) == 3
    assert [rule["source"] for rule in effective] == [
        "baseline",
        "baseline",
        "baseline",
        "selected",
    ]


def test_new_experiment_requires_completed_v1_on_current_experiment() -> None:
    async def go() -> None:
        persistence = VerificationPersistence.__new__(VerificationPersistence)
        persistence._db = MagicMock()
        persistence._db.verification_experiments.find_one = AsyncMock(
            return_value={"_id": "experiment-e1", "job_id": "job-1", "name": "实验 E1"}
        )
        persistence._db.verification_versions.find_one = AsyncMock(return_value=None)
        persistence.get_job = AsyncMock(
            return_value={"_id": "job-1", "active_verification_experiment_id": "experiment-e1"}
        )
        persistence.get_active_machine_verification_run = AsyncMock(return_value=None)
        persistence.get_active_verification_session = AsyncMock(return_value=None)

        with pytest.raises(ConflictError, match="尚未生成完成的 V1"):
            await persistence.create_verification_experiment("job-1")

        persistence._db.verification_experiments.update_many.assert_not_called()

    asyncio.run(go())


def test_terminating_machine_run_discards_partial_old_rule_results() -> None:
    async def go() -> None:
        repository = MagicMock()
        repository.terminate_machine_verification_run = AsyncMock()
        repository.clear_machine_verification_items = AsyncMock()
        repository.get_machine_verification_run = AsyncMock(
            return_value={"_id": "run-1", "job_id": "job-1", "status": "terminated"}
        )
        dispatcher = MagicMock()
        dispatcher.cancel = AsyncMock(return_value=True)
        settings = MagicMock()
        settings.verification_llm_max_concurrency = 1
        settings.verification_llm_timeout_seconds = 5.0
        service = VerificationService(
            settings=settings,
            repository=repository,
            dispatcher=dispatcher,
        )
        try:
            run = await service.terminate_machine_verification(job_id="job-1", run_id="run-1")
        finally:
            await service.close()

        assert run["status"] == "terminated"
        repository.terminate_machine_verification_run.assert_awaited_once_with(
            job_id="job-1", run_id="run-1"
        )
        dispatcher.cancel.assert_awaited_once_with("run-1")
        repository.clear_machine_verification_items.assert_awaited_once_with(
            job_id="job-1", run_id="run-1"
        )

    asyncio.run(go())


def test_full_field_distribution_counts_only_explicit_final_failure_causes() -> None:
    distribution = VerificationPersistence._field_error_distribution(
        [
            {
                "record_id": "r-1",
                "verdict": "failed",
                "field_results": [
                    {
                        "field": "artifact_crop",
                        "verdict": "failed",
                        "failure_code": "artifact_crop_missing",
                        "causes_overall_failure": True,
                    },
                    {
                        "field": "figure_caption",
                        "verdict": "failed",
                        "failure_code": "figure_caption_evidence_conflict",
                        "causes_overall_failure": False,
                    },
                ],
            },
            {
                "record_id": "r-2",
                "verdict": "passed",
                "field_results": [
                    {
                        "field": "artifact_crop",
                        "verdict": "failed",
                        "failure_code": "artifact_crop_missing",
                        "causes_overall_failure": True,
                    },
                ],
            },
            {"record_id": "r-3", "verdict": "failed", "field_results": []},
        ]
    )

    assert distribution == [
        {"key": "artifact_crop_missing", "count": 1},
        {"key": "unclassified_failure", "count": 1},
    ]


def test_review_required_count_is_separate_from_final_failures() -> None:
    count = VerificationPersistence._review_required_count(
        [
            {"verdict": "passed", "field_results": [{"verdict": "uncertain"}]},
            {"verdict": "passed", "field_results": [{"verdict": "failed"}]},
            {"verdict": "failed", "field_results": [{"verdict": "failed"}]},
        ]
    )

    assert count == 2


def test_semantic_failure_on_passed_card_is_not_a_final_failure_cause() -> None:
    result = VerificationService._semantic_result_payload(
        {"field": "figure_caption", "verdict": "failed", "reason": "OCR 证据不足"},
        overall_verdict="passed",
    )

    assert result["failure_code"] == "figure_caption_evidence_conflict"
    assert result["causes_overall_failure"] is False


def test_judge_record_sends_page_rules_not_gold_labels() -> None:
    async def go() -> None:
        settings = MagicMock()
        settings.verification_llm_max_concurrency = 1
        settings.verification_llm_timeout_seconds = 5.0
        service = VerificationService(
            settings=settings,
            repository=MagicMock(),
            dispatcher=MagicMock(),
        )
        service._call_llm = AsyncMock(
            return_value={
                "overall_verdict": "passed",
                "confidence": 0.91,
                "reason": "图注与尺寸均符合规则",
                "field_results": [],
            }
        )
        record = {
            "_id": "r-1",
            "fields": {
                "artifact_id": {"value": "T3,4"},
                "figure_caption": {"value": "图一"},
                "measurements": {"value": "口径 6.4 厘米"},
                "texture": {"value": "夹砂陶"},
            },
            "region_ids": ["artifact-1"],
            "relation_ids": ["rel-1"],
        }
        try:
            result = await service._judge_record(
                record=record,
                rules=[
                    {
                        "id": 3,
                        "title": "Figure Caption Check",
                        "description": "图注必须存在",
                        "enabled": True,
                    }
                ],
                expected_label="correct",
                artifact_id_counts={"T3,4": 1},
                visual_context={
                    "artifact_crop_present": True,
                    "color_plate_present": False,
                    "caption_present": True,
                    "number_present": True,
                    "relation_count": 1,
                    "region_kinds": ["artifact"],
                },
            )
        finally:
            await service.close()

        kwargs = service._call_llm.await_args.kwargs
        assert "gold_fields" not in kwargs
        assert "gold_standard" not in kwargs
        assert kwargs["rules"][0]["title"] == "Figure Caption Check"
        assert result["gold_match_status"] is None
        assert result["ai_verdict"] == "passed"

    asyncio.run(go())
