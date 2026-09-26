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

class QualityPersistence:
    """Quality evaluation runs and item metrics."""

    async def create_quality_evaluation_run(
        self,
        *,
        job_id: str,
        document_id: str,
        dataset_id: str,
        dataset_version: str,
        matching_version_id: str,
    ) -> dict[str, Any]:
        active = await self._db.quality_evaluation_runs.find_one(
            {"job_id": job_id, "status": {"$in": ["queued", "running"]}}
        )
        if active is not None:
            raise ConflictError("当前任务已有正在执行的质量评测")
        now = utc_now()
        run = {
            "_id": f"quality_{uuid4().hex}",
            "job_id": job_id,
            "document_id": document_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "matching_version_id": matching_version_id,
            "status": "queued",
            "progress": {"current": 0, "total": 5, "percent": 0, "stage": "queued"},
            "summary": None,
            "field_metrics": [],
            "ocr_metrics": [],
            "detection_metrics": [],
            "relation_metrics": {},
            "unmatched": {},
            "warnings": [],
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        await self._db.quality_evaluation_runs.insert_one(run)
        return run


    async def get_quality_evaluation_run(
        self,
        *,
        job_id: str,
        evaluation_id: str,
    ) -> dict[str, Any]:
        run = await self._db.quality_evaluation_runs.find_one(
            {"_id": evaluation_id, "job_id": job_id}
        )
        if run is None:
            raise NotFoundError("质量评测任务不存在")
        return run


    async def get_quality_evaluation_run_by_id(
        self,
        evaluation_id: str,
    ) -> dict[str, Any]:
        run = await self._db.quality_evaluation_runs.find_one({"_id": evaluation_id})
        if run is None:
            raise NotFoundError("质量评测任务不存在")
        return run


    async def list_quality_evaluation_runs(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.quality_evaluation_runs.find({"job_id": job_id}).sort(
            "created_at", DESCENDING
        )
        return await cursor.to_list(length=100)


    async def update_quality_evaluation_run(
        self,
        evaluation_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        fields["updated_at"] = utc_now()
        run = await self._db.quality_evaluation_runs.find_one_and_update(
            {"_id": evaluation_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if run is None:
            raise NotFoundError("质量评测任务不存在")
        return run


    async def replace_quality_evaluation_items(
        self,
        *,
        run_id: str,
        job_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        documents = [
            {
                "_id": f"qualityitem_{run_id}_{item['record_id']}",
                "evaluation_id": run_id,
                "job_id": job_id,
                "created_at": now,
                **item,
            }
            for item in items
        ]
        await self._replace_scoped_documents(
            self._db.quality_evaluation_items,
            scope={"evaluation_id": run_id},
            documents=documents,
        )


    async def list_quality_evaluation_items(
        self,
        *,
        job_id: str,
        evaluation_id: str,
        page: int,
        page_size: int,
        match_status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {"job_id": job_id, "evaluation_id": evaluation_id}
        if match_status:
            query["match_status"] = match_status
        total = await self._db.quality_evaluation_items.count_documents(query)
        cursor = (
            self._db.quality_evaluation_items.find(query)
            .sort([("artifact_id", 1), ("created_at", 1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return await cursor.to_list(length=page_size), total


    async def mark_stale_active_quality_evaluation_runs(self) -> int:
        now = utc_now()
        result = await self._db.quality_evaluation_runs.update_many(
            {"status": {"$in": ["queued", "running"]}},
            {
                "$set": {
                    "status": "failed",
                    "error": "质量评测在服务重启后中断，请重新启动评测",
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        return int(result.modified_count)

