from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

_RULE_SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "identifier": (
        ("uniqueid", "artifactid", "identifier", "number", "编号", "器物号", "唯一"),
        ("artifact_id", "context_id"),
    ),
    "color": (
        ("color", "颜色", "色泽", "表面色", "nullvalue"),
        ("surface_color",),
    ),
    "figure": (
        ("figure", "caption", "plate", "图注", "图号", "图版", "序号"),
        ("figure_caption", "plate_no", "color_plate"),
    ),
    "measurements": (
        ("size", "dimension", "measurement", "尺寸", "口径", "底径", "高度", "precision"),
        ("measurements",),
    ),
    "classification": (
        ("material", "texture", "type", "category", "质地", "类别", "器型", "vessel", "alignment"),
        ("texture", "category", "material"),
    ),
}

_EMPTY_MARKERS = {"", "无", "none", "null", "nan", "无记录"}
_MEASUREMENT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:cm|mm|m|厘米|毫米|米|㎝|㎜)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RuleCheckResult:
    scope: str
    verdict: str
    reason: str


@dataclass(frozen=True, slots=True)
class VerificationSampleProfile:
    record_id: str
    expected_label: str
    primary_page: int
    confidence_bucket: str
    matching_method: str
    page_scope: str
    has_color_plate: bool
    rule_states: tuple[str, ...]
    rule_checks: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def document(self) -> dict[str, Any]:
        return asdict(self)

    def coverage_tags(self) -> tuple[str, ...]:
        return (
            f"expected:{self.expected_label}",
            f"confidence:{self.confidence_bucket}",
            f"method:{self.matching_method}",
            f"scope:{self.page_scope}",
            f"color:{'present' if self.has_color_plate else 'absent'}",
            *self.rule_states,
        )


@dataclass(frozen=True, slots=True)
class VerificationSampleSelection:
    record_ids: list[str]
    metadata: dict[str, dict[str, Any]]
    eligible_count: int
    correct_pool_size: int
    incorrect_pool_size: int


