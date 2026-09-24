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

class ConfigPersistence:
    """Extraction templates and post-processing rules."""

    async def get_extraction_system_prompt(self) -> dict[str, Any] | None:
        return await self._db.extraction_system_prompts.find_one({"_id": "default"})

    async def replace_extraction_system_prompt(self, content: str) -> None:
        now = utc_now()
        await self._db.extraction_system_prompts.update_one(
            {"_id": "default"},
            {
                "$set": {"content": content, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

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

