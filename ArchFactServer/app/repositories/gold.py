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

class GoldPersistence:
    """Human annotation datasets used for algorithm evaluation."""

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
        await self._replace_scoped_documents(
            self._db.gold_records,
            scope={"dataset_id": dataset_id},
            documents=records,
        )
        await self._replace_scoped_documents(
            self._db.gold_regions,
            scope={"dataset_id": dataset_id},
            documents=regions,
        )
        await self._replace_scoped_documents(
            self._db.gold_assets,
            scope={"dataset_id": dataset_id},
            documents=assets,
        )
        await self._replace_scoped_documents(
            self._db.gold_links,
            scope={"dataset_id": dataset_id},
            documents=links,
        )


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

