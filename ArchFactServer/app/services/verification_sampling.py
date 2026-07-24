from __future__ import annotations

import random
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

_RULE_SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "identifier": (
        ("uniqueid", "artifactid", "identifier", "number", "编号", "器物号", "唯一"),
        ("artifact_id", "context_id"),
    ),
    "color": (
        ("color", "颜色", "色泽", "表面色"),
        ("surface_color",),
    ),
    "figure": (
        ("figure", "caption", "plate", "图注", "图号", "图版", "序号"),
        ("figure_caption", "plate_no", "color_plate"),
    ),
    "measurements": (
        ("size", "dimension", "measurement", "尺寸", "口径", "底径", "高度"),
        ("measurements",),
    ),
    "classification": (
        ("material", "texture", "type", "category", "质地", "类别", "器型"),
        ("texture", "category", "material"),
    ),
}


@dataclass(frozen=True, slots=True)
class VerificationSampleProfile:
    record_id: str
    primary_page: int
    confidence_bucket: str
    matching_method: str
    page_scope: str
    has_color_plate: bool
    rule_states: tuple[str, ...]

    def document(self) -> dict[str, Any]:
        return asdict(self)

    def coverage_tags(self) -> tuple[str, ...]:
        return (
            f"confidence:{self.confidence_bucket}",
            f"method:{self.matching_method}",
            f"scope:{self.page_scope}",
            f"color:{'present' if self.has_color_plate else 'absent'}",
            *self.rule_states,
        )


def select_stratified_verification_sample(
    *,
    records: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    sample_size: int,
    seed: int,
) -> tuple[list[str], dict[str, dict[str, Any]], int]:
    """Select a deterministic, coverage-oriented fixed verification cohort."""

    matched = [
        record
        for record in records
        if record.get("fusion_status") in {"linked", "partial"}
        or bool(record.get("region_ids"))
    ]
    candidates = matched or records
    rule_scopes = _enabled_rule_scopes(rules)
    scoped = [record for record in candidates if _record_matches_scopes(record, rule_scopes)]
    candidates = scoped or candidates

    relation_by_id = {
        str(relation.get("_id") or relation.get("id")): relation for relation in relations
    }
    region_by_id = {str(region.get("_id") or region.get("id")): region for region in regions}
    profiles = [
        _profile_record(
            record=record,
            relation_by_id=relation_by_id,
            region_by_id=region_by_id,
            rule_scopes=rule_scopes,
        )
        for record in candidates
        if record.get("_id") or record.get("id")
    ]

    rng = random.Random(seed)
    rng.shuffle(profiles)
    selected: list[VerificationSampleProfile] = []
    tag_counts: dict[str, int] = {}
    page_counts: dict[int, int] = {}
    target_count = min(max(sample_size, 0), len(profiles))

    while len(selected) < target_count:
        best_index = max(
            range(len(profiles)),
            key=lambda index: _coverage_score(
                profiles[index],
                tag_counts=tag_counts,
                page_counts=page_counts,
                stable_tiebreak=-index,
            ),
        )
        profile = profiles.pop(best_index)
        selected.append(profile)
        for tag in profile.coverage_tags():
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        page_counts[profile.primary_page] = page_counts.get(profile.primary_page, 0) + 1

    selected_ids = [profile.record_id for profile in selected]
    metadata = {profile.record_id: profile.document() for profile in selected}
    return selected_ids, metadata, len(candidates)


def _profile_record(
    *,
    record: dict[str, Any],
    relation_by_id: dict[str, dict[str, Any]],
    region_by_id: dict[str, dict[str, Any]],
    rule_scopes: dict[str, tuple[str, ...]],
) -> VerificationSampleProfile:
    record_id = str(record.get("_id") or record.get("id"))
    record_relations = [
        relation_by_id[relation_id]
        for relation_id in map(str, record.get("relation_ids", []))
        if relation_id in relation_by_id
    ]
    relation_scores = [
        float(relation["score"])
        for relation in record_relations
        if isinstance(relation.get("score"), (int, float))
    ]
    entity_confidence = record.get("entity_confidence")
    confidence = (
        float(entity_confidence)
        if isinstance(entity_confidence, (int, float))
        else min(relation_scores, default=0.0)
    )
    confidence_bucket = "high" if confidence >= 0.85 else "medium" if confidence >= 0.60 else "low"

    methods = {str(relation.get("method", "")) for relation in record_relations}
    if any("ocr" in method and "fallback" not in method for method in methods):
        matching_method = "ocr_exact"
    elif any(
        marker in method
        for method in methods
        for marker in ("fallback", "directional", "nearest", "spatial", "global")
    ):
        matching_method = "layout_fallback"
    else:
        matching_method = "other"

    source_pages = _positive_pages(record.get("source_pages", []))
    associated_pages = _positive_pages(record.get("associated_pages", []))
    all_pages = source_pages | associated_pages
    primary_page = min(source_pages or all_pages or {0})
    page_scope = "cross_page" if bool(associated_pages - source_pages) else "same_page"
    record_regions = [
        region_by_id[region_id]
        for region_id in map(str, record.get("region_ids", []))
        if region_id in region_by_id
    ]
    has_color_plate = any(region.get("kind") == "color_plate" for region in record_regions)

    fields = record.get("fields", {}) if isinstance(record.get("fields"), dict) else {}
    rule_states = tuple(
        f"rule:{scope}:{_field_state(fields, field_keys)}"
        for scope, field_keys in sorted(rule_scopes.items())
    ) or ("rule:general:covered",)
    return VerificationSampleProfile(
        record_id=record_id,
        primary_page=primary_page,
        confidence_bucket=confidence_bucket,
        matching_method=matching_method,
        page_scope=page_scope,
        has_color_plate=has_color_plate,
        rule_states=rule_states,
    )


def _coverage_score(
    profile: VerificationSampleProfile,
    *,
    tag_counts: dict[str, int],
    page_counts: dict[int, int],
    stable_tiebreak: int,
) -> tuple[float, int]:
    score = sum(1.0 / (1 + tag_counts.get(tag, 0)) for tag in profile.coverage_tags())
    score += 1.5 / (1 + page_counts.get(profile.primary_page, 0))
    return score, stable_tiebreak


def _enabled_rule_scopes(rules: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    scopes: dict[str, tuple[str, ...]] = {}
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        text = _normalize(f"{rule.get('title', '')} {rule.get('description', '')}")
        for scope, (keywords, fields) in _RULE_SCOPES.items():
            if any(_normalize(keyword) in text for keyword in keywords):
                scopes[scope] = fields
    return scopes


def _record_matches_scopes(
    record: dict[str, Any],
    scopes: dict[str, tuple[str, ...]],
) -> bool:
    if not scopes:
        return True
    fields = record.get("fields", {})
    if not isinstance(fields, dict):
        return False
    return any(
        any(field_key in fields for field_key in field_keys)
        for field_keys in scopes.values()
    )


def _field_state(fields: dict[str, Any], field_keys: tuple[str, ...]) -> str:
    matching = [fields[key] for key in field_keys if isinstance(fields.get(key), dict)]
    if not matching:
        return "unavailable"
    issue_statuses = {"missing", "ambiguous", "invalid", "needs_review"}
    return (
        "issue"
        if any(str(field.get("status", "")).lower() in issue_statuses for field in matching)
        else "covered"
    )


def _normalize(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if character.isalnum()
    )


def _positive_pages(values: Any) -> set[int]:
    pages: set[int] = set()
    if not isinstance(values, (list, tuple, set)):
        return pages
    for value in values:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages.add(page)
    return pages
