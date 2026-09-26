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

class PersistenceOps:
    """Shared Mongo helpers used by every bounded-context mixin."""

    def __init__(self, database: MongoDatabase) -> None:
        self._db = database.database
        self._reference_index_page_cache: dict[str, list[int]] = {}


    async def _replace_job_documents(
        self,
        collection: Any,
        *,
        job_id: str,
        documents: list[dict[str, Any]],
    ) -> None:
        await self._replace_scoped_documents(
            collection,
            scope={"job_id": job_id},
            documents=documents,
        )


    async def _replace_scoped_documents(
        self,
        collection: Any,
        *,
        scope: dict[str, Any],
        documents: list[dict[str, Any]],
    ) -> None:
        """Upsert the new documents first, then prune leftovers in the same scope."""

        keep_ids = [document["_id"] for document in documents]
        if documents:
            await collection.bulk_write(
                [
                    ReplaceOne({"_id": document["_id"]}, document, upsert=True)
                    for document in documents
                ],
                ordered=False,
            )
        delete_query: dict[str, Any] = dict(scope)
        if keep_ids:
            delete_query["_id"] = {"$nin": keep_ids}
        await collection.delete_many(delete_query)


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
    def _relation_key(relation: dict[str, Any]) -> tuple[str, str, str]:
        return relation_key(relation)

