import pytest
from pydantic import ValidationError

from app.main import app
from app.models.schemas import RegionRelationRebindRequest, RegionRelationReviewUpdate


def test_relation_review_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/extraction-jobs/{job_id}/records/{record_id}/evidence-context"]
    assert "patch" in paths["/api/v1/extraction-jobs/{job_id}/relations/{relation_id}/review"]
    assert "post" in paths["/api/v1/extraction-jobs/{job_id}/relations/{relation_id}/rebind"]
    assert "get" in paths["/api/v1/extraction-jobs/{job_id}/relations/{relation_id}/revisions"]


def test_relation_review_accepts_only_supported_states() -> None:
    assert RegionRelationReviewUpdate(status="accepted").status == "accepted"
    with pytest.raises(ValidationError):
        RegionRelationReviewUpdate(status="passed")


def test_relation_rebind_requires_two_distinct_regions() -> None:
    request = RegionRelationRebindRequest(
        source_region_id="region-number",
        target_region_id="region-artifact",
    )
    assert request.target_region_id == "region-artifact"

    with pytest.raises(ValidationError):
        RegionRelationRebindRequest(
            source_region_id="region-same",
            target_region_id="region-same",
        )
