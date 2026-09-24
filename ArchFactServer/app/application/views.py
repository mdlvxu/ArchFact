from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.models.schemas import (
    AiVerificationRunView,
    ArtifactEntityView,
    ExtractionJobView,
    ExtractionRecordView,
    JobEventView,
    MachineVerificationRunView,
    RecordRevisionView,
    RegionRelationView,
    RelationRevisionView,
    RematchRunView,
    SourceRegionView,
    VerificationExperimentView,
    VerificationSessionView,
    VerificationVersionView,
)

if TYPE_CHECKING:
    from app.container import Container

def _coerce_record_timestamp(record: dict[str, Any], *keys: str) -> datetime:
    """Older restored/rematch payloads may omit created_at; keep list APIs readable."""

    for key in keys:
        value = record.get(key)
        if isinstance(value, datetime):
            return value
    return datetime.now(UTC)


def build_record_view(record: dict, *, compact: bool = False) -> ExtractionRecordView:
    linkage = record.get("linkage", {})
    compact_text_evidence = record.get("text_evidence", [])
    compact_region_ids = record.get("region_ids", [])
    if compact:
        visual_link = dict(linkage.get("visual_link", {}))
        visual_link["evidence"] = []
        visual_link["evidence_block_ids"] = []
        linkage = {
            "identity": linkage.get("identity", {}),
            "visual_link": visual_link,
        }
        compact_text_evidence = [
            {
                "page": item.get("page"),
                "quote": item.get("quote", ""),
                "bbox": item.get("bbox"),
                "region_id": item.get("region_id"),
                "kind": item.get("kind", "text"),
            }
            for item in record.get("text_evidence") or []
            if isinstance(item, dict) and item.get("page") is not None
        ]
    return ExtractionRecordView(
        id=record["_id"],
        job_id=record["job_id"],
        record_type=record.get("record_type", "artifact"),
        source_pages=record.get("source_pages", []),
        fields=record.get("fields", {}),
        text_evidence=compact_text_evidence if compact else record.get("text_evidence", []),
        linkage=linkage,
        link_hints=record.get("link_hints", {}),
        warnings=record.get("warnings", []),
        review_status=record.get("review_status", "unreviewed"),
        reviewed_at=record.get("reviewed_at"),
        model_run_ids=[] if compact else record.get("model_run_ids", []),
        region_ids=compact_region_ids if compact else record.get("region_ids", []),
        relation_ids=[] if compact else record.get("relation_ids", []),
        associated_pages=record.get("associated_pages", []),
        thumbnail_region_id=record.get("thumbnail_region_id"),
        primary_number_region_id=record.get("primary_number_region_id"),
        primary_artifact_region_id=record.get("primary_artifact_region_id"),
        primary_relation_id=record.get("primary_relation_id"),
        primary_link_score=record.get("primary_link_score"),
        fusion_status=record.get("fusion_status", "unlinked"),
        entity_id=record.get("entity_id"),
        entity_confidence=record.get("entity_confidence"),
        entity_match_status=record.get("entity_match_status", "unlinked"),
        created_at=_coerce_record_timestamp(record, "created_at", "updated_at"),
    )


def build_entity_view(entity: dict) -> ArtifactEntityView:
    return ArtifactEntityView(
        id=entity["_id"],
        job_id=entity["job_id"],
        document_id=entity["document_id"],
        canonical_artifact_id=entity.get("canonical_artifact_id"),
        aliases=entity.get("aliases", []),
        figure_refs=entity.get("figure_refs", []),
        plate_refs=entity.get("plate_refs", []),
        match_keys=entity.get("match_keys", []),
        record_ids=entity.get("record_ids", []),
        region_ids=entity.get("region_ids", []),
        relation_ids=entity.get("relation_ids", []),
        source_pages=entity.get("source_pages", []),
        associated_pages=entity.get("associated_pages", []),
        thumbnail_region_id=entity.get("thumbnail_region_id"),
        confidence=entity.get("confidence", 0),
        link_status=entity.get("link_status", "unlinked"),
        link_reasons=entity.get("link_reasons", []),
        version=entity.get("version", "1"),
    )


