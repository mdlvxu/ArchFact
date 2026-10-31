from __future__ import annotations

from typing import Any
from uuid import uuid4

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from app.core.errors import NotFoundError
from app.domain.time import utc_now

_ACTIVE_EXTRACTION_STATUSES = [
    "queued",
    "preparing",
    "parsing",
    "extracting",
    "matching",
    "merging",
    "post_processing",
]


class JobPersistence:
    """Extraction jobs, page runs, events, model runs, and semantic cache."""

    async def get_semantic_extraction_cache(
        self,
        cache_key: str,
    ) -> dict[str, Any] | None:
        return await self._db.semantic_extraction_cache.find_one({"cache_key": cache_key})


    async def upsert_semantic_extraction_cache(
        self,
        *,
        cache_key: str,
        document_id: str,
        page_no: int,
        provider: str,
        model: str,
        schema_hash: str,
        text_hash: str,
        records: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        await self._db.semantic_extraction_cache.update_one(
            {"cache_key": cache_key},
            {
                "$set": {
                    "document_id": document_id,
                    "page_no": int(page_no),
                    "provider": provider,
                    "model": model,
                    "schema_hash": schema_hash,
                    "text_hash": text_hash,
                    "records": records,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "_id": f"semantic_cache_{uuid4().hex}",
                    "cache_key": cache_key,
                    "created_at": now,
                },
            },
            upsert=True,
        )


    async def create_job(
        self,
        *,
        document_id: str,
        pages: list[int] | None,
        pipeline_id: str,
        config: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing = await self._db.extraction_jobs.find_one({"idempotency_key": idempotency_key})
            if existing is not None:
                existing["_was_created"] = False
                return existing

        now = utc_now()
        job = {
            "_id": f"job_{uuid4().hex}",
            "document_id": document_id,
            "pages": pages,
            "pipeline_id": pipeline_id,
            "config": config,
            "status": "queued",
            "stage": "waiting",
            "progress": {"current": 0, "total": 0, "percent": 0},
            "cancel_requested": False,
            "page_issues": [],
            "succeeded_pages": 0,
            "failed_pages": 0,
            "active_matching_version_id": "M0",
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "attempt_started_at": None,
        }
        if idempotency_key:
            job["idempotency_key"] = idempotency_key
        try:
            await self._db.extraction_jobs.insert_one(job)
        except DuplicateKeyError:
            existing = await self._db.extraction_jobs.find_one({"idempotency_key": idempotency_key})
            if existing is None:
                raise
            existing["_was_created"] = False
            return existing
        job["_was_created"] = True
        return job


    async def get_job(self, job_id: str) -> dict[str, Any]:
        job = await self._db.extraction_jobs.find_one({"_id": job_id})
        if job is None:
            raise NotFoundError("抽取任务不存在")
        return job


    async def get_latest_completed_job(
        self,
        document_id: str | None = None,
        *,
        include_active: bool = False,
        filename_contains: str | None = None,
        prefer_verified: bool = False,
    ) -> dict[str, Any] | None:
        document_ids: list[str] | None = None
        if filename_contains:
            import re

            documents = await self._db.documents.find(
                {"filename": {"$regex": re.escape(filename_contains), "$options": "i"}},
                {"_id": 1},
            ).to_list(length=10000)
            document_ids = [str(item["_id"]) for item in documents]
            if not document_ids:
                return None
            if document_id and document_id not in document_ids:
                return None
        if include_active:
            active_query: dict[str, Any] = {
                "status": {"$in": _ACTIVE_EXTRACTION_STATUSES},
                "cancel_requested": {"$ne": True},
            }
            if document_id:
                active_query["document_id"] = document_id
            elif document_ids is not None:
                active_query["document_id"] = {"$in": document_ids}
            active = await self._db.extraction_jobs.find_one(
                active_query,
                sort=[("updated_at", DESCENDING), ("created_at", DESCENDING)],
            )
            if active is not None:
                return active

        query: dict[str, Any] = {
            "status": {"$in": ["completed", "completed_with_warnings"]},
        }
        if document_id:
            query["document_id"] = document_id
        elif document_ids is not None:
            query["document_id"] = {"$in": document_ids}
        if prefer_verified:
            versions = await self._db.verification_versions.find(
                {"version": {"$gte": 2}}, {"job_id": 1}
            ).to_list(length=10000)
            verified_ids = list({item["job_id"] for item in versions if item.get("job_id")})
            if verified_ids:
                verified = await self._db.extraction_jobs.find_one(
                    {**query, "_id": {"$in": verified_ids}},
                    sort=[("updated_at", DESCENDING), ("created_at", DESCENDING)],
                )
                if verified is not None:
                    return verified
        return await self._db.extraction_jobs.find_one(
            query,
            sort=[("updated_at", DESCENDING), ("created_at", DESCENDING)],
        )


    async def list_jobs_for_document(
        self,
        document_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        cursor = self._db.extraction_jobs.find(
            {"document_id": document_id},
            {"_id": 1, "document_id": 1, "status": 1, "created_at": 1, "updated_at": 1,
             "completed_at": 1, "config": 1, "active_matching_version_id": 1},
        ).sort("created_at", DESCENDING)
        return await cursor.to_list(length=max(1, min(limit, 200)))


    async def list_recent_completed_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        cursor = self._db.extraction_jobs.find(
            {"status": {"$in": ["completed", "completed_with_warnings"]}},
            {"_id": 1, "document_id": 1, "status": 1, "created_at": 1,
             "completed_at": 1, "config": 1, "active_matching_version_id": 1},
        ).sort("created_at", DESCENDING)
        jobs = await cursor.to_list(length=max(1, min(limit, 500)))
        document_ids = list({job["document_id"] for job in jobs if job.get("document_id")})
        documents = await self._db.documents.find(
            {"_id": {"$in": document_ids}}, {"_id": 1, "filename": 1}
        ).to_list(length=len(document_ids)) if document_ids else []
        filenames = {item["_id"]: item.get("filename", "") for item in documents}
        for job in jobs:
            job["document_filename"] = filenames.get(job.get("document_id"), "")
        return jobs


    async def update_job(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = utc_now()
        # Rematch / verification / matching-version updates bump updated_at. Elapsed time
        # must freeze at the extraction finish instant, so track completed_at separately.
        terminal_statuses = {
            "completed",
            "completed_with_warnings",
            "failed",
            "cancelled",
        }
        status = fields.get("status")
        if status in terminal_statuses:
            fields.setdefault("completed_at", utc_now())
        elif status is not None:
            fields["completed_at"] = None
        result = await self._db.extraction_jobs.update_one({"_id": job_id}, {"$set": fields})
        if result.matched_count == 0:
            raise NotFoundError("抽取任务不存在")


    async def upsert_job_page_run(
        self,
        *,
        job_id: str,
        document_id: str,
        page_no: int,
        **fields: Any,
    ) -> None:
        now = utc_now()
        await self._db.job_page_runs.update_one(
            {"job_id": job_id, "page_no": int(page_no)},
            {
                "$set": {
                    **fields,
                    "document_id": document_id,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "_id": f"page_run_{uuid4().hex}",
                    "job_id": job_id,
                    "page_no": int(page_no),
                    "created_at": now,
                },
            },
            upsert=True,
        )


    async def list_job_page_runs(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.job_page_runs.find({"job_id": job_id}).sort("page_no", 1)
        return await cursor.to_list(length=5000)


    async def request_cancel(self, job_id: str) -> dict[str, Any]:
        await self.update_job(
            job_id,
            cancel_requested=True,
            status="cancelling",
            stage="cancelling",
        )
        return await self.get_job(job_id)


    async def append_event(self, job_id: str, level: str, message: str) -> None:
        await self._db.job_events.insert_one(
            {
                "_id": f"evt_{uuid4().hex}",
                "job_id": job_id,
                "level": level,
                "message": message,
                "created_at": utc_now(),
            }
        )


    async def list_events(self, job_id: str, limit: int) -> list[dict[str, Any]]:
        cursor = (
            self._db.job_events.find({"job_id": job_id}).sort("created_at", DESCENDING).limit(limit)
        )
        events = await cursor.to_list(length=limit)
        events.reverse()
        return events


    async def create_model_run(
        self,
        *,
        job_id: str,
        stage: str,
        provider: str,
        model: str,
        version: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = {
            "_id": f"run_{uuid4().hex}",
            "job_id": job_id,
            "stage": stage,
            "provider": provider,
            "model": model,
            "version": version,
            "config": config or {},
            "status": "running",
            "started_at": utc_now(),
            "completed_at": None,
            "error": None,
        }
        await self._db.model_runs.insert_one(run)
        return run


    async def finish_model_run(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        await self._db.model_runs.update_one(
            {"_id": run_id},
            {"$set": {"status": status, "error": error, "completed_at": utc_now()}},
        )


    async def list_model_runs(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.model_runs.find({"job_id": job_id}).sort("started_at", 1)
        return await cursor.to_list(length=200)


    async def finalize_cancelling_extraction_jobs(self) -> int:
        """Complete jobs that were already cancelling when the process died."""

        now = utc_now()
        result = await self._db.extraction_jobs.update_many(
            {
                "$or": [
                    {"status": "cancelling"},
                    {
                        "status": {
                            "$in": [
                                "queued",
                                "preparing",
                                "parsing",
                                "extracting",
                                "matching",
                                "merging",
                                "post_processing",
                            ]
                        },
                        "cancel_requested": True,
                    },
                ]
            },
            [
                {
                    "$set": {
                        "completed_at": {"$ifNull": ["$completed_at", "$updated_at"]},
                    }
                },
                {
                    "$set": {
                        "status": "cancelled",
                        "stage": "cancelled",
                        "cancel_requested": True,
                        "error": None,
                        "updated_at": now,
                    }
                },
            ],
        )
        return int(result.modified_count)


    async def list_resumable_extraction_jobs(self) -> list[dict[str, Any]]:
        cursor = self._db.extraction_jobs.find(
            {
                "status": {"$in": _ACTIVE_EXTRACTION_STATUSES},
                "cancel_requested": {"$ne": True},
            }
        )
        return await cursor.to_list(length=1000)

