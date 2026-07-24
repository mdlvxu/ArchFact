from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel, MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database

from app.core.config import Settings


class MongoDatabase:
    """Owns MongoDB clients and collection indexes.

    The async client is used by FastAPI services. GridFS currently uses a synchronous
    client behind ``asyncio.to_thread`` so large file operations never block the event loop.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.async_client = AsyncMongoClient(settings.mongodb_uri)
        self.sync_client = MongoClient(settings.mongodb_uri)
        self.database: AsyncDatabase = self.async_client[settings.mongodb_database]
        self.sync_database: Database = self.sync_client[settings.mongodb_database]

    async def connect(self) -> None:
        await self.async_client.admin.command("ping")
        await self._create_indexes()

    async def _create_indexes(self) -> None:
        await self._drop_legacy_document_image_index()
        await self.database.documents.create_indexes(
            [
                IndexModel([("sha256", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)]),
            ]
        )
        await self.database.document_pages.create_indexes(
            [
                IndexModel(
                    [("document_id", ASCENDING), ("page_no", ASCENDING)],
                    unique=True,
                )
            ]
        )
        await self.database.document_page_index.create_indexes(
            [
                IndexModel(
                    [
                        ("document_id", ASCENDING),
                        ("index_version", ASCENDING),
                        ("page_no", ASCENDING),
                    ],
                    unique=True,
                ),
                IndexModel(
                    [
                        ("document_id", ASCENDING),
                        ("references", ASCENDING),
                    ]
                ),
                IndexModel(
                    [
                        ("document_id", ASCENDING),
                        ("page_type", ASCENDING),
                        ("visual_score", DESCENDING),
                    ]
                ),
            ]
        )
        await self.database.document_text_chunks.create_indexes(
            [
                IndexModel(
                    [("job_id", ASCENDING), ("ordinal", ASCENDING)],
                    unique=True,
                ),
                IndexModel(
                    [("document_id", ASCENDING), ("artifact_ids", ASCENDING)]
                ),
                IndexModel(
                    [("document_id", ASCENDING), ("reference_tokens", ASCENDING)]
                ),
                IndexModel([("job_id", ASCENDING), ("source_pages", ASCENDING)]),
            ]
        )
        await self.database.semantic_extraction_cache.create_indexes(
            [
                IndexModel([("cache_key", ASCENDING)], unique=True),
                IndexModel(
                    [
                        ("document_id", ASCENDING),
                        ("page_no", ASCENDING),
                        ("updated_at", DESCENDING),
                    ]
                ),
            ]
        )
        await self.database.extraction_jobs.create_indexes(
            [
                IndexModel([("document_id", ASCENDING), ("created_at", DESCENDING)]),
                IndexModel([("status", ASCENDING), ("created_at", ASCENDING)]),
                IndexModel(
                    [("idempotency_key", ASCENDING)],
                    unique=True,
                    sparse=True,
                ),
            ]
        )
        await self.database.job_page_runs.create_indexes(
            [
                IndexModel(
                    [("job_id", ASCENDING), ("page_no", ASCENDING)],
                    unique=True,
                ),
                IndexModel(
                    [
                        ("document_id", ASCENDING),
                        ("status", ASCENDING),
                        ("page_no", ASCENDING),
                    ]
                ),
            ]
        )
        await self.database.extraction_records.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("source_pages", ASCENDING)]),
                IndexModel([("job_id", ASCENDING), ("entity_id", ASCENDING)]),
            ]
        )
        await self.database.artifact_entities.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("associated_pages", ASCENDING)]),
                IndexModel(
                    [
                        ("document_id", ASCENDING),
                        ("canonical_artifact_id", ASCENDING),
                    ]
                ),
                IndexModel([("job_id", ASCENDING), ("match_keys", ASCENDING)]),
            ]
        )
        await self.database.source_regions.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("page", ASCENDING)]),
                IndexModel([("document_id", ASCENDING), ("page", ASCENDING)]),
            ]
        )
        await self.database.region_relations.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("source_region_id", ASCENDING)]),
                IndexModel([("job_id", ASCENDING), ("target_region_id", ASCENDING)]),
            ]
        )
        await self.database.model_runs.create_indexes(
            [IndexModel([("job_id", ASCENDING), ("started_at", ASCENDING)])]
        )
        await self.database.record_revisions.create_indexes(
            [
                IndexModel(
                    [("job_id", ASCENDING), ("record_id", ASCENDING), ("created_at", DESCENDING)]
                )
            ]
        )
        await self.database.relation_revisions.create_indexes(
            [
                IndexModel(
                    [("job_id", ASCENDING), ("relation_id", ASCENDING), ("created_at", DESCENDING)]
                )
            ]
        )
        await self.database.job_events.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("created_at", ASCENDING)]),
            ]
        )
        await self.database.post_processing_rules.create_indexes(
            [IndexModel([("key", ASCENDING)], unique=True)]
        )
        await self.database.document_images.create_indexes(
            [
                IndexModel(
                    [("document_id", ASCENDING), ("page_no", ASCENDING), ("image_type", ASCENDING)],
                    unique=True,
                    name="unique_page_render_per_page",
                    partialFilterExpression={"image_type": "page_render"},
                ),
                IndexModel([("created_at", DESCENDING)]),
            ]
        )
        await self.database.verification_cohorts.create_indexes(
            [IndexModel([("job_id", ASCENDING)], unique=True)]
        )
        await self.database.verification_sessions.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("created_at", DESCENDING)]),
                IndexModel([("job_id", ASCENDING), ("status", ASCENDING)]),
            ]
        )
        await self.database.verification_versions.create_indexes(
            [
                IndexModel(
                    [("job_id", ASCENDING), ("version", ASCENDING)],
                    unique=True,
                )
            ]
        )
        await self.database.ai_verification_runs.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("created_at", DESCENDING)]),
                IndexModel([("session_id", ASCENDING), ("status", ASCENDING)]),
            ]
        )
        await self.database.gold_datasets.create_indexes(
            [
                IndexModel(
                    [("document_id", ASCENDING), ("version", ASCENDING)],
                    unique=True,
                ),
                IndexModel([("status", ASCENDING), ("created_at", DESCENDING)]),
            ]
        )
        await self.database.gold_records.create_indexes(
            [
                IndexModel([("dataset_id", ASCENDING), ("canonical_artifact_id", ASCENDING)]),
                IndexModel([("dataset_id", ASCENDING), ("source_row", ASCENDING)], unique=True),
            ]
        )
        await self.database.gold_regions.create_indexes(
            [IndexModel([("dataset_id", ASCENDING), ("page", ASCENDING), ("kind", ASCENDING)])]
        )
        await self.database.gold_assets.create_indexes(
            [
                IndexModel([("dataset_id", ASCENDING), ("asset_type", ASCENDING)]),
                IndexModel([("dataset_id", ASCENDING), ("reference_keys", ASCENDING)]),
            ]
        )
        await self.database.gold_links.create_indexes(
            [
                IndexModel([("dataset_id", ASCENDING), ("record_id", ASCENDING)]),
                IndexModel([("dataset_id", ASCENDING), ("asset_id", ASCENDING)]),
            ]
        )
        await self.database.quality_evaluation_runs.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("created_at", DESCENDING)]),
                IndexModel([("job_id", ASCENDING), ("status", ASCENDING)]),
            ]
        )
        await self.database.quality_evaluation_items.create_indexes(
            [
                IndexModel(
                    [("evaluation_id", ASCENDING), ("record_id", ASCENDING)],
                    unique=True,
                ),
                IndexModel([("job_id", ASCENDING), ("match_status", ASCENDING)]),
            ]
        )
        await self.database.rematch_runs.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("created_at", DESCENDING)]),
                IndexModel([("job_id", ASCENDING), ("status", ASCENDING)]),
            ]
        )
        for collection in (
            self.database.rematch_relations,
            self.database.rematch_records,
            self.database.rematch_entities,
        ):
            await collection.create_indexes(
                [
                    IndexModel(
                        [
                            ("rematch_id", ASCENDING),
                            ("snapshot_kind", ASCENDING),
                            ("position", ASCENDING),
                        ],
                        unique=True,
                    ),
                    IndexModel([("job_id", ASCENDING), ("created_at", DESCENDING)]),
                ]
            )

    async def _drop_legacy_document_image_index(self) -> None:
        indexes = await self.database.document_images.index_information()
        legacy_key = [("document_id", 1), ("page_no", 1), ("image_type", 1)]
        for name, definition in indexes.items():
            if (
                definition.get("key") == legacy_key
                and definition.get("unique")
                and not definition.get("partialFilterExpression")
            ):
                await self.database.document_images.drop_index(name)

    async def close(self) -> None:
        await self.async_client.close()
        self.sync_client.close()
