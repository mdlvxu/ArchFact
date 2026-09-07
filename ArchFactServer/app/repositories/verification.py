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

class VerificationPersistence:
    """Verification cohorts, sessions, AI runs, and versions."""

    async def mark_stale_active_verification_runs(self) -> int:
        now = utc_now()
        result = await self._db.ai_verification_runs.update_many(
            {"status": {"$in": ["queued", "running"]}},
            {
                "$set": {
                    "status": "failed",
                    "error": "AI 复核在服务重启后中断，请重新点击完成核验",
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        await self._db.verification_sessions.update_many(
            {"status": "ai_review"},
            {
                "$set": {
                    "status": "in_progress",
                    "ai_run_id": None,
                    "updated_at": now,
                }
            },
        )
        return int(result.modified_count)


    async def get_or_create_verification_cohort(
        self,
        *,
        job_id: str,
        sample_size: int,
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = await self._db.verification_cohorts.find_one({"job_id": job_id})
        if existing is not None:
            if existing.get("sampling_strategy") == "balanced_correct_incorrect_v1":
                return existing
            await self._db.verification_cohorts.delete_one({"_id": existing["_id"]})

        records = (
            await self._db.extraction_records.find({"job_id": job_id})
            .sort([("source_pages", 1), ("created_at", 1)])
            .to_list(length=100000)
        )
        if not records:
            raise DomainError("当前抽取任务没有可核验的器物记录")

        seed = secrets.randbelow(2**31 - 1)
        relations = await self.list_job_relations(job_id)
        regions = await self.list_job_regions(job_id)
        selection = select_balanced_verification_sample(
            records=records,
            relations=relations,
            regions=regions,
            rules=rules,
            sample_size=sample_size,
            seed=seed,
        )
        now = utc_now()
        cohort = {
            "_id": f"cohort_{uuid4().hex}",
            "job_id": job_id,
            "sample_size": len(selection.record_ids),
            "random_seed": seed,
            "record_ids": selection.record_ids,
            "sampling_strategy": "balanced_correct_incorrect_v1",
            "eligible_count": selection.eligible_count,
            "correct_pool_size": selection.correct_pool_size,
            "incorrect_pool_size": selection.incorrect_pool_size,
            "strata_by_record": selection.metadata,
            "created_at": now,
        }
        try:
            await self._db.verification_cohorts.insert_one(cohort)
            return cohort
        except DuplicateKeyError:
            concurrent = await self._db.verification_cohorts.find_one({"job_id": job_id})
            if concurrent is None:
                raise
            return concurrent


    async def create_verification_session(
        self,
        *,
        job_id: str,
        rules: list[dict[str, Any]],
        sample_size: int,
    ) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job.get("status") not in {"completed", "completed_with_warnings"}:
            raise ConflictError("抽取任务完成后才能执行校验")

        active = await self.get_active_verification_session(job_id)
        if active is not None:
            if active.get("status") == "ai_review":
                active = await self.reopen_verification_session_for_human_review(
                    job_id=job_id,
                    session_id=active["_id"],
                )
            return active

        cohort = await self.get_or_create_verification_cohort(
            job_id=job_id,
            sample_size=sample_size,
            rules=rules,
        )
        cohort_records = await self._db.extraction_records.find(
            {"job_id": job_id, "_id": {"$in": cohort["record_ids"]}}
        ).to_list(length=max(len(cohort["record_ids"]), 1))
        record_by_id = {str(record["_id"]): record for record in cohort_records}
        latest = await self._db.verification_versions.find_one(
            {"job_id": job_id},
            sort=[("version", DESCENDING)],
        )
        target_version = int(latest.get("version", 0)) + 1 if latest else 1
        previous_item_by_id = {
            str(item.get("record_id")): item for item in (latest or {}).get("items", [])
        }
        strata_by_record = cohort.get("strata_by_record", {})

        def build_item(record_id: str) -> dict[str, Any]:
            record = record_by_id.get(str(record_id))
            relation_signature = self._record_relation_signature(record or {})
            previous_signature = str(
                previous_item_by_id.get(str(record_id), {}).get("relation_signature", "")
            )
            stale = record is None
            strata = strata_by_record.get(str(record_id), {})
            expected_label = strata.get("expected_label")
            sampling_strata = list(strata.get("rule_states", []))
            if expected_label:
                sampling_strata = [f"expected:{expected_label}", *sampling_strata]
            return {
                "record_id": record_id,
                "verdict": "stale" if stale else "unreviewed",
                "failure_code": None,
                "failure_reason": "固定样本在当前匹配版本中已失效" if stale else "",
                "relation_signature": relation_signature,
                "relation_changed": bool(
                    not stale
                    and previous_signature
                    and relation_signature != previous_signature
                ),
                "sampling_strata": sampling_strata,
                "expected_label": expected_label,
                "stale": stale,
                "reviewed_at": None,
                "ai_verdict": None,
                "ai_confidence": None,
                "ai_reason": "",
                "ai_field_results": [],
                "gold_record_id": None,
                "gold_match_status": None,
                "consensus_status": "pending",
                "conflict_resolved": False,
            }

        items = [build_item(record_id) for record_id in cohort["record_ids"]]
        now = utc_now()
        session = {
            "_id": f"verify_{uuid4().hex}",
            "job_id": job_id,
            "cohort_id": cohort["_id"],
            "target_version": target_version,
            "status": "in_progress",
            "matching_version_id": job.get("active_matching_version_id", "M0"),
            "rules": rules,
            "items": items,
            "reviewed_count": sum(item["verdict"] != "unreviewed" for item in items),
            "sample_count": len(cohort["record_ids"]),
            "version_id": None,
            "ai_run_id": None,
            "gold_dataset_id": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        try:
            await self._db.verification_sessions.insert_one(session)
        except DuplicateKeyError:
            concurrent = await self.get_active_verification_session(job_id)
            if concurrent is None:
                raise
            return concurrent
        return session


    async def reopen_verification_session_for_human_review(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        updated = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "ai_review"},
            {
                "$set": {
                    "status": "in_progress",
                    "ai_run_id": None,
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return updated
        return await self.get_verification_session(job_id=job_id, session_id=session_id)


    async def get_active_verification_session(self, job_id: str) -> dict[str, Any] | None:
        return await self._db.verification_sessions.find_one(
            {"job_id": job_id, "status": {"$in": ["in_progress", "ai_review", "conflict_review"]}}
        )


    async def get_verification_session(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        session = await self._db.verification_sessions.find_one(
            {"_id": session_id, "job_id": job_id}
        )
        if session is None:
            raise NotFoundError("校验会话不存在")
        return session


    async def list_verification_session_records(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        record_ids = [item["record_id"] for item in session.get("items", [])]
        cursor = self._db.extraction_records.find({"job_id": job_id, "_id": {"$in": record_ids}})
        records = await cursor.to_list(length=max(len(record_ids), 1))
        by_id = {record["_id"]: record for record in records}
        return [by_id[record_id] for record_id in record_ids if record_id in by_id]


    async def update_verification_item(
        self,
        *,
        job_id: str,
        session_id: str,
        record_id: str,
        verdict: str,
        failure_code: str | None,
        failure_reason: str,
    ) -> dict[str, Any]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") not in {"in_progress", "conflict_review"}:
            raise ConflictError("已完成的校验版本不能修改")
        items = session.get("items", [])
        if not any(item.get("record_id") == record_id for item in items):
            raise NotFoundError("该器物不属于当前固定样本集")
        if any(
            item.get("record_id") == record_id and item.get("stale")
            for item in items
        ):
            raise ConflictError("该固定样本在当前匹配版本中已失效，不能提交核验结果")

        now = utc_now()
        resolving_conflict = session.get("status") == "conflict_review"
        updated_items = [
            {
                **item,
                "verdict": verdict,
                "failure_code": failure_code if verdict == "failed" else None,
                "failure_reason": failure_reason if verdict == "failed" else "",
                "reviewed_at": now,
                "conflict_resolved": bool(
                    resolving_conflict and item.get("consensus_status") == "conflict"
                ),
                "consensus_status": (
                    "human_resolved"
                    if resolving_conflict and item.get("consensus_status") == "conflict"
                    else item.get("consensus_status", "pending")
                ),
            }
            if item.get("record_id") == record_id
            else item
            for item in items
        ]
        reviewed_count = sum(item.get("verdict") != "unreviewed" for item in updated_items)
        updated = await self._db.verification_sessions.find_one_and_update(
            {
                "_id": session_id,
                "job_id": job_id,
                "status": {"$in": ["in_progress", "conflict_review"]},
            },
            {
                "$set": {
                    "items": updated_items,
                    "reviewed_count": reviewed_count,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ConflictError("校验会话状态已经发生变化，请刷新后重试")
        return updated


    async def create_ai_verification_run(
        self,
        *,
        job_id: str,
        session_id: str,
        gold_dataset_id: str | None,
        total: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") != "in_progress":
            raise ConflictError("当前校验会话不能启动 AI 复核")
        now = utc_now()
        run = {
            "_id": f"airun_{uuid4().hex}",
            "job_id": job_id,
            "session_id": session_id,
            "status": "queued",
            "progress": {"current": 0, "total": total, "percent": 0},
            "gold_dataset_id": gold_dataset_id,
            "benchmark_available": bool(gold_dataset_id),
            "conflict_count": 0,
            "uncertain_count": 0,
            "version_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        await self._db.ai_verification_runs.insert_one(run)
        updated = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "in_progress"},
            {
                "$set": {
                    "status": "ai_review",
                    "ai_run_id": run["_id"],
                    "gold_dataset_id": gold_dataset_id,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await self._db.ai_verification_runs.delete_one({"_id": run["_id"]})
            raise ConflictError("校验会话状态已发生变化，请刷新后重试")
        return updated, run


    async def get_ai_verification_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self._db.ai_verification_runs.find_one({"_id": run_id, "job_id": job_id})
        if run is None:
            raise NotFoundError("AI 复核任务不存在")
        return run


    async def update_ai_verification_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = utc_now()
        run = await self._db.ai_verification_runs.find_one_and_update(
            {"_id": run_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if run is None:
            raise NotFoundError("AI 复核任务不存在")
        return run


    async def apply_ai_verification_results(
        self,
        *,
        job_id: str,
        session_id: str,
        run_id: str,
        results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") != "ai_review" or session.get("ai_run_id") != run_id:
            raise ConflictError("AI 复核结果对应的会话已发生变化")
        items: list[dict[str, Any]] = []
        now = utc_now()
        for item in session.get("items", []):
            result = results.get(str(item.get("record_id")))
            if not result:
                items.append(item)
                continue
            human_verdict = item.get("verdict")
            human_reviewed_at = item.get("reviewed_at")
            human_failure_code = item.get("failure_code")
            human_failure_reason = item.get("failure_reason")
            merged = {**item, **result}
            merged["verdict"] = human_verdict
            merged["reviewed_at"] = human_reviewed_at
            merged["failure_code"] = human_failure_code
            merged["failure_reason"] = human_failure_reason
            items.append(merged)
        updated = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "ai_review", "ai_run_id": run_id},
            {
                "$set": {
                    "items": items,
                    # Keep ai_review until finalize freezes the version. Conflicts are
                    # informational only and no longer route users into conflict_review.
                    "status": "ai_review",
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ConflictError("AI 复核结果保存失败，请刷新后重试")
        return updated


    async def reset_failed_ai_verification(
        self,
        *,
        job_id: str,
        session_id: str,
        run_id: str,
    ) -> None:
        await self._db.verification_sessions.update_one(
            {"_id": session_id, "job_id": job_id, "status": "ai_review", "ai_run_id": run_id},
            {"$set": {"status": "in_progress", "ai_run_id": None, "updated_at": utc_now()}},
        )


    async def finalize_verification_session(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") == "completed" and session.get("version_id"):
            version = await self._db.verification_versions.find_one(
                {"_id": session["version_id"], "job_id": job_id}
            )
            if version is not None:
                return session, version

        items = session.get("items", [])
        unreviewed = [item for item in items if item.get("verdict") == "unreviewed"]
        if unreviewed:
            raise ConflictError(f"还有 {len(unreviewed)} 条样本尚未完成核验")

        pass_count = sum(item.get("verdict") == "passed" for item in items)
        fail_count = sum(item.get("verdict") == "failed" for item in items)
        stale_count = sum(bool(item.get("stale")) for item in items)
        changed_count = sum(bool(item.get("relation_changed")) for item in items)
        total_artifacts = await self._db.extraction_records.count_documents({"job_id": job_id})
        sample_count = len(items)
        reviewed_count = pass_count + fail_count
        report = {
            "sample_count": sample_count,
            "reviewed_count": reviewed_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "stale_count": stale_count,
            "relation_changed_count": changed_count,
            "pass_rate": round(pass_count / reviewed_count, 4) if reviewed_count else 0.0,
            "total_artifacts": total_artifacts,
            "ai_pass_count": sum(item.get("ai_verdict") == "passed" for item in items),
            "ai_fail_count": sum(item.get("ai_verdict") == "failed" for item in items),
            "ai_uncertain_count": sum(item.get("ai_verdict") == "uncertain" for item in items),
            "conflict_count": sum(
                item.get("consensus_status") in {"conflict", "human_resolved"} for item in items
            ),
            "benchmark_matched_count": sum(
                item.get("gold_match_status") == "matched" for item in items
            ),
        }
        latest = await self._db.verification_versions.find_one(
            {"job_id": job_id},
            sort=[("version", DESCENDING)],
        )
        parent_version_id = latest["_id"] if latest else None
        now = utc_now()
        version = {
            "_id": f"version_{uuid4().hex}",
            "job_id": job_id,
            "cohort_id": session["cohort_id"],
            "version": session["target_version"],
            "parent_version_id": parent_version_id,
            "matching_version_id": session.get("matching_version_id", "M0"),
            "rules": session.get("rules", []),
            "items": items,
            "report": report,
            "ai_run_id": session.get("ai_run_id"),
            "gold_dataset_id": session.get("gold_dataset_id"),
            "gold_dataset_version": None,
            "created_at": now,
        }
        if session.get("gold_dataset_id"):
            dataset = await self._db.gold_datasets.find_one({"_id": session["gold_dataset_id"]})
            version["gold_dataset_version"] = (dataset or {}).get("version")
        try:
            await self._db.verification_versions.insert_one(version)
        except DuplicateKeyError as exc:
            raise ConflictError("该校验版本已经存在，请刷新后重试") from exc

        completed = await self._db.verification_sessions.find_one_and_update(
            {
                "_id": session_id,
                "job_id": job_id,
                "status": {"$in": ["in_progress", "ai_review", "conflict_review"]},
            },
            {
                "$set": {
                    "status": "completed",
                    "version_id": version["_id"],
                    "reviewed_count": sample_count,
                    "updated_at": now,
                    "completed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if completed is None:
            await self._db.verification_versions.delete_one({"_id": version["_id"]})
            raise ConflictError("校验会话状态已经发生变化，请刷新后重试")
        return completed, version


    async def list_verification_versions(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.verification_versions.find({"job_id": job_id}).sort("version", DESCENDING)
        return await cursor.to_list(length=1000)


    async def get_verification_version(
        self,
        *,
        job_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        version = await self._db.verification_versions.find_one(
            {"_id": version_id, "job_id": job_id}
        )
        if version is None:
            raise NotFoundError("校验版本不存在")
        return version

