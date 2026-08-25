import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import DESCENDING, ReplaceOne, ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.infrastructure.mongodb import MongoDatabase
from app.services.page_semantics import PageSemantics
from app.services.verification_sampling import select_stratified_verification_sample


def utc_now() -> datetime:
    return datetime.now(UTC)


class MongoRepository:
    def __init__(self, database: MongoDatabase) -> None:
        self._db = database.database
        self._reference_index_page_cache: dict[str, list[int]] = {}

    async def create_document(
        self,
        *,
        filename: str,
        content_type: str,
        size: int,
        sha256: str,
        gridfs_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        document = {
            "_id": f"doc_{uuid4().hex}",
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "sha256": sha256,
            "storage": {"type": "gridfs", "file_id": gridfs_id},
            "page_count": None,
            "status": "uploaded",
            "error": None,
            "created_at": now,
            "attempt_started_at": now,
            "updated_at": now,
        }
        await self._db.documents.insert_one(document)
        return document

    async def get_document(self, document_id: str) -> dict[str, Any]:
        document = await self._db.documents.find_one({"_id": document_id})
        if document is None:
            raise NotFoundError("PDF 文档不存在")
        return document

    async def update_document(self, document_id: str, **fields: Any) -> None:
        fields["updated_at"] = utc_now()
        result = await self._db.documents.update_one({"_id": document_id}, {"$set": fields})
        if result.matched_count == 0:
            raise NotFoundError("PDF 文档不存在")

    async def replace_gold_dataset(
        self,
        *,
        dataset: dict[str, Any],
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        assets: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> None:
        """Replace an evaluation dataset without touching production extraction data."""
        dataset_id = dataset["_id"]
        await self._db.gold_datasets.replace_one({"_id": dataset_id}, dataset, upsert=True)
        for collection in (
            self._db.gold_records,
            self._db.gold_regions,
            self._db.gold_assets,
            self._db.gold_links,
        ):
            await collection.delete_many({"dataset_id": dataset_id})
        if records:
            await self._db.gold_records.insert_many(records, ordered=False)
        if regions:
            await self._db.gold_regions.insert_many(regions, ordered=False)
        if assets:
            await self._db.gold_assets.insert_many(assets, ordered=False)
        if links:
            await self._db.gold_links.insert_many(links, ordered=False)

    async def get_gold_dataset(self, dataset_id: str) -> dict[str, Any]:
        dataset = await self._db.gold_datasets.find_one({"_id": dataset_id})
        if dataset is None:
            raise NotFoundError("人工标注数据集不存在")
        return dataset

    async def get_gold_dataset_for_document(
        self,
        *,
        document_id: str,
        version: str | None = None,
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {"document_id": document_id, "status": "ready"}
        if version is not None:
            query["version"] = version
        return await self._db.gold_datasets.find_one(query, sort=[("updated_at", DESCENDING)])

    async def list_gold_datasets(self) -> list[dict[str, Any]]:
        cursor = self._db.gold_datasets.find({}).sort("updated_at", DESCENDING)
        return await cursor.to_list(length=1000)

    async def find_gold_records_by_artifact_id(
        self,
        *,
        dataset_id: str,
        canonical_artifact_id: str,
    ) -> list[dict[str, Any]]:
        cursor = self._db.gold_records.find(
            {
                "dataset_id": dataset_id,
                "canonical_artifact_id": canonical_artifact_id,
            }
        )
        return await cursor.to_list(length=20)

    async def get_gold_record_assets(
        self,
        *,
        dataset_id: str,
        record_id: str,
    ) -> list[dict[str, Any]]:
        links = await self._db.gold_links.find(
            {"dataset_id": dataset_id, "record_id": record_id}
        ).to_list(length=100)
        asset_ids = [link["asset_id"] for link in links]
        if not asset_ids:
            return []
        assets = await self._db.gold_assets.find(
            {"dataset_id": dataset_id, "_id": {"$in": asset_ids}}
        ).to_list(length=len(asset_ids))
        link_by_asset = {link["asset_id"]: link for link in links}
        return [{**asset, "link": link_by_asset.get(asset["_id"], {})} for asset in assets]

    async def list_gold_records(self, dataset_id: str) -> list[dict[str, Any]]:
        cursor = self._db.gold_records.find({"dataset_id": dataset_id}).sort("source_row", 1)
        return await cursor.to_list(length=100000)

    async def list_gold_regions(self, dataset_id: str) -> list[dict[str, Any]]:
        cursor = self._db.gold_regions.find({"dataset_id": dataset_id}).sort(
            [("page", 1), ("source_line", 1)]
        )
        return await cursor.to_list(length=200000)

    async def list_gold_links(self, dataset_id: str) -> list[dict[str, Any]]:
        cursor = self._db.gold_links.find({"dataset_id": dataset_id})
        return await cursor.to_list(length=200000)

    async def list_document_pages(self, document_id: str) -> list[dict[str, Any]]:
        cursor = self._db.document_pages.find({"document_id": document_id}).sort("page_no", 1)
        return await cursor.to_list(length=10000)

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
        await self._db.quality_evaluation_items.delete_many({"evaluation_id": run_id})
        if not items:
            return
        now = utc_now()
        documents = [
            {
                "_id": f"qualityitem_{uuid4().hex}",
                "evaluation_id": run_id,
                "job_id": job_id,
                "created_at": now,
                **item,
            }
            for item in items
        ]
        await self._db.quality_evaluation_items.insert_many(documents, ordered=False)

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

    async def list_extraction_templates(self) -> list[dict[str, Any]]:
        cursor = self._db.extraction_templates.find({}).sort("position", 1)
        return await cursor.to_list(length=200)

    async def replace_extraction_templates(self, templates: list[dict[str, Any]]) -> None:
        now = utc_now()
        template_ids = [template["id"] for template in templates]
        operations = []
        for position, template in enumerate(templates):
            document = {
                "_id": template["id"],
                "name": template["name"],
                "fields": template["fields"],
                "builtin": template.get("builtin", False),
                "position": position,
                "updated_at": now,
            }
            operations.append(
                ReplaceOne(
                    {"_id": template["id"]},
                    {**document, "created_at": now},
                    upsert=True,
                )
            )
        if operations:
            await self._db.extraction_templates.bulk_write(operations)
        await self._db.extraction_templates.delete_many({"_id": {"$nin": template_ids}})

    async def count_extraction_templates(self) -> int:
        return await self._db.extraction_templates.count_documents({})

    async def list_post_processing_rules(self) -> list[dict[str, Any]]:
        cursor = self._db.post_processing_rules.find({}).sort("position", 1)
        return await cursor.to_list(length=200)

    async def replace_post_processing_rules(self, rules: list[dict[str, Any]]) -> None:
        now = utc_now()
        rule_ids = [rule["id"] for rule in rules]
        operations = []
        for position, rule in enumerate(rules):
            document = {
                "_id": rule["id"],
                "key": rule["key"],
                "name": rule["name"],
                "description": rule.get("description", ""),
                "example": rule.get("example", ""),
                "handler": rule.get("handler", "builtin"),
                "enabled": rule.get("enabled", True),
                "builtin": rule.get("builtin", False),
                "position": position,
                "updated_at": now,
            }
            operations.append(
                ReplaceOne(
                    {"_id": rule["id"]},
                    {**document, "created_at": now},
                    upsert=True,
                )
            )
        if operations:
            await self._db.post_processing_rules.bulk_write(operations)
        await self._db.post_processing_rules.delete_many({"_id": {"$nin": rule_ids}})

    async def count_post_processing_rules(self) -> int:
        return await self._db.post_processing_rules.count_documents({})

    async def get_page_render_image(self, document_id: str, page_no: int) -> dict[str, Any] | None:
        return await self._db.document_images.find_one(
            {"document_id": document_id, "page_no": page_no, "image_type": "page_render"}
        )

    async def upsert_document_image(self, image: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        identity = (
            {
                "document_id": image["document_id"],
                "page_no": image["page_no"],
                "image_type": "page_render",
            }
            if image["image_type"] == "page_render"
            else {"_id": image["id"]}
        )
        await self._db.document_images.update_one(
            identity,
            {
                "$set": {**image, "updated_at": now},
                "$setOnInsert": {"_id": image["id"], "created_at": now},
            },
            upsert=True,
        )
        stored = await self._db.document_images.find_one(identity)
        if stored is None:
            raise RuntimeError("图片元数据写入失败")
        return stored

    async def list_document_images(self, document_id: str) -> list[dict[str, Any]]:
        cursor = self._db.document_images.find({"document_id": document_id}).sort("page_no", 1)
        return await cursor.to_list(length=5000)

    async def get_document_image(self, document_id: str, image_id: str) -> dict[str, Any]:
        image = await self._db.document_images.find_one(
            {"_id": image_id, "document_id": document_id}
        )
        if image is None:
            raise NotFoundError("图片不存在")
        return image

    async def upsert_pages(self, document_id: str, pages: list[dict[str, Any]]) -> None:
        if not pages:
            return
        operations = []
        for page in pages:
            data = {
                "document_id": document_id,
                "page_no": page["page_no"],
                "text": page["text"],
                "pdf_text": page.get("pdf_text", ""),
                "ocr_text": page.get("ocr_text", ""),
                "blocks": page.get("blocks", []),
                "pdf_blocks": page.get("pdf_blocks", []),
                "ocr_blocks": page.get("ocr_blocks", []),
                "effective_text_source": page.get("effective_text_source", "none"),
                "parse_method": page.get("parse_method", "text"),
                "status": page.get("status", "ready"),
                "needs_ocr": page.get("needs_ocr", False),
                "error": page.get("error"),
                "page_width": page.get("page_width"),
                "page_height": page.get("page_height"),
                "text_char_count": page.get("text_char_count", len(page.get("text", ""))),
                "image_id": page.get("image_id"),
                "image_object_key": page.get("image_object_key"),
                "image_width": page.get("image_width"),
                "image_height": page.get("image_height"),
                "render_scale": page.get("render_scale"),
                "render_cache_hit": page.get("render_cache_hit", False),
                "ocr_attempted": page.get("ocr_attempted", False),
                "ocr_status": page.get("ocr_status", "not_requested"),
                "ocr_error": page.get("ocr_error"),
                "ocr_provider": page.get("ocr_provider"),
                "ocr_model": page.get("ocr_model"),
                "ocr_version": page.get("ocr_version"),
                "ocr_config_hash": page.get("ocr_config_hash"),
                "ocr_cache_hit": page.get("ocr_cache_hit", False),
                "ocr_ms": page.get("ocr_ms", 0),
                "text_model_run_id": page.get("text_model_run_id"),
                "classifier_version": page.get("classifier_version"),
                "page_type": page.get("page_type", "unknown"),
                "raw_page_type": page.get("raw_page_type", "unknown"),
                "classification_confidence": page.get(
                    "classification_confidence", 0.0
                ),
                "classification_reason": page.get("classification_reason"),
                "semantic_text_source": page.get("semantic_text_source", True),
                "linkage_ocr_enabled": page.get("linkage_ocr_enabled", True),
                "visual_detection_enabled": page.get(
                    "visual_detection_enabled", True
                ),
                "color_ratio": page.get("color_ratio", 0.0),
                "foreground_color_ratio": page.get(
                    "foreground_color_ratio", 0.0
                ),
                "color_tile_ratio": page.get("color_tile_ratio", 0.0),
                "chroma_p95": page.get("chroma_p95", 0.0),
                "updated_at": utc_now(),
            }
            operations.append(
                UpdateOne(
                    {"document_id": document_id, "page_no": page["page_no"]},
                    {"$set": data, "$setOnInsert": {"created_at": utc_now()}},
                    upsert=True,
                )
            )
        await self._db.document_pages.bulk_write(operations)

    async def replace_document_page_index(
        self,
        *,
        document_id: str,
        index_version: str,
        pages: list[dict[str, Any]],
    ) -> None:
        await self._db.document_page_index.delete_many(
            {"document_id": document_id, "index_version": index_version}
        )
        if not pages:
            return
        now = utc_now()
        documents = [
            {
                "_id": f"pageidx_{document_id}_{index_version}_{int(page['page_no']):04d}",
                "document_id": document_id,
                **page,
                "index_version": index_version,
                "created_at": now,
                "updated_at": now,
            }
            for page in pages
        ]
        await self._db.document_page_index.insert_many(documents)

    async def list_document_page_index(
        self,
        *,
        document_id: str,
        index_version: str,
    ) -> list[dict[str, Any]]:
        cursor = self._db.document_page_index.find(
            {"document_id": document_id, "index_version": index_version}
        ).sort("page_no", 1)
        return await cursor.to_list(length=5000)

    async def replace_document_text_chunks(
        self,
        *,
        job_id: str,
        document_id: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        await self._db.document_text_chunks.delete_many({"job_id": job_id})
        if not chunks:
            return
        now = utc_now()
        documents = [
            {
                "_id": chunk["id"],
                **{key: value for key, value in chunk.items() if key != "id"},
                "job_id": job_id,
                "document_id": document_id,
                "created_at": now,
                "updated_at": now,
            }
            for chunk in chunks
        ]
        await self._db.document_text_chunks.insert_many(documents)

    async def list_document_text_chunks(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.document_text_chunks.find({"job_id": job_id}).sort(
            "ordinal", 1
        )
        return await cursor.to_list(length=100000)

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

    async def get_latest_completed_job(self) -> dict[str, Any] | None:
        return await self._db.extraction_jobs.find_one(
            {"status": {"$in": ["completed", "completed_with_warnings"]}},
            sort=[("created_at", DESCENDING)],
        )

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

    async def replace_job_records(
        self,
        job_id: str,
        records: list[dict[str, Any]],
        model_run_ids: list[str] | None = None,
        preserve_reviews: bool = False,
    ) -> None:
        review_by_id: dict[str, dict[str, Any]] = {}
        if preserve_reviews:
            previous = await self._db.extraction_records.find(
                {"job_id": job_id},
                {"review_status": 1, "reviewed_at": 1},
            ).to_list(length=100000)
            review_by_id = {
                str(record["_id"]): {
                    "review_status": record.get("review_status", "unreviewed"),
                    "reviewed_at": record.get("reviewed_at"),
                }
                for record in previous
            }
        await self._db.extraction_records.delete_many({"job_id": job_id})
        if not records:
            return
        now = utc_now()
        documents = []
        for record in records:
            record_id = record.get("id") or f"rec_{uuid4().hex}"
            review = review_by_id.get(str(record_id), {})
            documents.append(
                {
                    "_id": record_id,
                    "job_id": job_id,
                    "record_type": record.get("record_type", "unknown"),
                    "source_pages": record.get("source_pages", []),
                    "fields": record.get("fields", {}),
                    "linkage": record.get("linkage", {}),
                    "link_hints": record.get("link_hints", {}),
                    "warnings": record.get("warnings", []),
                    "model_run_ids": record.get("model_run_ids", model_run_ids or []),
                    "region_ids": record.get("region_ids", []),
                    "relation_ids": record.get("relation_ids", []),
                    "associated_pages": record.get("associated_pages", []),
                    "thumbnail_region_id": record.get("thumbnail_region_id"),
                    "primary_number_region_id": record.get("primary_number_region_id"),
                    "primary_artifact_region_id": record.get("primary_artifact_region_id"),
                    "primary_relation_id": record.get("primary_relation_id"),
                    "primary_link_score": record.get("primary_link_score"),
                    "fusion_status": record.get("fusion_status", "unlinked"),
                    "entity_id": record.get("entity_id"),
                    "entity_confidence": record.get("entity_confidence"),
                    "entity_match_status": record.get(
                        "entity_match_status",
                        "unlinked",
                    ),
                    "review_status": review.get("review_status", "unreviewed"),
                    "reviewed_at": review.get("reviewed_at"),
                    "created_at": now,
                }
            )
        await self._db.extraction_records.insert_many(documents)

    async def replace_job_entities(
        self,
        *,
        job_id: str,
        document_id: str,
        entities: list[dict[str, Any]],
    ) -> None:
        await self._db.artifact_entities.delete_many({"job_id": job_id})
        if not entities:
            return
        now = utc_now()
        documents = [
            {
                "_id": entity["id"],
                **{key: value for key, value in entity.items() if key != "id"},
                "job_id": job_id,
                "document_id": document_id,
                "created_at": now,
                "updated_at": now,
            }
            for entity in entities
        ]
        await self._db.artifact_entities.insert_many(documents)

    async def get_entity(self, job_id: str, entity_id: str) -> dict[str, Any] | None:
        return await self._db.artifact_entities.find_one({"_id": entity_id, "job_id": job_id})

    async def replace_job_regions(self, job_id: str, regions: list[dict[str, Any]]) -> None:
        self._reference_index_page_cache.pop(job_id, None)
        await self._db.source_regions.delete_many({"job_id": job_id})
        if not regions:
            return
        now = utc_now()
        documents = [
            {
                "_id": region["id"],
                **{key: value for key, value in region.items() if key != "id"},
                "job_id": job_id,
                "created_at": now,
            }
            for region in regions
        ]
        await self._db.source_regions.insert_many(documents)

    async def replace_inferred_color_plate_regions(
        self,
        job_id: str,
        regions: list[dict[str, Any]],
    ) -> None:
        self._reference_index_page_cache.pop(job_id, None)
        await self._db.source_regions.delete_many(
            {
                "job_id": job_id,
                "kind": "color_plate",
                "source": "ocr_identifier_inference",
            }
        )
        if not regions:
            return
        now = utc_now()
        documents = [
            {
                "_id": str(region.get("id") or region.get("_id")),
                **{
                    key: value
                    for key, value in region.items()
                    if key not in {"id", "_id", "job_id", "created_at", "updated_at"}
                },
                "job_id": job_id,
                "created_at": now,
            }
            for region in regions
        ]
        await self._db.source_regions.insert_many(documents)

    async def replace_job_relations(self, job_id: str, relations: list[dict[str, Any]]) -> None:
        await self._db.region_relations.delete_many({"job_id": job_id})
        if not relations:
            return
        now = utc_now()
        documents = [
            {
                "_id": relation["id"],
                **{key: value for key, value in relation.items() if key != "id"},
                "job_id": job_id,
                "created_at": now,
            }
            for relation in relations
        ]
        await self._db.region_relations.insert_many(documents)

    async def list_page_regions(self, job_id: str, page_no: int) -> list[dict[str, Any]]:
        if page_no in await self._reference_index_pages(job_id):
            return []
        cursor = self._db.source_regions.find({"job_id": job_id, "page": page_no}).sort(
            "created_at", 1
        )
        return await cursor.to_list(length=5000)

    async def get_region(self, job_id: str, region_id: str) -> dict[str, Any]:
        region = await self._db.source_regions.find_one({"_id": region_id, "job_id": job_id})
        if region is None:
            raise NotFoundError("检测区域不存在")
        return region

    async def list_page_relations(self, job_id: str, page_no: int) -> list[dict[str, Any]]:
        region_ids = [region["_id"] for region in await self.list_page_regions(job_id, page_no)]
        if not region_ids:
            return []
        cursor = self._db.region_relations.find(
            {
                "job_id": job_id,
                "$or": [
                    {"source_region_id": {"$in": region_ids}},
                    {"target_region_id": {"$in": region_ids}},
                ],
            }
        ).sort("created_at", 1)
        return await cursor.to_list(length=5000)

    async def update_relation_review(
        self,
        *,
        job_id: str,
        relation_id: str,
        status: str,
        reason: str,
        reviewer: str | None,
    ) -> dict[str, Any]:
        relation = await self._db.region_relations.find_one({"_id": relation_id, "job_id": job_id})
        if relation is None:
            raise NotFoundError("Region relation does not exist")

        now = utc_now()
        reviewed_at = None if status == "unreviewed" else now
        updated = await self._db.region_relations.find_one_and_update(
            {"_id": relation_id, "job_id": job_id},
            {
                "$set": {
                    "review_status": status,
                    "reviewed_at": reviewed_at,
                    "reviewer": reviewer,
                    "review_reason": reason,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise NotFoundError("Region relation does not exist")

        await self._db.relation_revisions.insert_one(
            {
                "_id": f"relrev_{uuid4().hex}",
                "job_id": job_id,
                "relation_id": relation_id,
                "action": "review",
                "before": {
                    "review_status": relation.get("review_status", "unreviewed"),
                    "reviewer": relation.get("reviewer"),
                    "review_reason": relation.get("review_reason", ""),
                },
                "after": {
                    "review_status": status,
                    "reviewer": reviewer,
                    "review_reason": reason,
                },
                "reason": reason,
                "reviewer": reviewer,
                "created_at": now,
            }
        )
        return updated

    async def rebind_relation(
        self,
        *,
        job_id: str,
        relation_id: str,
        source_region_id: str,
        target_region_id: str,
        relation_type: str | None,
        reason: str,
        reviewer: str | None,
    ) -> dict[str, Any]:
        relation = await self._db.region_relations.find_one({"_id": relation_id, "job_id": job_id})
        if relation is None:
            raise NotFoundError("Region relation does not exist")

        region_count = await self._db.source_regions.count_documents(
            {
                "job_id": job_id,
                "_id": {"$in": [source_region_id, target_region_id]},
            }
        )
        if region_count != 2:
            raise NotFoundError("One or more source regions do not exist")

        now = utc_now()
        new_relation_id = f"rel_{uuid4().hex}"
        new_relation = {
            "_id": new_relation_id,
            "job_id": job_id,
            "source_region_id": source_region_id,
            "target_region_id": target_region_id,
            "relation_type": relation_type or relation.get("relation_type", "related_to"),
            "score": None,
            "method": "manual_rebind",
            "version": "manual-1",
            "model_run_id": None,
            "review_status": "accepted",
            "reviewed_at": now,
            "reviewer": reviewer,
            "review_reason": reason,
            "supersedes_relation_id": relation_id,
            "created_at": now,
        }
        await self._db.region_relations.insert_one(new_relation)
        await self._db.region_relations.update_one(
            {"_id": relation_id, "job_id": job_id},
            {
                "$set": {
                    "review_status": "rejected",
                    "reviewed_at": now,
                    "reviewer": reviewer,
                    "review_reason": reason,
                    "superseded_by_relation_id": new_relation_id,
                }
            },
        )
        await self._db.extraction_records.update_many(
            {"job_id": job_id, "relation_ids": relation_id},
            [
                {
                    "$set": {
                        "relation_ids": {
                            "$setUnion": [
                                {
                                    "$filter": {
                                        "input": "$relation_ids",
                                        "as": "relation_id",
                                        "cond": {"$ne": ["$$relation_id", relation_id]},
                                    }
                                },
                                [new_relation_id],
                            ]
                        }
                    }
                }
            ],
        )
        await self._db.relation_revisions.insert_one(
            {
                "_id": f"relrev_{uuid4().hex}",
                "job_id": job_id,
                "relation_id": relation_id,
                "action": "rebind",
                "before": {
                    "relation_id": relation_id,
                    "source_region_id": relation["source_region_id"],
                    "target_region_id": relation["target_region_id"],
                    "relation_type": relation.get("relation_type", "related_to"),
                },
                "after": {
                    "relation_id": new_relation_id,
                    "source_region_id": source_region_id,
                    "target_region_id": target_region_id,
                    "relation_type": new_relation["relation_type"],
                },
                "reason": reason,
                "reviewer": reviewer,
                "created_at": now,
            }
        )
        return new_relation

    async def list_relation_revisions(
        self,
        *,
        job_id: str,
        relation_id: str,
    ) -> list[dict[str, Any]]:
        cursor = self._db.relation_revisions.find(
            {"job_id": job_id, "relation_id": relation_id}
        ).sort("created_at", DESCENDING)
        return await cursor.to_list(length=1000)

    async def list_page_records(self, job_id: str, page_no: int) -> list[dict[str, Any]]:
        if page_no in await self._reference_index_pages(job_id):
            return []
        cursor = self._db.extraction_records.find({"job_id": job_id, "source_pages": page_no}).sort(
            "created_at", 1
        )
        return await cursor.to_list(length=5000)

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

    async def _reference_index_pages(self, job_id: str) -> list[int]:
        cached = self._reference_index_page_cache.get(job_id)
        if cached is not None:
            return cached

        job = await self._db.extraction_jobs.find_one(
            {"_id": job_id},
            {"reference_index_pages": 1},
        )
        configured_pages = job.get("reference_index_pages") if job else None
        if isinstance(configured_pages, list):
            pages = sorted(
                int(page) for page in configured_pages if isinstance(page, int)
            )
        else:
            cursor = self._db.source_regions.find(
                {"job_id": job_id},
                {
                    "_id": 0,
                    "page": 1,
                    "kind": 1,
                    "bbox": 1,
                    "text": 1,
                    "ocr_raw_text": 1,
                },
            )
            regions = await cursor.to_list(length=200000)
            pages = sorted(PageSemantics.reference_index_pages_from_regions(regions))

        self._reference_index_page_cache[job_id] = pages
        return pages

    async def list_records(
        self,
        job_id: str,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {"job_id": job_id}
        reference_index_pages = await self._reference_index_pages(job_id)
        if reference_index_pages:
            query["source_pages"] = {"$nin": reference_index_pages}
        total = await self._db.extraction_records.count_documents(query)
        cursor = (
            self._db.extraction_records.find(query)
            .sort([("source_pages", 1), ("created_at", 1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return await cursor.to_list(length=page_size), total

    async def get_record(self, job_id: str, record_id: str) -> dict[str, Any]:
        record = await self._db.extraction_records.find_one({"_id": record_id, "job_id": job_id})
        if record is None:
            raise NotFoundError("Extraction record does not exist")
        return record

    async def list_records_by_ids(
        self,
        job_id: str,
        record_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not record_ids:
            return []
        cursor = self._db.extraction_records.find(
            {"job_id": job_id, "_id": {"$in": record_ids}}
        )
        records = await cursor.to_list(length=len(record_ids))
        by_id = {str(record["_id"]): record for record in records}
        return [by_id[record_id] for record_id in record_ids if record_id in by_id]

    async def list_regions_by_ids(
        self,
        job_id: str,
        region_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not region_ids:
            return []
        cursor = self._db.source_regions.find({"job_id": job_id, "_id": {"$in": region_ids}}).sort(
            [("page", 1), ("created_at", 1)]
        )
        return await cursor.to_list(length=5000)

    async def list_relations_by_ids(
        self,
        job_id: str,
        relation_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not relation_ids:
            return []
        cursor = self._db.region_relations.find(
            {"job_id": job_id, "_id": {"$in": relation_ids}}
        ).sort("created_at", 1)
        return await cursor.to_list(length=5000)

    async def list_job_regions(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.source_regions.find({"job_id": job_id}).sort(
            [("page", 1), ("created_at", 1)]
        )
        return await cursor.to_list(length=100000)

    async def list_job_relations(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.region_relations.find({"job_id": job_id}).sort("created_at", 1)
        return await cursor.to_list(length=200000)

    async def list_job_records(self, job_id: str) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"job_id": job_id}
        reference_index_pages = await self._reference_index_pages(job_id)
        if reference_index_pages:
            query["source_pages"] = {"$nin": reference_index_pages}
        cursor = self._db.extraction_records.find(query).sort(
            [("source_pages", 1), ("created_at", 1)]
        )
        return await cursor.to_list(length=100000)

    async def list_job_entities(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.artifact_entities.find({"job_id": job_id}).sort("created_at", 1)
        return await cursor.to_list(length=100000)

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

    async def mark_stale_active_extraction_jobs(self) -> int:
        """Finalize extraction jobs that cannot continue after process restart.

        completed_at is frozen to the previous updated_at (last progress) so elapsed
        time does not stretch from attempt start until the next server boot.
        """

        now = utc_now()
        result = await self._db.extraction_jobs.update_many(
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
                        "cancelling",
                    ],
                }
            },
            [
                {
                    "$set": {
                        "completed_at": {"$ifNull": ["$completed_at", "$updated_at"]},
                    }
                },
                {
                    "$set": {
                        "status": "failed",
                        "stage": "failed",
                        "cancel_requested": True,
                        "error": "抽取任务在服务重启后中断，请重新启动抽取",
                        "updated_at": now,
                    }
                },
            ],
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
    def _relation_key(relation: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(relation.get("source_region_id", "")),
            str(relation.get("target_region_id", "")),
            str(relation.get("relation_type", "related_to")),
        )

    @staticmethod
    def _record_relation_signature(record: dict[str, Any]) -> str:
        marker = "|".join(sorted(str(value) for value in record.get("relation_ids", [])))
        return hashlib.sha256(marker.encode()).hexdigest()[:24] if marker else ""

    @staticmethod
    def _verification_protects_chain(verdict: str, failure_code: str | None) -> bool:
        relation_failure_codes = {
            "text_evidence_error",
            "caption_match_error",
            "number_match_error",
            "artifact_crop_error",
            "color_plate_error",
        }
        return verdict == "passed" or (
            verdict == "failed" and failure_code not in relation_failure_codes
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

    async def update_record_review(
        self,
        job_id: str,
        record_id: str,
        status: str,
    ) -> dict[str, Any]:
        now = utc_now()
        record = await self._db.extraction_records.find_one_and_update(
            {"_id": record_id, "job_id": job_id},
            {
                "$set": {
                    "review_status": status,
                    "reviewed_at": None if status == "unreviewed" else now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if record is None:
            raise NotFoundError("Extraction record does not exist")
        return record

    async def patch_record_paragraph_enrichment(
        self,
        *,
        job_id: str,
        record_id: str,
        fields: dict[str, Any],
        text_evidence: list[dict[str, Any]],
        region_ids: list[str],
        enrichment_version: str,
    ) -> None:
        """Persist OCR-paragraph card upgrades without rewriting the whole record."""

        now = utc_now()
        result = await self._db.extraction_records.update_one(
            {"_id": record_id, "job_id": job_id},
            {
                "$set": {
                    "fields": fields,
                    "text_evidence": text_evidence,
                    "region_ids": region_ids,
                    "paragraph_enrichment_version": enrichment_version,
                    "updated_at": now,
                }
            },
        )
        if result.matched_count == 0:
            raise NotFoundError("Extraction record does not exist")

    async def update_field_review(
        self,
        *,
        job_id: str,
        record_id: str,
        field_key: str,
        decision: str,
        value: Any,
        reason: str,
        reviewer: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        record = await self._db.extraction_records.find_one({"_id": record_id, "job_id": job_id})
        if record is None:
            raise NotFoundError("Extraction record does not exist")
        field = record.get("fields", {}).get(field_key)
        if field is None:
            raise NotFoundError("Extraction field does not exist")

        before = field.get("value")
        after = value if decision == "corrected" else before
        if decision == "rejected":
            next_status = "needs_review"
        elif decision == "corrected":
            next_status = "missing" if after is None else "valid"
        else:
            next_status = field.get("status", "valid")
        now = utc_now()
        revision = {
            "_id": f"rev_{uuid4().hex}",
            "job_id": job_id,
            "record_id": record_id,
            "field_key": field_key,
            "decision": decision,
            "before": before,
            "after": after,
            "reason": reason,
            "reviewer": reviewer,
            "created_at": now,
        }
        await self._db.record_revisions.insert_one(revision)
        updated = await self._db.extraction_records.find_one_and_update(
            {"_id": record_id, "job_id": job_id},
            {
                "$set": {
                    f"fields.{field_key}.value": after,
                    f"fields.{field_key}.status": next_status,
                    f"fields.{field_key}.review_decision": decision,
                    f"fields.{field_key}.reviewed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise NotFoundError("Extraction record does not exist")
        return updated, revision

    async def list_record_revisions(
        self,
        *,
        job_id: str,
        record_id: str,
    ) -> list[dict[str, Any]]:
        cursor = self._db.record_revisions.find({"job_id": job_id, "record_id": record_id}).sort(
            "created_at", DESCENDING
        )
        return await cursor.to_list(length=1000)

    async def get_or_create_verification_cohort(
        self,
        *,
        job_id: str,
        sample_size: int,
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = await self._db.verification_cohorts.find_one({"job_id": job_id})
        if existing is not None:
            return existing

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
        selected, strata_by_record, eligible_count = select_stratified_verification_sample(
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
            "sample_size": len(selected),
            "random_seed": seed,
            "record_ids": selected,
            "sampling_strategy": "stratified_v1",
            "eligible_count": eligible_count,
            "strata_by_record": strata_by_record,
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

        active = await self._db.verification_sessions.find_one(
            {"job_id": job_id, "status": {"$in": ["in_progress", "ai_review", "conflict_review"]}}
        )
        if active is not None:
            raise ConflictError("当前任务已有正在进行的校验，请先完成该校验")

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
                "sampling_strata": list(
                    strata_by_record.get(str(record_id), {}).get("rule_states", [])
                ),
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
        await self._db.verification_sessions.insert_one(session)
        return session

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
        for item in session.get("items", []):
            result = results.get(str(item.get("record_id")))
            items.append({**item, **result} if result else item)
        now = utc_now()
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
