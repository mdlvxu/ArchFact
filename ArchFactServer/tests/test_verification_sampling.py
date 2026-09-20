from app.domain.verification_sampling import (
    evaluate_record_rules,
    select_balanced_verification_sample,
)


DEFAULT_RULES = [
    {
        "id": 1,
        "title": "ID Uniqueness",
        "description": "The unique ID of each artifact must not be duplicated.",
        "enabled": True,
    },
    {
        "id": 2,
        "title": "Color Null Value Logic",
        "description": "None is allowed when the source has no color record.",
        "enabled": True,
    },
    {
        "id": 3,
        "title": "Figure Caption Check",
        "description": "The figure caption number must match the figure order.",
        "enabled": True,
    },
    {
        "id": 4,
        "title": "Size Precision",
        "description": "Dimensions may include any measurement with an accepted unit.",
        "enabled": True,
    },
    {
        "id": 5,
        "title": "Material / Vessel Type Alignment",
        "description": "Missing material or vessel attributes are prohibited.",
        "enabled": True,
    },
]


def _record(
    record_id: str,
    page: int,
    *,
    artifact_id: str | None = None,
    caption: str | None = "图一",
    measurements: str | None = "口径 6.4 厘米",
    texture: str | None = "夹砂陶",
    color: str | None = None,
    fusion_status: str = "linked",
    region_ids: list[str] | None = None,
) -> dict:
    fields = {
        "artifact_id": {"value": artifact_id or record_id, "status": "present"},
    }
    if caption is not None:
        fields["figure_caption"] = {"value": caption, "status": "present"}
    if measurements is not None:
        fields["measurements"] = {"value": measurements, "status": "present"}
    if texture is not None:
        fields["texture"] = {"value": texture, "status": "present"}
    if color is not None:
        fields["surface_color"] = {"value": color, "status": "present"}
    return {
        "_id": record_id,
        "record_type": "artifact",
        "source_pages": [page],
        "associated_pages": [page],
        "relation_ids": [],
        "region_ids": region_ids or [f"artifact-{record_id}"],
        "fusion_status": fusion_status,
        "fields": fields,
    }


def test_balanced_sample_picks_nine_correct_and_nine_incorrect() -> None:
    records = [
        *[_record(f"ok-{index}", index) for index in range(1, 12)],
        *[
            _record(f"bad-{index}", 20 + index, caption=None, measurements=None, texture=None)
            for index in range(1, 12)
        ],
    ]

    first = select_balanced_verification_sample(
        records=records,
        relations=[],
        regions=[],
        rules=DEFAULT_RULES,
        sample_size=18,
        seed=20260831,
    )
    second = select_balanced_verification_sample(
        records=records,
        relations=[],
        regions=[],
        rules=DEFAULT_RULES,
        sample_size=18,
        seed=20260831,
    )

    assert first == second
    assert first.eligible_count == 22
    assert first.correct_pool_size == 11
    assert first.incorrect_pool_size == 11
    labels = [first.metadata[record_id]["expected_label"] for record_id in first.record_ids]
    assert labels.count("correct") == 9
    assert labels.count("incorrect") == 9


def test_duplicate_identifier_is_classified_incorrect() -> None:
    records = [
        _record("r-a", 1, artifact_id="T3,4"),
        _record("r-b", 2, artifact_id="T3：4"),
        _record("r-c", 3, artifact_id="H1,2"),
    ]
    scopes = {
        "identifier": ("artifact_id",),
        "figure": ("figure_caption",),
        "measurements": ("measurements",),
        "classification": ("texture",),
    }
    counts = {"T3,4": 2, "H1,2": 1}

    results_a = evaluate_record_rules(
        record=records[0],
        rule_scopes=scopes,
        artifact_id_counts=counts,
    )
    results_c = evaluate_record_rules(
        record=records[2],
        rule_scopes=scopes,
        artifact_id_counts=counts,
    )
    identifier_a = next(item for item in results_a if item.scope == "identifier")
    identifier_c = next(item for item in results_c if item.scope == "identifier")
    assert identifier_a.verdict == "failed"
    assert identifier_c.verdict == "passed"


def test_missing_pool_does_not_steal_from_the_other_side() -> None:
    records = [_record(f"ok-{index}", index) for index in range(1, 6)]
    selection = select_balanced_verification_sample(
        records=records,
        relations=[],
        regions=[],
        rules=DEFAULT_RULES,
        sample_size=18,
        seed=3,
    )

    assert selection.correct_pool_size == 5
    assert selection.incorrect_pool_size == 0
    assert selection.record_ids == [
        record_id for record_id in selection.record_ids if selection.metadata[record_id]["expected_label"] == "correct"
    ]
    assert len(selection.record_ids) == 5


def test_empty_color_is_not_treated_as_an_error() -> None:
    record = _record("r-color", 1, color="无")
    results = evaluate_record_rules(
        record=record,
        rule_scopes={"color": ("surface_color",)},
        artifact_id_counts={"R-COLOR": 1},
    )
    assert results[0].verdict == "passed"
