import asyncio
import copy
from typing import Any

from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.models.schemas import ExtractionConfig, RematchCreate
from app.repositories.mongo_repository import MongoRepository, utc_now
from app.services.artifact_entity_linker import ArtifactEntityLinker
from app.services.page_discovery import PageDiscoveryService
from app.services.relation_matcher import RelationMatcher
from app.services.result_fusion import ResultFusionService


class RematchService:
    """Rebuild visual relations from persisted OCR/YOLO data without model calls."""

    def __init__(
        self,
        *,
        repository: MongoRepository,
        relation_matcher: RelationMatcher,
        result_fusion: ResultFusionService,
        entity_linker: ArtifactEntityLinker,
        dispatcher: LocalJobDispatcher,
    ) -> None:
        self._repository = repository
        self._relation_matcher = relation_matcher
        self._result_fusion = result_fusion
        self._entity_linker = entity_linker
        self._dispatcher = dispatcher

    async def create(self, job_id: str, payload: RematchCreate) -> dict[str, Any]:
        run = await self._repository.create_rematch_run(
            job_id=job_id,
            preserve_reviewed=payload.preserve_reviewed,
            apply_immediately=payload.apply_immediately,
        )
        await self._dispatcher.dispatch(run["_id"])
        return run

    async def cancel(self, job_id: str, rematch_id: str) -> dict[str, Any]:
        run = await self._repository.request_rematch_cancel(job_id, rematch_id)
        if run.get("status") in {"completed", "applied", "failed", "cancelled"}:
            return run
        cancelled = await self._dispatcher.cancel(rematch_id)
        if cancelled:
            return await self._repository.update_rematch_run(
                rematch_id,
                status="cancelled",
                cancel_requested=True,
                completed_at=utc_now(),
                progress={"current": 0, "total": 0, "percent": 0, "stage": "cancelled"},
            )
        current = await self._repository.get_rematch_run(job_id, rematch_id)
        if current.get("status") in {"completed", "applied", "failed", "cancelled"}:
            return current
        # The local dispatcher loses its in-memory task table after a service
        # restart. If no task exists in this process, the persisted run is stale
        # and can be finalized safely instead of blocking every future rematch.
        return await self._repository.update_rematch_run(
            rematch_id,
            status="cancelled",
            cancel_requested=True,
            completed_at=utc_now(),
            progress={"current": 0, "total": 0, "percent": 0, "stage": "cancelled"},
        )

    async def apply(self, job_id: str, rematch_id: str) -> dict[str, Any]:
        return await self._repository.apply_rematch_snapshot(
            job_id=job_id,
            rematch_id=rematch_id,
        )

    async def run(self, rematch_id: str) -> None:
        active_model_runs: set[str] = set()
        try:
            run = await self._repository.get_rematch_run_by_id(rematch_id)
            job_id = str(run["job_id"])
            job = await self._repository.get_job(job_id)
            config = ExtractionConfig.model_validate(job["config"])
            regions_raw = await self._repository.list_job_regions(job_id)
            records_raw = await self._repository.list_job_records(job_id)
            relations_raw = await self._repository.list_job_relations(job_id)
            entities_raw = await self._repository.list_job_entities(job_id)
            page_index_raw = await self._load_page_index(str(job["document_id"]))
            regions = [self._domain_item(region) for region in regions_raw]
            records = [self._clean_record(record) for record in records_raw]
            page_metadata = {
                int(page["page_no"]): self._domain_item(page)
                for page in page_index_raw
                if isinstance(page.get("page_no"), int)
            }
            pages = sorted({int(region.get("page", 0)) for region in regions if region.get("page")})
            total_steps = max(len(pages) + 2, 1)
            await self._repository.update_rematch_run(
                rematch_id,
                status="running",
                progress={
                    "current": 0,
                    "total": total_steps,
                    "percent": 0,
                    "stage": "relation_matching",
                },
            )
            await self._ensure_not_cancelled(rematch_id)

            matching_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="rematch_relation_matching",
                provider=self._relation_matcher.provider,
                model=self._relation_matcher.model,
                version=self._relation_matcher.version,
                config=self._relation_matcher.config.as_dict(),
            )
            matching_run_id = str(matching_run["_id"])
            active_model_runs.add(matching_run_id)
            generated_by_id: dict[str, dict[str, Any]] = {}
            for page_index, page_no in enumerate(pages, start=1):
                await self._ensure_not_cancelled(rematch_id)
                for relation in self._relation_matcher.match_page(
                    job_id=job_id,
                    page_no=page_no,
                    regions=regions,
                ):
                    relation["model_run_id"] = matching_run_id
                    generated_by_id[str(relation["id"])] = relation
                await self._repository.update_rematch_run(
                    rematch_id,
                    progress={
                        "current": page_index,
                        "total": total_steps,
                        "percent": round(page_index / total_steps * 100),
                        "stage": "relation_matching",
                    },
                )
            await self._repository.finish_model_run(matching_run_id, status="completed")
            active_model_runs.discard(matching_run_id)

            protection = {
                "accepted_relation_ids": set(),
                "rejected_relation_keys": set(),
                "passed_record_ids": set(),
                "protected_record_ids": set(),
                "protected_relation_ids": set(),
                "relation_by_id": {},
            }
            if run.get("preserve_reviewed", True):
                protection = await self._repository.get_rematch_protection(job_id)
            protected_conflict_ids = set(protection["protected_relation_ids"]) - set(
                generated_by_id
            )
            relation_by_id = {
                relation_id: relation
                for relation_id, relation in generated_by_id.items()
                if self._repository._relation_key(relation)
                not in protection["rejected_relation_keys"]
            }
            for relation_id in protection["protected_relation_ids"]:
                protected = protection["relation_by_id"].get(relation_id)
                if protected is not None:
                    relation_by_id[str(relation_id)] = self._domain_item(protected)
            relations = list(relation_by_id.values())

            await self._ensure_not_cancelled(rematch_id)
            fusion_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="rematch_result_fusion",
                provider=self._result_fusion.provider,
                model=self._result_fusion.model,
                version=self._result_fusion.version,
                config={"schema_version": config.schema_version},
            )
            fusion_run_id = str(fusion_run["_id"])
            active_model_runs.add(fusion_run_id)
            fusion_output = self._result_fusion.fuse(
                job_id=job_id,
                records=records,
                regions=regions,
                relations=relations,
                config=config,
                model_run_id=fusion_run_id,
                page_metadata=page_metadata,
            )
            records = fusion_output.records
            relations = fusion_output.relations
            self._restore_passed_record_links(
                records=records,
                baseline_records=records_raw,
                passed_record_ids=protection["protected_record_ids"],
            )
            await self._repository.finish_model_run(fusion_run_id, status="completed")
            active_model_runs.discard(fusion_run_id)
            await self._repository.update_rematch_run(
                rematch_id,
                progress={
                    "current": len(pages) + 1,
                    "total": total_steps,
                    "percent": round((len(pages) + 1) / total_steps * 100),
                    "stage": "entity_linking",
                },
            )

            await self._ensure_not_cancelled(rematch_id)
            entity_output = self._entity_linker.link(
                job_id=job_id,
                document_id=str(job["document_id"]),
                records=records,
                regions=regions,
            )
            records = entity_output.records
            entities = entity_output.entities
            report = self._build_report(
                baseline_relations=relations_raw,
                candidate_relations=relations,
                candidate_records=records,
                regions=regions,
                protection=protection,
                conflict_relations=len(protected_conflict_ids),
            )
            await self._repository.save_rematch_snapshot(
                rematch_id=rematch_id,
                job_id=job_id,
                baseline_relations=relations_raw,
                baseline_records=records_raw,
                baseline_entities=entities_raw,
                candidate_relations=relations,
                candidate_records=records,
                candidate_entities=entities,
                baseline_inferred_regions=[
                    self._domain_item(region)
                    for region in regions_raw
                    if region.get("source") == "ocr_identifier_inference"
                    and region.get("kind") == "color_plate"
                ],
                candidate_inferred_regions=[
                    copy.deepcopy(region)
                    for region in regions
                    if region.get("source") == "ocr_identifier_inference"
                    and region.get("kind") == "color_plate"
                ],
                report=report,
            )
            if run.get("apply_immediately"):
                await self._repository.apply_rematch_snapshot(
                    job_id=job_id,
                    rematch_id=rematch_id,
                )
        except asyncio.CancelledError:
            for model_run_id in active_model_runs:
                await self._repository.finish_model_run(model_run_id, status="cancelled")
            try:
                await self._repository.update_rematch_run(
                    rematch_id,
                    status="cancelled",
                    cancel_requested=True,
                    completed_at=utc_now(),
                    progress={"current": 0, "total": 0, "percent": 0, "stage": "cancelled"},
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            for model_run_id in active_model_runs:
                await self._repository.finish_model_run(
                    model_run_id,
                    status="failed",
                    error=str(exc),
                )
            await self._repository.update_rematch_run(
                rematch_id,
                status="failed",
                error=str(exc),
                completed_at=utc_now(),
                progress={"current": 0, "total": 0, "percent": 0, "stage": "failed"},
            )

    async def _load_page_index(self, document_id: str) -> list[dict[str, Any]]:
        """Prefer current classification metadata, with read-only legacy fallback."""

        versions = [PageDiscoveryService.version]
        if PageDiscoveryService.version != "1":
            versions.append("1")
        for version in versions:
            pages = await self._repository.list_document_page_index(
                document_id=document_id,
                index_version=version,
            )
            if not pages:
                continue
            if version != PageDiscoveryService.version:
                for page in pages:
                    if page.get("page_type") in {"color_plate", "color_visual"}:
                        page.setdefault("semantic_text_source", False)
            return pages
        return []

    async def _ensure_not_cancelled(self, rematch_id: str) -> None:
        run = await self._repository.get_rematch_run_by_id(rematch_id)
        if run.get("cancel_requested"):
            raise asyncio.CancelledError

    @staticmethod
    def _domain_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            **{
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key not in {"_id", "created_at", "updated_at", "job_id"}
            },
            "id": str(item.get("id") or item.get("_id")),
        }

    @classmethod
    def _clean_record(cls, record: dict[str, Any]) -> dict[str, Any]:
        cleaned = cls._domain_item(record)
        for key in (
            "region_ids",
            "relation_ids",
            "associated_pages",
            "thumbnail_region_id",
            "primary_number_region_id",
            "primary_artifact_region_id",
            "primary_relation_id",
            "primary_link_score",
            "fusion_status",
            "entity_id",
            "entity_confidence",
            "entity_match_status",
            "review_status",
            "reviewed_at",
        ):
            cleaned.pop(key, None)
        for field in cleaned.get("fields", {}).values():
            if not isinstance(field, dict):
                continue
            for evidence in field.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                evidence.pop("linked_region_ids", None)
                evidence.pop("relation_ids", None)
        return cleaned

    @staticmethod
    def _restore_passed_record_links(
        *,
        records: list[dict[str, Any]],
        baseline_records: list[dict[str, Any]],
        passed_record_ids: set[str],
    ) -> None:
        baseline_by_id = {str(record.get("_id")): record for record in baseline_records}
        association_keys = (
            "region_ids",
            "relation_ids",
            "associated_pages",
            "thumbnail_region_id",
            "primary_number_region_id",
            "primary_artifact_region_id",
            "primary_relation_id",
            "primary_link_score",
            "fusion_status",
        )
        for record in records:
            record_id = str(record.get("id", ""))
            if record_id not in passed_record_ids:
                continue
            baseline = baseline_by_id.get(record_id)
            if baseline is None:
                continue
            for key in association_keys:
                record[key] = copy.deepcopy(baseline.get(key))

    @classmethod
    def _build_report(
        cls,
        *,
        baseline_relations: list[dict[str, Any]],
        candidate_relations: list[dict[str, Any]],
        candidate_records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        protection: dict[str, Any],
        conflict_relations: int = 0,
    ) -> dict[str, Any]:
        baseline = {str(item.get("_id") or item.get("id")): item for item in baseline_relations}
        candidate = {str(item.get("id") or item.get("_id")): item for item in candidate_relations}
        common = set(baseline) & set(candidate)
        changed = sum(
            baseline[key].get("method") != candidate[key].get("method")
            or baseline[key].get("score") != candidate[key].get("score")
            for key in common
        )
        scores = [
            float(relation["score"])
            for relation in candidate_relations
            if isinstance(relation.get("score"), (int, float))
        ]
        region_kind = {str(region["id"]): str(region.get("kind")) for region in regions}
        complete_chains = 0
        for record in candidate_records:
            kinds = {region_kind.get(str(region_id)) for region_id in record.get("region_ids", [])}
            if "artifact" in kinds and ("caption" in kinds or "number" in kinds):
                complete_chains += 1
        return {
            "total_records": len(candidate_records),
            "linked_records": sum(
                record.get("fusion_status") == "linked" for record in candidate_records
            ),
            "partial_records": sum(
                record.get("fusion_status") == "partial" for record in candidate_records
            ),
            "unlinked_records": sum(
                record.get("fusion_status") == "unlinked" for record in candidate_records
            ),
            "complete_chains": complete_chains,
            "ocr_exact_relations": sum(
                "ocr" in str(relation.get("method", "")) for relation in candidate_relations
            ),
            "layout_fallback_relations": sum(
                any(
                    marker in str(relation.get("method", ""))
                    for marker in ("fallback", "spatial", "nearest")
                )
                for relation in candidate_relations
            ),
            "conflict_relations": conflict_relations,
            "confidence": {
                "high": sum(score >= 0.85 for score in scores),
                "medium": sum(0.6 <= score < 0.85 for score in scores),
                "low": sum(score < 0.6 for score in scores),
            },
            "delta": {
                "added": len(set(candidate) - set(baseline)),
                "removed": len(set(baseline) - set(candidate)),
                "changed": changed,
                "unchanged": len(common) - changed,
            },
            "protection": {
                "accepted_relations": len(protection["accepted_relation_ids"]),
                "rejected_relations": len(protection["rejected_relation_keys"]),
                "passed_records": len(protection["passed_record_ids"]),
                "protected_relations": len(protection["protected_relation_ids"]),
            },
        }