def select_balanced_verification_sample(
    *,
    records: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    sample_size: int,
    seed: int,
) -> VerificationSampleSelection:
    """Pick a balanced 9-correct / 9-incorrect cohort from generated cards.

    Correct/incorrect is decided from the generated card fields against the
    enabled page-3 rules. Gold labels are not used.
    """

    matched = [
        record
        for record in records
        if record.get("fusion_status") in {"linked", "partial"}
        or bool(record.get("region_ids"))
    ]
    candidates = matched or records
    enabled_rules = [rule for rule in rules if rule.get("enabled", True)]
    rule_scopes = _enabled_rule_scopes(enabled_rules)
    artifact_id_counts = _artifact_id_counts(candidates)

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
            artifact_id_counts=artifact_id_counts,
        )
        for record in candidates
        if record.get("_id") or record.get("id")
    ]

    correct = [profile for profile in profiles if profile.expected_label == "correct"]
    incorrect = [profile for profile in profiles if profile.expected_label == "incorrect"]
    rng = random.Random(seed)
    rng.shuffle(correct)
    rng.shuffle(incorrect)

    correct_target = max(sample_size, 0) // 2
    incorrect_target = max(sample_size, 0) - correct_target
    selected = [
        *correct[:correct_target],
        *incorrect[:incorrect_target],
    ]
    rng.shuffle(selected)
    return VerificationSampleSelection(
        record_ids=[profile.record_id for profile in selected],
        metadata={profile.record_id: profile.document() for profile in selected},
        eligible_count=len(profiles),
        correct_pool_size=len(correct),
        incorrect_pool_size=len(incorrect),
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
    """Backward-compatible wrapper around balanced 9/9 sampling."""

    selection = select_balanced_verification_sample(
        records=records,
        relations=relations,
        regions=regions,
        rules=rules,
        sample_size=sample_size,
        seed=seed,
    )
    return selection.record_ids, selection.metadata, selection.eligible_count


def evaluate_record_rules(
    *,
    record: dict[str, Any],
    rule_scopes: dict[str, tuple[str, ...]],
    artifact_id_counts: dict[str, int],
) -> list[RuleCheckResult]:
    fields = record.get("fields", {}) if isinstance(record.get("fields"), dict) else {}
    if not rule_scopes:
        return [_completeness_check(record, fields)]

    results: list[RuleCheckResult] = []
    for scope in sorted(rule_scopes):
        if scope == "identifier":
            results.append(_identifier_check(record, fields, artifact_id_counts))
        elif scope == "color":
            results.append(_color_check(fields))
        elif scope == "figure":
            results.append(_figure_check(fields))
        elif scope == "measurements":
            results.append(_measurements_check(fields))
        elif scope == "classification":
            results.append(_classification_check(fields))
    return results


def enabled_rule_payload(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        payload.append(
            {
                "id": rule.get("id"),
                "title": str(rule.get("title", "")).strip(),
                "description": str(rule.get("description", "")).strip(),
            }
        )
    return payload


def rule_scopes_from_rules(rules: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    return _enabled_rule_scopes([rule for rule in rules if rule.get("enabled", True)])


def _profile_record(
    *,
    record: dict[str, Any],
    relation_by_id: dict[str, dict[str, Any]],
    region_by_id: dict[str, dict[str, Any]],
    rule_scopes: dict[str, tuple[str, ...]],
    artifact_id_counts: dict[str, int],
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

    checks = evaluate_record_rules(
        record=record,
        rule_scopes=rule_scopes,
        artifact_id_counts=artifact_id_counts,
    )
    expected_label = (
        "correct" if checks and all(item.verdict == "passed" for item in checks) else "incorrect"
    )
    rule_states = tuple(
        f"rule:{item.scope}:{('covered' if item.verdict == 'passed' else 'issue')}"
        for item in checks
    ) or ("rule:general:covered",)
    return VerificationSampleProfile(
        record_id=record_id,
        expected_label=expected_label,
        primary_page=primary_page,
        confidence_bucket=confidence_bucket,
        matching_method=matching_method,
        page_scope=page_scope,
        has_color_plate=has_color_plate,
        rule_states=rule_states,
        rule_checks=tuple(asdict(item) for item in checks),
    )


def _identifier_check(
    record: dict[str, Any],
    fields: dict[str, Any],
    artifact_id_counts: dict[str, int],
) -> RuleCheckResult:
    artifact_id = _record_artifact_id(record, fields)
    if not artifact_id:
        return RuleCheckResult("identifier", "failed", "器物编号缺失")
    if artifact_id_counts.get(artifact_id, 0) > 1:
        return RuleCheckResult("identifier", "failed", f"器物编号 {artifact_id} 重复")
    return RuleCheckResult("identifier", "passed", "器物编号唯一且非空")


def _color_check(fields: dict[str, Any]) -> RuleCheckResult:
    field = fields.get("surface_color")
    value = _field_text(field)
    status = str(field.get("status", "")).lower() if isinstance(field, dict) else ""
    if status in {"invalid", "ambiguous"}:
        return RuleCheckResult("color", "failed", "颜色字段状态为无效或歧义")
    if _is_empty_marker(value):
        return RuleCheckResult("color", "passed", "源文无颜色记录时允许空值")
    return RuleCheckResult("color", "passed", "颜色字段可接受")


def _figure_check(fields: dict[str, Any]) -> RuleCheckResult:
    if _has_text(fields.get("figure_caption")):
        return RuleCheckResult("figure", "passed", "图注已填写")
    return RuleCheckResult("figure", "failed", "图注缺失")


def _measurements_check(fields: dict[str, Any]) -> RuleCheckResult:
    value = _field_text(fields.get("measurements"))
    if not value or _is_empty_marker(value):
        return RuleCheckResult("measurements", "failed", "尺寸缺失")
    if _MEASUREMENT_RE.search(value):
        return RuleCheckResult("measurements", "passed", "尺寸含数值或单位")
    return RuleCheckResult("measurements", "failed", "尺寸缺少可识别的数值或单位")


def _classification_check(fields: dict[str, Any]) -> RuleCheckResult:
    if any(_has_text(fields.get(key)) for key in ("texture", "category", "material")):
        return RuleCheckResult("classification", "passed", "质地或器型已填写")
    return RuleCheckResult("classification", "failed", "质地与器型均缺失")


def _completeness_check(record: dict[str, Any], fields: dict[str, Any]) -> RuleCheckResult:
    has_id = bool(_record_artifact_id(record, fields))
    has_caption = _has_text(fields.get("figure_caption"))
    has_size = bool(_MEASUREMENT_RE.search(_field_text(fields.get("measurements"))))
    if has_id and has_caption and has_size:
        return RuleCheckResult("completeness", "passed", "编号、图注和尺寸均已填写")
    return RuleCheckResult("completeness", "failed", "编号、图注或尺寸不完整")


def _enabled_rule_scopes(rules: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    scopes: dict[str, tuple[str, ...]] = {}
    for rule in rules:
        text = _normalize(f"{rule.get('title', '')} {rule.get('description', '')}")
        for scope, (keywords, fields) in _RULE_SCOPES.items():
            if any(_normalize(keyword) in text for keyword in keywords):
                scopes[scope] = fields
    return scopes


def _artifact_id_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        fields = record.get("fields", {}) if isinstance(record.get("fields"), dict) else {}
        artifact_id = _record_artifact_id(record, fields)
        if not artifact_id:
            continue
        counts[artifact_id] = counts.get(artifact_id, 0) + 1
    return counts


def _normalize_identifier(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("，", ",").replace("：", ",").replace(":", ",")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r",+", ",", text).strip(",")
    return text.upper()


def _record_artifact_id(record: dict[str, Any], fields: dict[str, Any] | None = None) -> str:
    identity = record.get("linkage", {}).get("identity", {})
    if not isinstance(identity, dict):
        identity = {}
    field_map = fields if isinstance(fields, dict) else record.get("fields", {})
    if not isinstance(field_map, dict):
        field_map = {}
    candidate = (
        identity.get("artifact_id_normalized")
        or identity.get("artifact_id_raw")
        or _field_text(field_map.get("artifact_id"))
    )
    return _normalize_identifier(candidate)


def _field_text(field: Any) -> str:
    if field is None:
        return ""
    if isinstance(field, dict):
        value = field.get("value", field.get("raw_value"))
        return "" if value is None else str(value).strip()
    return str(field).strip()


def _has_text(field: Any) -> bool:
    return not _is_empty_marker(_field_text(field))


def _is_empty_marker(value: str) -> bool:
    return _normalize(value) in _EMPTY_MARKERS or not value.strip()


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
