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

class DocumentPersistence:
    """PDF documents, pages, images, and text chunks."""

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
        try:
            await self._db.documents.insert_one(document)
        except DuplicateKeyError:
            existing = await self.get_document_by_sha256(sha256)
            if existing is None:
                raise
            return existing
        return document


    async def get_document_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        return await self._db.documents.find_one(
            {"sha256": sha256},
            sort=[("created_at", DESCENDING)],
        )


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


    async def list_document_pages(self, document_id: str) -> list[dict[str, Any]]:
        cursor = self._db.document_pages.find({"document_id": document_id}).sort("page_no", 1)
        return await cursor.to_list(length=10000)


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
        await self._replace_scoped_documents(
            self._db.document_page_index,
            scope={"document_id": document_id, "index_version": index_version},
            documents=documents,
        )


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
        await self._replace_scoped_documents(
            self._db.document_text_chunks,
            scope={"job_id": job_id},
            documents=documents,
        )


    async def list_document_text_chunks(self, job_id: str) -> list[dict[str, Any]]:
        cursor = self._db.document_text_chunks.find({"job_id": job_id}).sort(
            "ordinal", 1
        )
        return await cursor.to_list(length=100000)

