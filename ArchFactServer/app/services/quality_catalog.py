"""Select the same final artifact-card cohort shown in Data Preview.

The extraction pipeline keeps page-level records.  The preview catalog shows
one representative per entity and excludes cards without an artifact crop.
Quality evaluation must use that cohort, not every stored page record.
"""

from __future__ import annotations

import re
from typing import Any

BODY_FIELDS = (
    "morphological_description",
    "measurements",
    "texture",
    "surface_color",
    "completeness",
)
CAPTION_ONLY_FIELDS = {"artifact_id", "figure_caption", "category"}
FIELD_WEIGHTS = {
    "artifact_id": 3,
    "category": 4,
    "material": 6,
    "surface_color": 6,
    "texture": 6,
    "surface_treatment": 6,
    "measurements": 10,
    "morphological_description": 12,
    "figure_caption": 1,
}
TEXTURE_PATTERN = re.compile(
    r"((?:泥质|夹砂|夹炭|细泥|粗泥|硬陶)[\u4e00-\u9fff]{0,6}陶|[\u4e00-\u9fff]{0,4}(?:闪玉|玉|石|青铜|铜|铁))"
)


def field_text(record: dict[str, Any], key: str) -> str:
    field = (record.get("fields") or {}).get(key) or {}
    value = (
        (field.get("value") if field.get("value") is not None else field.get("raw_value"))
        if isinstance(field, dict)
        else field
    )
    return str(value).strip() if value is not None else ""


def _has_value(record: dict[str, Any], key: str) -> bool:
    value = field_text(record, key)
    return bool(value and value.lower() not in {"none", "null"})


def text_evidence_summary(record: dict[str, Any]) -> str:
    text = "".join(
        str(item.get("quote") or "").strip() for item in record.get("text_evidence") or []
    )
    artifact_id = field_text(record, "artifact_id") or field_text(record, "context_id")
    if artifact_id:
        text = re.sub(
            rf"^\s*{re.escape(artifact_id).replace(':', '[:：]')}\s*[，,、:：.。]?\s*", "", text
        )
    text = re.sub(
        r"[（(][^)）]*(?:图|彩版|图版|fig(?:ure)?|plate)[^)）]*[）)]\s*$", "", text, flags=re.I
    )
    text = re.sub(
        r"(?:最大径|直径|残高|通高|全高|口径|底径|腹径|高|长|宽|厚|径)\s*约?\s*\d+(?:[.,]\d+)?\s*(?:厘米|毫米|米|cm|mm|m)?",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"[、,，；;\s]{2,}", "，", text).strip("，,、:：;；。．. ")


def preview_field_value(record: dict[str, Any], key: str) -> str:
    """Resolve the catalog's OCR-backed text fields before comparison."""
    explicit = field_text(record, key)
    if key not in {"morphological_description", "category", "texture"}:
        return explicit
    summary = text_evidence_summary(record)
    if key == "category":
        for candidate_key in ("category", "type", "subtype"):
            candidate = field_text(record, candidate_key)
            if candidate:
                return candidate
        match = re.match(r"^([\u4e00-\u9fffA-Za-z0-9ⅣⅢⅡⅠ]{1,12})(?=[。．.；;，,、]|$)", summary)
        candidate = match.group(1) if match else ""
        return "" if TEXTURE_PATTERN.search(candidate) else candidate
    category = preview_field_value(record, "category")
    if category and summary.startswith(category):
        summary = summary[len(category) :].lstrip("。．.；;，,、 ")
    if key == "texture":
        for candidate_key in ("texture", "surface_color", "material", "page_text"):
            candidate = field_text(record, candidate_key)
            if candidate:
                return candidate
        match = TEXTURE_PATTERN.search(summary)
        return match.group(1) if match else ""
    texture = TEXTURE_PATTERN.search(summary)
    if texture:
        summary = summary.replace(texture.group(1), "", 1)
    summary = re.sub(r"[、,，；;\s]{2,}", "，", summary).strip("，,、:：;；。．. ")
    if not explicit or (
        summary
        and len(summary) >= max(12, len(explicit) + 8)
        and (explicit in summary or len(explicit) <= 8)
    ):
        return summary or explicit
    return explicit


def _representative_score(record: dict[str, Any]) -> int:
    source_pages = set(record.get("source_pages") or [])
    score = 0
    for key, field in (record.get("fields") or {}).items():
        if not _has_value(record, key):
            continue
        evidence = field.get("evidence") or [] if isinstance(field, dict) else []
        own_text = any(
            item.get("page") in source_pages and item.get("kind", "text") == "text"
            for item in evidence
        )
        score += FIELD_WEIGHTS.get(key, 2) + int(own_text)
    return score + (9 if len(text_evidence_summary(record)) >= 12 else 0)


def _caption_only_sparse(record: dict[str, Any]) -> bool:
    if any(_has_value(record, key) for key in BODY_FIELDS):
        return False
    populated = {key for key in (record.get("fields") or {}) if _has_value(record, key)}
    if not populated.issubset(CAPTION_ONLY_FIELDS):
        return False
    caption = field_text(record, "figure_caption")
    artifact_id = field_text(record, "artifact_id")
    return bool(caption or re.match(r"^[\u4e00-\u9fff]{1,2}(?=[A-Za-z])", artifact_id))


def _crop_bound(record: dict[str, Any]) -> bool:
    return bool(record.get("primary_artifact_region_id") or record.get("thumbnail_region_id"))


def select_preview_cards(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One representative per preview-visible entity, preserving catalog order."""
    crop_entities = {record.get("entity_id") for record in records if _crop_bound(record)}
    selected: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if _caption_only_sparse(record):
            continue
        if not _crop_bound(record) and not any(_has_value(record, key) for key in BODY_FIELDS):
            if (
                len(text_evidence_summary(record)) < 12
                and len(
                    set((record.get("source_pages") or []) + (record.get("associated_pages") or []))
                )
                <= 1
            ):
                continue
        key = (
            f"entity:{record['entity_id']}"
            if record.get("entity_id")
            else f"record:{record.get('_id', index)}"
        )
        if key not in selected or _representative_score(record) > _representative_score(
            selected[key]
        ):
            selected[key] = record
    return [
        record for record in selected.values()
        if _crop_bound(record) or (record.get("entity_id") and record["entity_id"] in crop_entities)
    ]
