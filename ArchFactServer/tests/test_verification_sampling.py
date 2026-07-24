from app.services.verification_sampling import select_stratified_verification_sample


def _record(
    record_id: str,
    page: int,
    relation_id: str,
    region_ids: list[str],
    *,
    associated_pages: list[int] | None = None,
    field_status: str = "present",
) -> dict:
    return {
        "_id": record_id,
        "record_type": "artifact",
        "source_pages": [page],
        "associated_pages": associated_pages or [page],
        "relation_ids": [relation_id],
        "region_ids": region_ids,
        "fusion_status": "linked",
        "fields": {
            "figure_caption": {"value": f"图{page}", "status": field_status},
            "artifact_id": {"value": record_id, "status": "present"},
        },
    }


def test_stratified_sample_is_deterministic_and_keeps_metadata() -> None:
    records = [
        _record("r-high", 1, "rel-high", ["artifact-high", "plate-high"]),
        _record("r-medium", 2, "rel-medium", ["artifact-medium"], associated_pages=[2, 8]),
        _record("r-low", 3, "rel-low", ["artifact-low"], field_status="missing"),
    ]
    relations = [
        {"_id": "rel-high", "score": 0.93, "method": "caption_ocr_constrained_assignment"},
        {"_id": "rel-medium", "score": 0.72, "method": "caption_scope_fallback"},
        {"_id": "rel-low", "score": 0.42, "method": "nearest_visual_fusion"},
    ]
    regions = [
        {"_id": "artifact-high", "kind": "artifact"},
        {"_id": "plate-high", "kind": "color_plate"},
        {"_id": "artifact-medium", "kind": "artifact"},
        {"_id": "artifact-low", "kind": "artifact"},
    ]
    rules = [{"title": "Figure Caption Check", "description": "图注必须匹配", "enabled": True}]

    first = select_stratified_verification_sample(
        records=records,
        relations=relations,
        regions=regions,
        rules=rules,
        sample_size=3,
        seed=20260719,
    )
    second = select_stratified_verification_sample(
        records=records,
        relations=relations,
        regions=regions,
        rules=rules,
        sample_size=3,
        seed=20260719,
    )

    assert first == second
    selected, metadata, eligible_count = first
    assert set(selected) == {"r-high", "r-medium", "r-low"}
    assert eligible_count == 3
    assert {item["confidence_bucket"] for item in metadata.values()} == {"high", "medium", "low"}
    assert metadata["r-medium"]["page_scope"] == "cross_page"
    assert metadata["r-high"]["has_color_plate"] is True
    assert "rule:figure:issue" in metadata["r-low"]["rule_states"]


def test_rule_scopes_filter_initial_candidate_population() -> None:
    records = [
        _record("r-figure", 1, "rel-figure", ["artifact-figure"]),
        {
            "_id": "r-text-only",
            "source_pages": [2],
            "associated_pages": [2],
            "relation_ids": ["rel-text"],
            "region_ids": ["artifact-text"],
            "fusion_status": "linked",
            "fields": {"morphological_description": {"value": "罐", "status": "present"}},
        },
    ]
    selected, _, eligible_count = select_stratified_verification_sample(
        records=records,
        relations=[],
        regions=[],
        rules=[{"title": "Figure Caption Check", "enabled": True}],
        sample_size=18,
        seed=7,
    )

    assert selected == ["r-figure"]
    assert eligible_count == 1
