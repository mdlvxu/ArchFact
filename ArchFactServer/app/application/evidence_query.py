from __future__ import annotations

from app.application.evidence import (
    _candidate_evidence_pages as candidate_evidence_pages,
    _record_core_visual_region_ids as record_core_visual_region_ids,
    _record_evidence_region_ids as record_evidence_region_ids,
    _record_explicit_relation_ids as record_explicit_relation_ids,
    _resolve_color_plate_pages as resolve_color_plate_pages,
    _select_primary_text_evidence as select_primary_text_evidence,
    _select_relevant_evidence_relations as select_relevant_evidence_relations,
)
from app.application.record_enrichment import enrich_records_with_paragraph_fields
from app.application.views import build_entity_view, build_record_view, build_region_view, build_relation_view
from app.container import Container
from app.models.schemas import RecordEvidenceContextView


async def load_record_evidence_context(
    container: Container,
    job_id: str,
    record_id: str,
) -> RecordEvidenceContextView:
    job = await container.repository.get_job(job_id)
    record = await container.repository.get_record(job_id, record_id)
    entity = None
    entity_records = [record]
    if record.get("entity_id"):
        entity = await container.repository.get_entity(job_id, record["entity_id"])
        if entity is not None:
            siblings = await container.repository.list_records_by_ids(
                job_id,
                entity.get("record_ids", []),
            )
            sibling_by_id = {str(sibling["_id"]): sibling for sibling in siblings}
            sibling_by_id[str(record["_id"])] = record
            entity_records = list(sibling_by_id.values())

    candidate_pages = candidate_evidence_pages(entity_records)
    if entity is not None:
        for page in entity.get("associated_pages", []):
            if isinstance(page, int):
                candidate_pages.add(page)

    text_record, primary_text_page = select_primary_text_evidence(
        entity_records,
        record,
        color_plate_pages=await resolve_color_plate_pages(
            container,
            job_id=job_id,
            document_id=str(job.get("document_id") or "") or None,
            candidate_pages=candidate_pages,
        ),
    )

    context_records = list(
        {str(item["_id"]): item for item in (record, text_record)}.values()
    )
    await enrich_records_with_paragraph_fields(
        container,
        job_id,
        context_records,
        persist=True,
    )
    refreshed = {str(item["_id"]): item for item in context_records}
    if str(record.get("_id")) in refreshed:
        record = refreshed[str(record["_id"])]
    if str(text_record.get("_id")) in refreshed:
        text_record = refreshed[str(text_record["_id"])]
    context_records = list(refreshed.values())
    evidence_region_ids = set().union(
        *(record_evidence_region_ids(candidate) for candidate in context_records)
    )
    core_visual_region_ids = set().union(
        *(record_core_visual_region_ids(candidate) for candidate in context_records)
    )
    explicit_relation_ids = set().union(
        *(record_explicit_relation_ids(candidate) for candidate in context_records)
    )
    candidate_relation_ids = set(record.get("relation_ids", []))
    candidate_relation_ids.update(text_record.get("relation_ids", []))
    if entity is not None:
        candidate_relation_ids.update(entity.get("relation_ids", []))

    candidate_relations = await container.repository.list_relations_by_ids(
        job_id,
        sorted(candidate_relation_ids),
    )
    relations = select_relevant_evidence_relations(
        candidate_relations,
        evidence_region_ids=evidence_region_ids,
        core_visual_region_ids=core_visual_region_ids,
        explicit_relation_ids=explicit_relation_ids,
    )

    region_ids = evidence_region_ids | core_visual_region_ids
    for candidate in (record, text_record):
        for field in candidate.get("fields", {}).values():
            for evidence in field.get("evidence", []):
                if evidence.get("region_id"):
                    region_ids.add(evidence["region_id"])
    for relation in relations:
        region_ids.add(relation["source_region_id"])
        region_ids.add(relation["target_region_id"])
    regions = await container.repository.list_regions_by_ids(job_id, sorted(region_ids))
    page_numbers = sorted(
        set(record.get("source_pages", []))
        | set(text_record.get("source_pages", []))
        | {int(region["page"]) for region in regions}
    )
    relevant_region_ids = sorted(region_ids)
    relevant_relation_ids = [str(relation["_id"]) for relation in relations]
    context_record = {
        **record,
        "region_ids": relevant_region_ids,
        "relation_ids": relevant_relation_ids,
        "associated_pages": page_numbers,
    }
    context_text_record = {
        **text_record,
        "region_ids": relevant_region_ids,
        "relation_ids": relevant_relation_ids,
        "associated_pages": page_numbers,
    }
    context_entity = (
        {
            **entity,
            "region_ids": relevant_region_ids,
            "relation_ids": relevant_relation_ids,
            "associated_pages": page_numbers,
        }
        if entity
        else None
    )
    return RecordEvidenceContextView(
        record=build_record_view(context_record),
        text_record=build_record_view(context_text_record),
        primary_text_page=primary_text_page,
        entity=build_entity_view(context_entity) if context_entity else None,
        page_numbers=page_numbers,
        regions=[build_region_view(region) for region in regions],
        relations=[build_relation_view(relation) for relation in relations],
    )
