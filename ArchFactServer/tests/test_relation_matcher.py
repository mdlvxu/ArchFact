from app.core.config import Settings
from app.services.relation_matcher import RelationMatcher, RelationMatcherConfig


def test_global_assignment_avoids_greedy_duplicate_choice() -> None:
    matches = RelationMatcher._maximum_assignment(
        [[0.9, 0.8], [0.85, 0.1]],
        min_score=0.2,
    )

    assert {(row, column) for row, column, _ in matches} == {(0, 1), (1, 0)}


def test_matcher_creates_number_to_artifact_relations() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {
            "id": "artifact-1",
            "page": 3,
            "kind": "artifact",
            "bbox": [0.1, 0.1, 0.35, 0.4],
            "confidence": 0.95,
        },
        {
            "id": "number-1",
            "page": 3,
            "kind": "number",
            "bbox": [0.17, 0.41, 0.28, 0.46],
            "confidence": 0.9,
        },
    ]

    relations = matcher.match_page(job_id="job-1", page_no=3, regions=regions)

    assert len(relations) == 1
    assert relations[0]["source_region_id"] == "number-1"
    assert relations[0]["target_region_id"] == "artifact-1"
    assert relations[0]["relation_type"] == "number_of"
    assert relations[0]["method"] == "directional_assignment"


def test_matcher_restricts_number_assignment_to_detected_group() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {"id": "group-left", "page": 1, "kind": "group", "bbox": [0, 0, 0.5, 1]},
        {"id": "group-right", "page": 1, "kind": "group", "bbox": [0.5, 0, 1, 1]},
        {
            "id": "artifact-left",
            "page": 1,
            "kind": "artifact",
            "bbox": [0.40, 0.1, 0.49, 0.3],
            "confidence": 0.9,
        },
        {
            "id": "artifact-right",
            "page": 1,
            "kind": "artifact",
            "bbox": [0.51, 0.1, 0.60, 0.3],
            "confidence": 0.9,
        },
        {
            "id": "number-left",
            "page": 1,
            "kind": "number",
            "bbox": [0.46, 0.31, 0.49, 0.35],
            "confidence": 0.9,
        },
        {
            "id": "number-right",
            "page": 1,
            "kind": "number",
            "bbox": [0.51, 0.31, 0.54, 0.35],
            "confidence": 0.9,
        },
    ]

    relations = matcher.match_page(job_id="job-1", page_no=1, regions=regions)
    number_relations = {
        relation["source_region_id"]: relation["target_region_id"]
        for relation in relations
        if relation["relation_type"] == "number_of"
    }

    assert number_relations == {
        "number-left": "artifact-left",
        "number-right": "artifact-right",
    }
    assert all(
        relation["method"] == "directional_assignment_grouped"
        for relation in relations
        if relation["relation_type"] == "number_of"
    )


def test_group_caption_remains_group_level_without_false_artifact_inheritance() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {"id": "group-1", "page": 2, "kind": "group", "bbox": [0.1, 0.1, 0.9, 0.9]},
        {"id": "artifact-1", "page": 2, "kind": "artifact", "bbox": [0.2, 0.2, 0.4, 0.5]},
        {"id": "artifact-2", "page": 2, "kind": "artifact", "bbox": [0.5, 0.2, 0.7, 0.5]},
        {"id": "caption-1", "page": 2, "kind": "caption", "bbox": [0.2, 0.7, 0.8, 0.8]},
    ]

    relations = matcher.match_page(job_id="job-1", page_no=2, regions=regions)
    assert not any(relation["relation_type"] == "caption_of" for relation in relations)
    assert any(relation["relation_type"] == "caption_of_group" for relation in relations)
    assert not any(relation["method"] == "group_inheritance" for relation in relations)
    assert sum(relation["relation_type"] == "contains" for relation in relations) == 3


