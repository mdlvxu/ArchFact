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

class OutputPersistence:
    """Records, regions, relations, entities, and review revisions."""

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
        now = utc_now()
        documents = []
        for record in records:
            record_id = record.get("id") or record.get("_id") or f"rec_{uuid4().hex}"
            review = review_by_id.get(str(record_id), {})
            documents.append(
                {
                    "_id": record_id,
                    "job_id": job_id,
                    "record_type": record.get("record_type", "unknown"),
                    "source_pages": record.get("source_pages", []),
                    "fields": record.get("fields", {}),
                    "text_evidence": list(record.get("text_evidence") or []),
                    "paragraph_enrichment_version": record.get(
                        "paragraph_enrichment_version"
                    ),
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
                    "review_status": review.get(
                        "review_status",
                        record.get("review_status", "unreviewed"),
                    ),
                    "reviewed_at": review.get("reviewed_at", record.get("reviewed_at")),
                    "created_at": record.get("created_at") or now,
                    "updated_at": now,
                }
            )
        await self._replace_job_documents(
            self._db.extraction_records,
            job_id=job_id,
            documents=documents,
        )


    async def replace_job_entities(
        self,
        *,
        job_id: str,
        document_id: str,
        entities: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        documents = [
            {
                "_id": entity["id"],
                **{key: value for key, value in entity.items() if key != "id"},
                "job_id": job_id,
                "document_id": document_id,
                "created_at": entity.get("created_at") or now,
                "updated_at": now,
            }
            for entity in entities
        ]
        await self._replace_job_documents(
            self._db.artifact_entities,
            job_id=job_id,
            documents=documents,
        )


    async def get_entity(self, job_id: str, entity_id: str) -> dict[str, Any] | None:
        return await self._db.artifact_entities.find_one({"_id": entity_id, "job_id": job_id})


    async def replace_job_regions(self, job_id: str, regions: list[dict[str, Any]]) -> None:
        self._reference_index_page_cache.pop(job_id, None)
        now = utc_now()
        documents = [
            {
                "_id": region["id"],
                **{key: value for key, value in region.items() if key not in {"id", "_id"}},
                "job_id": job_id,
                "created_at": region.get("created_at") or now,
            }
            for region in regions
        ]
        await self._replace_job_documents(
            self._db.source_regions,
            job_id=job_id,
            documents=documents,
        )


    async def replace_inferred_color_plate_regions(
        self,
        job_id: str,
        regions: list[dict[str, Any]],
    ) -> None:
        self._reference_index_page_cache.pop(job_id, None)
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
        await self._replace_scoped_documents(
            self._db.source_regions,
            scope={
                "job_id": job_id,
                "kind": "color_plate",
                "source": "ocr_identifier_inference",
            },
            documents=documents,
        )


    async def replace_job_relations(self, job_id: str, relations: list[dict[str, Any]]) -> None:
        now = utc_now()
        documents = [
            {
                "_id": relation["id"],
                **{key: value for key, value in relation.items() if key not in {"id", "_id"}},
                "job_id": job_id,
                "created_at": relation.get("created_at") or now,
            }
            for relation in relations
        ]
        await self._replace_job_documents(
            self._db.region_relations,
            job_id=job_id,
            documents=documents,
        )


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