def build_region_view(region: dict) -> SourceRegionView:
    return SourceRegionView(
        id=region["_id"],
        job_id=region["job_id"],
        document_id=region["document_id"],
        page=region["page"],
        kind=region["kind"],
        bbox=region["bbox"],
        bbox_px=region.get("bbox_px"),
        text=region.get("text", ""),
        confidence=region.get("confidence"),
        source=region.get("source", "unknown"),
        model_run_id=region.get("model_run_id"),
        image_id=region.get("image_id"),
        crop_object_key=region.get("crop_object_key"),
        crop_width=region.get("crop_width"),
        crop_height=region.get("crop_height"),
        crop_content_type=region.get("crop_content_type"),
        crop_error=region.get("crop_error"),
        ocr_raw_text=region.get("ocr_raw_text"),
        ocr_confidence=region.get("ocr_confidence"),
        ocr_source=region.get("ocr_source"),
        ocr_model=region.get("ocr_model"),
        ocr_version=region.get("ocr_version"),
        ocr_model_run_id=region.get("ocr_model_run_id"),
        ocr_error=region.get("ocr_error"),
        approximate=bool(region.get("approximate", False)),
        geometry_type=region.get("geometry_type"),
        match_key=region.get("match_key"),
        match_reason=region.get("match_reason"),
    )


def build_relation_view(relation: dict) -> RegionRelationView:
    return RegionRelationView(
        id=relation["_id"],
        job_id=relation["job_id"],
        source_region_id=relation["source_region_id"],
        target_region_id=relation["target_region_id"],
        relation_type=relation.get("relation_type", "related_to"),
        score=relation.get("score"),
        method=relation.get("method", "unknown"),
        version=relation.get("version", "1"),
        model_run_id=relation.get("model_run_id"),
        review_status=relation.get("review_status", "unreviewed"),
        reviewed_at=relation.get("reviewed_at"),
        reviewer=relation.get("reviewer"),
        review_reason=relation.get("review_reason", ""),
        supersedes_relation_id=relation.get("supersedes_relation_id"),
        superseded_by_relation_id=relation.get("superseded_by_relation_id"),
    )


def build_relation_revision_view(revision: dict) -> RelationRevisionView:
    return RelationRevisionView(
        id=revision["_id"],
        job_id=revision["job_id"],
        relation_id=revision["relation_id"],
        action=revision["action"],
        before=revision.get("before"),
        after=revision.get("after"),
        reason=revision.get("reason", ""),
        reviewer=revision.get("reviewer"),
        created_at=revision["created_at"],
    )


def build_revision_view(revision: dict) -> RecordRevisionView:
    return RecordRevisionView(
        id=revision["_id"],
        job_id=revision["job_id"],
        record_id=revision["record_id"],
        field_key=revision["field_key"],
        decision=revision["decision"],
        before=revision.get("before"),
        after=revision.get("after"),
        reason=revision.get("reason", ""),
        reviewer=revision.get("reviewer"),
        created_at=revision["created_at"],
    )


def build_verification_session_view(session: dict) -> VerificationSessionView:
    return VerificationSessionView(
        id=session["_id"],
        job_id=session["job_id"],
        experiment_id=session.get("experiment_id", "legacy"),
        experiment_name=session.get("experiment_name", "历史校验"),
        cohort_id=session["cohort_id"],
        target_version=session["target_version"],
        status=session["status"],
        rules=session.get("rules", []),
        items=session.get("items", []),
        reviewed_count=session.get("reviewed_count", 0),
        sample_count=session.get("sample_count", len(session.get("items", []))),
        version_id=session.get("version_id"),
        ai_run_id=session.get("ai_run_id"),
        gold_dataset_id=session.get("gold_dataset_id"),
        matching_version_id=session.get("matching_version_id", "M0"),
        machine_run_id=session.get("machine_run_id"),
        assertion_baseline_id=session.get("assertion_baseline", {}).get("id", "v1"),
        assertion_baseline_name=session.get("assertion_baseline", {}).get("name", "LLM 断言 V1"),
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        completed_at=session.get("completed_at"),
    )


def build_verification_version_view(version: dict) -> VerificationVersionView:
    return VerificationVersionView(
        id=version["_id"],
        job_id=version["job_id"],
        experiment_id=version.get("experiment_id", "legacy"),
        experiment_name=version.get("experiment_name", "历史校验"),
        cohort_id=version["cohort_id"],
        version=version["version"],
        parent_version_id=version.get("parent_version_id"),
        matching_version_id=version.get("matching_version_id", "M0"),
        rules=version.get("rules", []),
        items=version.get("items", []),
        report=version["report"],
        ai_run_id=version.get("ai_run_id"),
        gold_dataset_id=version.get("gold_dataset_id"),
        gold_dataset_version=version.get("gold_dataset_version"),
        calibration_profile=version.get("calibration_profile"),
        assertion_baseline_id=version.get("assertion_baseline", {}).get("id", "v1"),
        assertion_baseline_name=version.get("assertion_baseline", {}).get("name", "LLM 断言 V1"),
        created_at=version["created_at"],
    )