def test_caption_scopes_number_and_constrains_artifact_assignment() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {"id": "caption-left", "page": 1, "kind": "caption", "bbox": [0.05, 0.8, 0.45, 0.9]},
        {"id": "caption-right", "page": 1, "kind": "caption", "bbox": [0.55, 0.8, 0.95, 0.9]},
        {"id": "artifact-left", "page": 1, "kind": "artifact", "bbox": [0.38, 0.2, 0.48, 0.55]},
        {"id": "artifact-right", "page": 1, "kind": "artifact", "bbox": [0.52, 0.2, 0.62, 0.55]},
        {"id": "number-left", "page": 1, "kind": "number", "bbox": [0.44, 0.54, 0.48, 0.58]},
        {"id": "number-right", "page": 1, "kind": "number", "bbox": [0.52, 0.54, 0.56, 0.58]},
    ]

    relations = matcher.match_page(job_id="job-1", page_no=1, regions=regions)
    caption_numbers = {
        relation["target_region_id"]: relation["source_region_id"]
        for relation in relations
        if relation["relation_type"] == "caption_to_number"
    }
    number_artifacts = {
        relation["source_region_id"]: relation["target_region_id"]
        for relation in relations
        if relation["relation_type"] == "number_of"
    }

    assert caption_numbers == {
        "number-left": "caption-left",
        "number-right": "caption-right",
    }
    assert number_artifacts == {
        "number-left": "artifact-left",
        "number-right": "artifact-right",
    }
    assert all(
        relation["method"] == "caption_constrained_assignment"
        for relation in relations
        if relation["relation_type"] == "number_of"
    )


def test_caption_ocr_content_overrides_a_closer_wrong_caption() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {
            "id": "caption-wrong",
            "page": 1,
            "kind": "caption",
            "bbox": [0.35, 0.8, 0.55, 0.9],
            "text": "图7 5—8为陶罐",
        },
        {
            "id": "caption-correct",
            "page": 1,
            "kind": "caption",
            "bbox": [0.45, 0.8, 0.65, 0.9],
            "text": "图6 1—4为陶鬲足",
        },
        {
            "id": "artifact-3",
            "page": 1,
            "kind": "artifact",
            "bbox": [0.46, 0.2, 0.64, 0.5],
        },
        {
            "id": "number-3",
            "page": 1,
            "kind": "number",
            "bbox": [0.43, 0.51, 0.47, 0.56],
            "text": "3",
        },
    ]

    relations = matcher.match_page(job_id="job-ocr", page_no=1, regions=regions)

    caption_relation = next(
        relation for relation in relations if relation["relation_type"] == "caption_to_number"
    )
    number_relation = next(
        relation for relation in relations if relation["relation_type"] == "number_of"
    )
    assert caption_relation["source_region_id"] == "caption-correct"
    assert caption_relation["target_region_id"] == "number-3"
    assert caption_relation["method"] == "caption_ocr_scope"
    assert number_relation["target_region_id"] == "artifact-3"
    assert number_relation["method"] == "caption_ocr_constrained_assignment"


def test_trusted_caption_ocr_conflict_does_not_create_a_false_chain() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {
            "id": "caption-1",
            "page": 1,
            "kind": "caption",
            "bbox": [0.2, 0.75, 0.8, 0.85],
            "text": "图6 1、陶罐",
        },
        {
            "id": "artifact-2",
            "page": 1,
            "kind": "artifact",
            "bbox": [0.4, 0.2, 0.6, 0.5],
        },
        {
            "id": "number-2",
            "page": 1,
            "kind": "number",
            "bbox": [0.47, 0.51, 0.53, 0.56],
            "text": "2",
        },
    ]

    relations = matcher.match_page(job_id="job-conflict", page_no=1, regions=regions)

    assert not any(relation["relation_type"] == "caption_to_number" for relation in relations)
    assert not any(relation["relation_type"] == "number_of" for relation in relations)


def test_number_above_artifact_is_not_matched() -> None:
    matcher = RelationMatcher(RelationMatcherConfig.from_settings(Settings(app_env="test")))
    regions = [
        {"id": "artifact-1", "page": 1, "kind": "artifact", "bbox": [0.2, 0.4, 0.4, 0.7]},
        {"id": "number-above", "page": 1, "kind": "number", "bbox": [0.25, 0.1, 0.32, 0.14]},
    ]

    relations = matcher.match_page(job_id="job-1", page_no=1, regions=regions)

    assert not any(relation["relation_type"] == "number_of" for relation in relations)
