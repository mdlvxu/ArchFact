from __future__ import annotations

import hashlib
import secrets
from typing import Any
from uuid import uuid4

from pymongo import DESCENDING, ReplaceOne, ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.domain.page_semantics import PageSemantics
from app.domain.relations import relation_key
from app.domain.time import utc_now
from app.domain.verification_sampling import select_balanced_verification_sample
from app.infrastructure.mongodb import MongoDatabase

class RematchPersistence:
    """Rematch preview snapshots and apply/rollback."""

    async def create_rematch_run(
        self,
        *,
        job_id: str,
        preserve_reviewed: bool,
        apply_immediately: bool,
    ) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job.get("status") not in {"completed", "completed_with_warnings"}:
            raise ConflictError("抽取任务完成后才能重新匹配")
        active = await self._db.rematch_runs.find_one(
            {
                "job_id": job_id,
                "status": {"$in": ["queued", "running", "applying", "cancelling"]},
            }
        )
        if active is not None:
            raise ConflictError("当前任务已有正在执行的重新匹配")
        now = utc_now()
        run = {
            "_id": f"rematch_{uuid4().hex}",
            "job_id": job_id,
            "base_matching_version_id": job.get("active_matching_version_id", "M0"),
            "status": "queued",
            "preserve_reviewed": preserve_reviewed,
            "apply_immediately": apply_immediately,
            "cancel_requested": False,
            "progress": {"current": 0, "total": 0, "percent": 0, "stage": "waiting"},
            "report": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "applied_at": None,
        }
        await self._db.rematch_runs.insert_one(run)
        return run


    async def get_rematch_run(self, job_id: str, rematch_id: str) -> dict[str, Any]:
        run = await self._db.rematch_runs.find_one({"_id": rematch_id, "job_id": job_id})
        if run is None:
            raise NotFoundError("重新匹配任务不存在")
        return run


    async def get_rematch_run_by_id(self, rematch_id: str) -> dict[str, Any]:
        run = await self._db.rematch_runs.find_one({"_id": rematch_id})
        if run is None:
            raise NotFoundError("重新匹配任务不存在")
        return run


    async def update_rematch_run(self, rematch_id: str, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = utc_now()
        run = await self._db.rematch_runs.find_one_and_update(
            {"_id": rematch_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if run is None:
            raise NotFoundError("重新匹配任务不存在")
        return run


    async def mark_stale_active_rematch_runs(self) -> int:
        """Finalize rematch runs that cannot continue after process restart."""

        now = utc_now()
        result = await self._db.rematch_runs.update_many(
            {
                "status": {
                    "$in": ["queued", "running", "applying", "cancelling"],
                }
            },
            {
                "$set": {
                    "status": "failed",
                    "cancel_requested": True,
                    "error": "重新匹配任务在服务重启后失效",
                    "completed_at": now,
                    "updated_at": now,
                    "progress": {
                        "current": 0,
                        "total": 0,
                        "percent": 0,
                        "stage": "failed",
                    },
                }
            },
        )
        return int(result.modified_count)


    async def request_rematch_cancel(self, job_id: str, rematch_id: str) -> dict[str, Any]:
        run = await self.get_rematch_run(job_id, rematch_id)
        if run.get("status") in {"completed", "applied", "failed", "cancelled"}:
            return run
        return await self.update_rematch_run(
            rematch_id,
            cancel_requested=True,
            status="cancelling",
            progress={**run.get("progress", {}), "stage": "cancelling"},
        )


    async def get_rematch_protection(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        relations = await self.list_job_relations(job_id)
        relation_by_id = {str(relation["_id"]): relation for relation in relations}
        accepted = {
            relation_id
            for relation_id, relation in relation_by_id.items()
            if relation.get("review_status") == "accepted"
            or relation.get("method") == "manual_rebind"
        }
        rejected_keys = {
            self._relation_key(relation)
            for relation in relations
            if relation.get("review_status") == "rejected"
        }
        passed_records: set[str] = set()
        protected_records: set[str] = set()
        protected_relation_ids = set(accepted)
        latest = await self._db.verification_versions.find_one(
            {
                "job_id": job_id,
                "matching_version_id": job.get("active_matching_version_id", "M0"),
            },
            sort=[("version", DESCENDING)],
        )
        if latest is not None:
            records = {str(record["_id"]): record for record in await self.list_job_records(job_id)}
            for item in latest.get("items", []):
                verdict = item.get("verdict")
                protects_chain = self._verification_protects_chain(
                    verdict,
                    item.get("failure_code"),
                )
                if not protects_chain:
                    continue
                record = records.get(str(item.get("record_id")))
                if record is None:
                    continue
                signature = self._record_relation_signature(record)
                if item.get("relation_signature") and item.get("relation_signature") != signature:
                    continue
                record_id = str(record["_id"])
                protected_records.add(record_id)
                if verdict == "passed":
                    passed_records.add(record_id)
                protected_relation_ids.update(
                    str(value) for value in record.get("relation_ids", [])
                )
        return {
            "accepted_relation_ids": accepted,
            "rejected_relation_keys": rejected_keys,
            "passed_record_ids": passed_records,
            "protected_record_ids": protected_records,
            "protected_relation_ids": protected_relation_ids,
            "relation_by_id": relation_by_id,
        }


    async def save_rematch_snapshot(
        self,
        *,
        rematch_id: str,
        job_id: str,
        baseline_relations: list[dict[str, Any]],
        baseline_records: list[dict[str, Any]],
        baseline_entities: list[dict[str, Any]],
        candidate_relations: list[dict[str, Any]],
        candidate_records: list[dict[str, Any]],
        candidate_entities: list[dict[str, Any]],
        baseline_inferred_regions: list[dict[str, Any]],
        candidate_inferred_regions: list[dict[str, Any]],
        report: dict[str, Any],
    ) -> None:
        await self._db.rematch_relations.delete_many({"rematch_id": rematch_id})
        await self._db.rematch_records.delete_many({"rematch_id": rematch_id})
        await self._db.rematch_entities.delete_many({"rematch_id": rematch_id})
        relation_docs = self._snapshot_documents(
            rematch_id,
            job_id,
            baseline_relations,
            candidate_relations,
            "relation",
        )
        record_docs = self._snapshot_documents(
            rematch_id,
            job_id,
            baseline_records,
            candidate_records,
            "record",
        )
        entity_docs = self._snapshot_documents(
            rematch_id,
            job_id,
            baseline_entities,
            candidate_entities,
            "entity",
        )
        if relation_docs:
            await self._db.rematch_relations.insert_many(relation_docs)
        if record_docs:
            await self._db.rematch_records.insert_many(record_docs)
        if entity_docs:
            await self._db.rematch_entities.insert_many(entity_docs)
        await self.update_rematch_run(
            rematch_id,
            status="completed",
            progress={"current": 1, "total": 1, "percent": 100, "stage": "completed"},
            report=report,
            baseline_inferred_regions=baseline_inferred_regions,
            candidate_inferred_regions=candidate_inferred_regions,
            completed_at=utc_now(),
        )


    async def load_rematch_snapshot(
        self,
        *,
        rematch_id: str,
        snapshot_kind: str = "candidate",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        async def load(collection: Any) -> list[dict[str, Any]]:
            docs = await collection.find(
                {"rematch_id": rematch_id, "snapshot_kind": snapshot_kind}
            ).sort("position", 1).to_list(length=200000)
            return [dict(document.get("payload", {})) for document in docs]

        return (
            await load(self._db.rematch_relations),
            await load(self._db.rematch_records),
            await load(self._db.rematch_entities),
        )


    async def get_rematch_relation_changes(
        self,
        *,
        job_id: str,
        rematch_id: str,
    ) -> list[dict[str, Any]]:
        """Return an auditable relation-level diff between preview and formal data."""
        await self.get_rematch_run(job_id, rematch_id)
        baseline_relations, _, _ = await self.load_rematch_snapshot(
            rematch_id=rematch_id,
            snapshot_kind="baseline",
        )
        candidate_relations, _, _ = await self.load_rematch_snapshot(
            rematch_id=rematch_id,
            snapshot_kind="candidate",
        )

        def relation_id(relation: dict[str, Any]) -> str:
            return str(relation.get("id") or relation.get("_id") or "")

        def numeric_score(value: Any) -> float | None:
            return float(value) if isinstance(value, (int, float)) else None

        def is_protected(relation: dict[str, Any] | None) -> bool:
            if relation is None:
                return False
            return relation.get("review_status") == "accepted" or relation.get(
                "method"
            ) == "manual_rebind"

        baseline = {relation_id(item): item for item in baseline_relations if relation_id(item)}
        candidate = {
            relation_id(item): item for item in candidate_relations if relation_id(item)
        }
        changes: list[dict[str, Any]] = []
        all_ids = sorted(set(baseline) | set(candidate))
        compared_fields = (
            "relation_type",
            "source_region_id",
            "target_region_id",
            "method",
            "score",
        )
        for item_id in all_ids:
            before = baseline.get(item_id)
            after = candidate.get(item_id)
            if before is None:
                change = "added"
            elif after is None:
                change = "removed"
            elif any(before.get(field) != after.get(field) for field in compared_fields):
                change = "changed"
            else:
                continue
            current = after or before or {}
            changes.append(
                {
                    "change": change,
                    "relation_id": item_id,
                    "relation_type": str(current.get("relation_type") or ""),
                    "source_region_id": str(current.get("source_region_id") or ""),
                    "target_region_id": str(current.get("target_region_id") or ""),
                    "before_method": str(before.get("method")) if before else None,
                    "after_method": str(after.get("method")) if after else None,
                    "before_score": numeric_score(before.get("score")) if before else None,
                    "after_score": numeric_score(after.get("score")) if after else None,
                    "protected": is_protected(before) or is_protected(after),
                }
            )
        return changes


    async def apply_rematch_snapshot(self, *, job_id: str, rematch_id: str) -> dict[str, Any]:
        run = await self.get_rematch_run(job_id, rematch_id)
        if run.get("status") == "applied":
            return run
        if run.get("status") != "completed":
            raise ConflictError("只有已完成的预览结果才能应用")
        active_verification = await self._db.verification_sessions.find_one(
            {"job_id": job_id, "status": {"$in": ["in_progress", "ai_review", "conflict_review"]}}
        )
        if active_verification is not None:
            raise ConflictError("请先完成当前人工核验，再应用新的匹配版本")
        job = await self.get_job(job_id)
        if job.get("active_matching_version_id", "M0") != run.get(
            "base_matching_version_id", "M0"
        ):
            raise ConflictError("当前正式匹配版本已变化，请重新生成预览")
        await self.update_rematch_run(rematch_id, status="applying")
        relations, records, entities = await self.load_rematch_snapshot(rematch_id=rematch_id)
        try:
            await self.replace_inferred_color_plate_regions(
                job_id,
                list(run.get("candidate_inferred_regions") or []),
            )
            await self.replace_job_relations(job_id, relations)
            await self.replace_job_records(job_id, records, preserve_reviews=True)
            await self.replace_job_entities(
                job_id=job_id,
                document_id=job["document_id"],
                entities=entities,
            )
            await self.update_job(job_id, active_matching_version_id=rematch_id)
        except Exception as exc:
            baseline_relations, baseline_records, baseline_entities = (
                await self.load_rematch_snapshot(
                    rematch_id=rematch_id,
                    snapshot_kind="baseline",
                )
            )
            await self.replace_inferred_color_plate_regions(
                job_id,
                list(run.get("baseline_inferred_regions") or []),
            )
            await self.replace_job_relations(job_id, baseline_relations)
            await self.replace_job_records(job_id, baseline_records, preserve_reviews=True)
            await self.replace_job_entities(
                job_id=job_id,
                document_id=job["document_id"],
                entities=baseline_entities,
            )
            await self.update_rematch_run(
                rematch_id,
                status="completed",
                error=f"应用失败，已自动恢复原版本：{exc}",
            )
            raise
        return await self.update_rematch_run(
            rematch_id,
            status="applied",
            applied_at=utc_now(),
            progress={"current": 1, "total": 1, "percent": 100, "stage": "applied"},
        )


    @staticmethod
    def _snapshot_documents(
        rematch_id: str,
        job_id: str,
        baseline: list[dict[str, Any]],
        candidate: list[dict[str, Any]],
        item_kind: str,
    ) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        now = utc_now()
        for snapshot_kind, items in (("baseline", baseline), ("candidate", candidate)):
            for position, item in enumerate(items):
                payload = {
                    **{
                        key: value
                        for key, value in item.items()
                        if key not in {"_id", "created_at", "updated_at"}
                    },
                    "id": item.get("id") or item.get("_id"),
                }
                documents.append(
                    {
                        "_id": f"{rematch_id}:{snapshot_kind}:{item_kind}:{position}",
                        "rematch_id": rematch_id,
                        "job_id": job_id,
                        "snapshot_kind": snapshot_kind,
                        "item_kind": item_kind,
                        "position": position,
                        "payload": payload,
                        "created_at": now,
                    }
                )
        return documents

