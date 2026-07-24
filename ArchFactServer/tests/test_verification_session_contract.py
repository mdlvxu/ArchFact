import pytest
from pydantic import ValidationError

from app.main import app
from app.models.schemas import VerificationItemUpdate, VerificationSessionCreate


def test_verification_session_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]
    base = "/api/v1/extraction-jobs/{job_id}"

    assert "post" in paths[f"{base}/verification-sessions"]
    assert "get" in paths[f"{base}/verification-sessions/{{session_id}}"]
    assert "get" in paths[f"{base}/verification-sessions/{{session_id}}/records"]
    assert "patch" in paths[f"{base}/verification-sessions/{{session_id}}/records/{{record_id}}"]
    assert "post" in paths[f"{base}/verification-sessions/{{session_id}}/complete"]
    assert "get" in paths[f"{base}/ai-verification-runs/{{run_id}}"]
    assert "get" in paths[f"{base}/verification-versions"]
    assert "post" in paths["/api/v1/gold-datasets/import/wenjiashan"]


def test_verification_session_requires_an_enabled_rule() -> None:
    request = VerificationSessionCreate(
        rules=[{"id": 1, "title": "Identifier check", "enabled": True}]
    )
    assert request.sample_size == 18

    with pytest.raises(ValidationError):
        VerificationSessionCreate(rules=[{"id": 1, "title": "Disabled rule", "enabled": False}])


def test_verification_item_accepts_only_pass_or_fail() -> None:
    assert VerificationItemUpdate(verdict="passed").verdict == "passed"
    failed = VerificationItemUpdate(verdict="failed", failure_reason="OCR mismatch")
    assert failed.verdict == "failed"

    with pytest.raises(ValidationError):
        VerificationItemUpdate(verdict="unreviewed")
