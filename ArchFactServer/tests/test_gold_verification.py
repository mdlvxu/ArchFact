from app.services.gold_dataset_service import (
    canonical_artifact_id,
    extract_color_plate_keys,
    normalize_identifier,
)
from app.services.verification_service import VerificationService, compact_text


def test_gold_identifiers_use_one_canonical_separator() -> None:
    assert canonical_artifact_id("T3", 4) == "T3,4"
    assert normalize_identifier(" T3：4 ") == "T3,4"


def test_color_plate_references_are_normalized() -> None:
    assert extract_color_plate_keys("图六；彩版一六，5") == ["彩版一六,5"]


def test_measurement_normalization_handles_equivalent_units() -> None:
    assert compact_text("口径 6.4 厘米") == compact_text("口径6.4 cm")


def test_human_and_ai_consensus_is_computed_after_ai_review() -> None:
    assert (
        VerificationService._consensus(
            human_verdict="passed",
            ai_verdict="passed",
            gold_match_status="matched",
        )
        == "agreed"
    )
    assert (
        VerificationService._consensus(
            human_verdict="passed",
            ai_verdict="failed",
            gold_match_status="matched",
        )
        == "conflict"
    )
    assert (
        VerificationService._consensus(
            human_verdict="passed",
            ai_verdict="uncertain",
            gold_match_status="unavailable",
        )
        == "benchmark_unavailable"
    )