def build_ai_verification_run_view(run: dict) -> AiVerificationRunView:
    return AiVerificationRunView(
        id=run["_id"],
        job_id=run["job_id"],
        session_id=run["session_id"],
        status=run["status"],
        progress=run.get("progress", {}),
        gold_dataset_id=run.get("gold_dataset_id"),
        benchmark_available=run.get("benchmark_available", False),
        conflict_count=run.get("conflict_count", 0),
        uncertain_count=run.get("uncertain_count", 0),
        version_id=run.get("version_id"),
        error=run.get("error"),
        created_at=run["created_at"],
        updated_at=run["updated_at"],
        completed_at=run.get("completed_at"),
    )


def build_machine_verification_run_view(run: dict) -> MachineVerificationRunView:
    return MachineVerificationRunView(
        id=run["_id"],
        job_id=run["job_id"],
        experiment_id=run.get("experiment_id", "legacy"),
        experiment_name=run.get("experiment_name", "历史校验"),
        mode=run.get("mode", "initial"),
        status=run["status"],
        progress=run.get("progress", {}),
        rules=run.get("rules", []),
        assertion_baseline_id=run.get("assertion_baseline", {}).get("id", "v1"),
        assertion_baseline_name=run.get("assertion_baseline", {}).get("name", "LLM 断言 V1"),
        sample_size=run.get("sample_size", 18),
        total_artifacts=run.get("total_artifacts", 0),
        pass_count=run.get("pass_count", 0),
        fail_count=run.get("fail_count", 0),
        uncertain_count=run.get("uncertain_count", 0),
        model_unavailable_count=run.get("model_unavailable_count", 0),
        model_unavailable_reason=run.get("model_unavailable_reason"),
        session_id=run.get("session_id"),
        version_id=run.get("version_id"),
        error=run.get("error"),
        created_at=run["created_at"],
        updated_at=run["updated_at"],
        completed_at=run.get("completed_at"),
    )


def build_verification_experiment_view(experiment: dict) -> VerificationExperimentView:
    return VerificationExperimentView(
        id=experiment["_id"],
        job_id=experiment["job_id"],
        name=experiment.get("name", "历史校验"),
        sequence=experiment.get("sequence", 0),
        status=experiment.get("status", "legacy"),
        matching_version_id=experiment.get("matching_version_id", "M0"),
        artifact_count=experiment.get("artifact_count", 0),
        created_at=experiment["created_at"],
    )


_TERMINAL_JOB_STATUSES = {
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
}


async def build_job_view(container: Container, job: dict) -> ExtractionJobView:
    events = await container.repository.list_events(
        job["_id"],
        container.settings.job_event_limit,
    )
    completed_at = job.get("completed_at")
    # Legacy jobs finished before completed_at existed: prefer the last extraction
    # event over updated_at (which rematch/apply can refresh days later).
    if completed_at is None and job.get("status") in _TERMINAL_JOB_STATUSES:
        completed_at = events[-1]["created_at"] if events else job.get("updated_at")
    return ExtractionJobView(
        id=job["_id"],
        document_id=job["document_id"],
        pipeline_id=job.get("pipeline_id", "default"),
        status=job["status"],
        stage=job["stage"],
        progress=job["progress"],
        cancel_requested=job.get("cancel_requested", False),
        error=job.get("error"),
        page_issues=job.get("page_issues", []),
        succeeded_pages=job.get("succeeded_pages", 0),
        failed_pages=job.get("failed_pages", 0),
        requested_pages=job.get("requested_pages", job.get("pages") or []),
        discovered_pages=job.get("discovered_pages", []),
        effective_pages=job.get("effective_pages", job.get("pages") or []),
        active_matching_version_id=job.get("active_matching_version_id", "M0"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        attempt_started_at=job.get("attempt_started_at"),
        completed_at=completed_at,
        retry_pages=job.get("retry_pages", []),
        events=[
            JobEventView(
                id=event["_id"],
                level=event["level"],
                message=event["message"],
                created_at=event["created_at"],
            )
            for event in events
        ],
    )


def build_rematch_view(run: dict) -> RematchRunView:
    return RematchRunView(
        id=run["_id"],
        job_id=run["job_id"],
        base_matching_version_id=run.get("base_matching_version_id", "M0"),
        status=run["status"],
        preserve_reviewed=run.get("preserve_reviewed", True),
        apply_immediately=run.get("apply_immediately", False),
        cancel_requested=run.get("cancel_requested", False),
        progress=run.get("progress", {}),
        report=run.get("report"),
        error=run.get("error"),
        created_at=run["created_at"],
        updated_at=run["updated_at"],
        completed_at=run.get("completed_at"),
        applied_at=run.get("applied_at"),
    )
