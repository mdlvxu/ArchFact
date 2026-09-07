from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.result_fusion import ResultFusionService

if TYPE_CHECKING:
    from app.container import Container

_CARD_ENRICHMENT_FIELD_KEYS = (
    "category",
    "texture",
    "surface_color",
    "morphological_description",
    "measurements",
    "figure_caption",
)


def _field_snapshot(fields: dict, key: str) -> tuple[str, str]:
    field = fields.get(key) if isinstance(fields, dict) else None
    if not isinstance(field, dict):
        return ("", "")
    return (
        str(field.get("value") or "").strip(),
        str(field.get("raw_value") or "").strip(),
    )


def _record_card_fields_changed(before: dict, after: dict) -> bool:
    before_fields = before.get("fields", {}) if isinstance(before.get("fields"), dict) else {}
    after_fields = after.get("fields", {}) if isinstance(after.get("fields"), dict) else {}
    for key in _CARD_ENRICHMENT_FIELD_KEYS:
        if _field_snapshot(before_fields, key) != _field_snapshot(after_fields, key):
            return True
    before_quotes = [
        str(item.get("quote") or "")
        for item in before.get("text_evidence", [])
        if isinstance(item, dict)
    ]
    after_quotes = [
        str(item.get("quote") or "")
        for item in after.get("text_evidence", [])
        if isinstance(item, dict)
    ]
    return before_quotes != after_quotes


async def enrich_records_with_paragraph_fields(
    container: Container,
    job_id: str,
    records: list[dict],
    *,
    persist: bool = True,
) -> int:
    """Fill/upgrade sparse card fields from OCR paragraphs.

    Older jobs often store a truncated morphological_description (e.g. ``片状``)
    while the full prose still exists in page OCR. Re-run deterministic paragraph
    completion on read, and persist upgrades once per fusion version so export /
    rematch baselines see the same card data as the UI.
    """

    if not records:
        return 0
    source_pages = sorted(
        {
            page
            for record in records
            for page in record.get("source_pages", [])
            if isinstance(page, int)
        }
    )
    if not source_pages:
        return 0

    persisted_regions: list[dict] = []
    for page_no in source_pages:
        persisted_regions.extend(
            await container.repository.list_page_regions(job_id, page_no)
        )
    if not persisted_regions:
        return 0

    regions = [
        {
            **region,
            "id": str(region.get("id") or region["_id"]),
        }
        for region in persisted_regions
    ]
    region_by_id = {str(region["id"]): region for region in regions}
    before_by_id = {
        str(record["_id"]): {
            "fields": {
                key: dict(field)
                for key, field in (record.get("fields") or {}).items()
                if isinstance(field, dict)
            },
            "text_evidence": list(record.get("text_evidence") or []),
        }
        for record in records
        if record.get("_id")
    }

    ResultFusionService.complete_record_text_evidence(
        records=records,
        regions=regions,
        region_by_id=region_by_id,
    )
    ResultFusionService.complete_multiline_figure_caption_evidence(
        records=records,
        regions=regions,
        region_by_id=region_by_id,
    )
    ResultFusionService.prune_cross_artifact_field_evidence(
        records=records,
        region_by_id=region_by_id,
    )

    if not persist:
        return 0

    persisted = 0
    enrichment_version = str(ResultFusionService.version)
    for record in records:
        record_id = str(record.get("_id") or "")
        if not record_id:
            continue
        before = before_by_id.get(record_id)
        if before is None:
            continue
        changed = _record_card_fields_changed(before, record)
        already_enriched = record.get("paragraph_enrichment_version") == enrichment_version
        if not changed and already_enriched:
            continue
        if not changed:
            continue

        region_ids: list[str] = []
        seen_region_ids: set[str] = set()
        for region_id in record.get("region_ids", []) or []:
            value = str(region_id)
            if value and value not in seen_region_ids:
                seen_region_ids.add(value)
                region_ids.append(value)
        await container.repository.patch_record_paragraph_enrichment(
            job_id=job_id,
            record_id=record_id,
            fields=record.get("fields") or {},
            text_evidence=list(record.get("text_evidence") or []),
            region_ids=region_ids,
            enrichment_version=enrichment_version,
        )
        record["paragraph_enrichment_version"] = enrichment_version
        persisted += 1
    return persisted
