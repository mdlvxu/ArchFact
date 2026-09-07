from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.container import Container

_TEXT_PAGE_FIELD_WEIGHTS = {
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


def _has_field_value(field: object) -> bool:
    if not isinstance(field, dict):
        return False
    value = field.get("value", field.get("raw_value"))
    return value not in (None, "", [], {})


def _candidate_evidence_pages(records: list[dict]) -> set[int]:
    pages: set[int] = set()
    for record in records:
        for page in record.get("source_pages", []):
            if isinstance(page, int):
                pages.add(page)
        for field in record.get("fields", {}).values():
            if not isinstance(field, dict):
                continue
            for evidence in field.get("evidence", []):
                if isinstance(evidence, dict) and isinstance(evidence.get("page"), int):
                    pages.add(int(evidence["page"]))
        for evidence in record.get("text_evidence", []):
            if isinstance(evidence, dict) and isinstance(evidence.get("page"), int):
                pages.add(int(evidence["page"]))
    return pages


def _select_primary_text_evidence(
    records: list[dict],
    selected_record: dict,
    *,
    color_plate_pages: set[int] | None = None,
) -> tuple[dict, int]:
    """Pick the entity page with the richest descriptive text evidence.

    Color-plate pages may carry short captions, but the left preview column must
    always prefer a non-color page when one exists. Every artifact is expected to
    have non-color text evidence; color plates are optional third-column support.
    """

    color_plate_pages = color_plate_pages or set()
    candidates: list[tuple[int, int, bool, int, dict]] = []
    selected_id = str(selected_record["_id"])

    for record in records:
        page_scores: dict[int, int] = {}
        page_field_counts: dict[int, int] = {}
        for field_key, field in record.get("fields", {}).items():
            if not _has_field_value(field):
                continue
            weight = _TEXT_PAGE_FIELD_WEIGHTS.get(field_key, 2)
            evidence_pages = {
                int(evidence["page"])
                for evidence in field.get("evidence", [])
                if isinstance(evidence, dict)
                and isinstance(evidence.get("page"), int)
                and evidence.get("kind", "text") == "text"
            }
            for page in evidence_pages:
                page_scores[page] = page_scores.get(page, 0) + weight
                page_field_counts[page] = page_field_counts.get(page, 0) + 1

        for page in record.get("source_pages", []):
            if isinstance(page, int):
                page_scores.setdefault(page, 0)
                page_field_counts.setdefault(page, 0)

        for page, score in page_scores.items():
            candidates.append(
                (
                    score,
                    page_field_counts[page],
                    str(record["_id"]) == selected_id,
                    -page,
                    record,
                )
            )

    if not candidates:
        fallback_page = next(
            (
                int(page)
                for page in selected_record.get("source_pages", [])
                if isinstance(page, int)
            ),
            1,
        )
        return selected_record, fallback_page

    non_color_candidates = [
        item for item in candidates if (-item[3]) not in color_plate_pages
    ]
    ranked = non_color_candidates or candidates
    score, field_count, selected, negative_page, record = max(
        ranked,
        key=lambda item: item[:4],
    )
    del score, field_count, selected
    return record, -negative_page


async def _resolve_color_plate_pages(
    container: Container,
    *,
    job_id: str,
    document_id: str | None,
    candidate_pages: set[int],
) -> set[int]:
    """Identify color-plate pages among evidence candidates."""

    color_pages: set[int] = set()
    if document_id:
        for page in await container.repository.list_document_pages(document_id):
            page_no = page.get("page_no")
            if (
                isinstance(page_no, int)
                and page_no in candidate_pages
                and page.get("page_type") == "color_plate"
            ):
                color_pages.add(page_no)

    for page_no in sorted(candidate_pages):
        if page_no in color_pages:
            continue
        regions = await container.repository.list_page_regions(job_id, page_no)
        if any(region.get("kind") == "color_plate" for region in regions):
            color_pages.add(page_no)
    return color_pages


def _record_evidence_region_ids(record: dict) -> set[str]:
    region_ids: set[str] = set()
    for field in record.get("fields", {}).values():
        if not isinstance(field, dict):
            continue
        for evidence in field.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            if evidence.get("region_id"):
                region_ids.add(str(evidence["region_id"]))
    for evidence in record.get("text_evidence", []):
        if isinstance(evidence, dict) and evidence.get("region_id"):
            region_ids.add(str(evidence["region_id"]))
    return region_ids


def _record_core_visual_region_ids(record: dict) -> set[str]:
    return {
        str(region_id)
        for region_id in (
            record.get("primary_number_region_id"),
            record.get("primary_artifact_region_id"),
            record.get("thumbnail_region_id"),
        )
        if region_id
    }


def _record_explicit_relation_ids(record: dict) -> set[str]:
    relation_ids: set[str] = set()
    if record.get("primary_relation_id"):
        relation_ids.add(str(record["primary_relation_id"]))
    for field in record.get("fields", {}).values():
        if not isinstance(field, dict):
            continue
        for evidence in field.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            relation_ids.update(
                str(relation_id)
                for relation_id in evidence.get("relation_ids", [])
                if relation_id
            )
    return relation_ids


def _select_relevant_evidence_relations(
    relations: list[dict],
    *,
    evidence_region_ids: set[str],
    core_visual_region_ids: set[str],
    explicit_relation_ids: set[str],
) -> list[dict]:
    """Keep the local evidence graph without traversing the whole entity graph."""
    structural_relation_types = {
        "caption_to_number",
        "number_of",
        "caption_of",
        "drawing_of",
        "color_plate_of",
        "plate_reference_to_color",
        "image_of",
    }
    selected: list[dict] = []
    for relation in relations:
        relation_id = str(relation["_id"])
        source_id = str(relation["source_region_id"])
        target_id = str(relation["target_region_id"])
        relation_type = relation.get("relation_type")
        touches_core = (
            source_id in core_visual_region_ids
            or target_id in core_visual_region_ids
        )
        is_local_self_evidence = (
            relation_type == "evidence_for"
            and source_id == target_id
            and source_id in evidence_region_ids
        )
        if (
            relation_id in explicit_relation_ids
            or is_local_self_evidence
            or (
                touches_core
                and relation_type in structural_relation_types | {"evidence_for"}
            )
        ):
            selected.append(relation)
    return selected

