import asyncio
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError, OperationFailure

from app.infrastructure.mongodb import MongoDatabase
from app.repositories.mongo_repository import MongoRepository
from app.services.document_service import DocumentService


class _Collection:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.bulk_ops: list[object] = []
        self.delete_query: dict | None = None
        self.inserted: dict | None = None

    async def bulk_write(self, ops, ordered=False):  # noqa: ANN001
        del ordered
        self.order.append("bulk_write")
        self.bulk_ops = list(ops)

    async def delete_many(self, query):  # noqa: ANN001
        self.order.append("delete_many")
        self.delete_query = query

    async def insert_one(self, document):  # noqa: ANN001
        self.inserted = document


class _UploadFile:
    filename = "wenjiashan.pdf"
    content_type = "application/pdf"


def test_upload_reuses_existing_document_by_sha256() -> None:
    storage = SimpleNamespace(
        digest_calls=0,
        store_calls=0,
    )

    async def digest_pdf(_file):  # noqa: ANN001
        storage.digest_calls += 1
        return "abc123", 2048

    async def store_pdf(_file, *, sha256):  # noqa: ANN001
        del sha256
        storage.store_calls += 1
        raise AssertionError("identical PDF must not be written to GridFS again")

    async def get_document_by_sha256(sha256):  # noqa: ANN001
        return {"_id": "doc_existing", "sha256": sha256, "filename": "wenjiashan.pdf"}

    service = DocumentService(
        SimpleNamespace(get_document_by_sha256=get_document_by_sha256),  # type: ignore[arg-type]
        SimpleNamespace(digest_pdf=digest_pdf, store_pdf=store_pdf),  # type: ignore[arg-type]
    )

    document = asyncio.run(service.upload(_UploadFile()))  # type: ignore[arg-type]

    assert document["_id"] == "doc_existing"
    assert storage.digest_calls == 1
    assert storage.store_calls == 0


def test_upload_deletes_orphan_gridfs_file_on_duplicate_sha256() -> None:
    storage = SimpleNamespace(deleted=[])

    async def digest_pdf(_file):  # noqa: ANN001
        return "abc123", 2048

    async def store_pdf(_file, *, sha256):  # noqa: ANN001
        del sha256
        return "gridfs_new"

    async def delete(file_id):  # noqa: ANN001
        storage.deleted.append(file_id)

    lookups = {"count": 0}

    async def get_document_by_sha256(sha256):  # noqa: ANN001
        lookups["count"] += 1
        if lookups["count"] == 1:
            return None
        return {"_id": "doc_winner", "sha256": sha256}

    async def create_document(**_: object):
        raise DuplicateKeyError("sha256")

    service = DocumentService(
        SimpleNamespace(
            get_document_by_sha256=get_document_by_sha256,
            create_document=create_document,
        ),  # type: ignore[arg-type]
        SimpleNamespace(digest_pdf=digest_pdf, store_pdf=store_pdf, delete=delete),  # type: ignore[arg-type]
    )

    document = asyncio.run(service.upload(_UploadFile()))  # type: ignore[arg-type]

    assert document["_id"] == "doc_winner"
    assert storage.deleted == ["gridfs_new"]


def test_create_document_returns_existing_row_on_duplicate_sha256() -> None:
    class Documents:
        async def insert_one(self, _document):  # noqa: ANN001
            raise DuplicateKeyError("sha256")

        async def find_one(self, query, sort=None):  # noqa: ANN001
            del sort
            return {"_id": "doc_existing", "sha256": query["sha256"]}

    repository = MongoRepository.__new__(MongoRepository)
    repository._db = SimpleNamespace(documents=Documents())  # type: ignore[attr-defined]

    document = asyncio.run(
        repository.create_document(
            filename="a.pdf",
            content_type="application/pdf",
            size=12,
            sha256="abc123",
            gridfs_id="file_1",
        )
    )

    assert document["_id"] == "doc_existing"


def test_replace_document_page_index_upserts_before_deleting_stale_rows() -> None:
    collection = _Collection()
    repository = MongoRepository.__new__(MongoRepository)
    repository._db = SimpleNamespace(document_page_index=collection)  # type: ignore[attr-defined]

    asyncio.run(
        repository.replace_document_page_index(
            document_id="doc_1",
            index_version="v1",
            pages=[{"page_no": 3, "page_type": "catalog"}],
        )
    )

    assert collection.order == ["bulk_write", "delete_many"]
    operation = collection.bulk_ops[0]
    assert operation._filter == {"_id": "pageidx_doc_1_v1_0003"}  # type: ignore[attr-defined]
    assert collection.delete_query == {
        "document_id": "doc_1",
        "index_version": "v1",
        "_id": {"$nin": ["pageidx_doc_1_v1_0003"]},
    }


def test_replace_gold_dataset_upserts_children_before_pruning() -> None:
    records = _Collection()
    regions = _Collection()
    assets = _Collection()
    links = _Collection()

    class Datasets:
        async def replace_one(self, *_: object, **__: object) -> None:
            return None

    repository = MongoRepository.__new__(MongoRepository)
    repository._db = SimpleNamespace(  # type: ignore[attr-defined]
        gold_datasets=Datasets(),
        gold_records=records,
        gold_regions=regions,
        gold_assets=assets,
        gold_links=links,
    )

    asyncio.run(
        repository.replace_gold_dataset(
            dataset={"_id": "gold_1"},
            records=[{"_id": "goldrec_1", "dataset_id": "gold_1"}],
            regions=[],
            assets=[],
            links=[],
        )
    )

    assert records.order == ["bulk_write", "delete_many"]
    assert records.delete_query == {"dataset_id": "gold_1", "_id": {"$nin": ["goldrec_1"]}}
    assert regions.order == ["delete_many"]
    assert regions.delete_query == {"dataset_id": "gold_1"}


def test_replace_quality_evaluation_items_uses_stable_ids() -> None:
    collection = _Collection()
    repository = MongoRepository.__new__(MongoRepository)
    repository._db = SimpleNamespace(quality_evaluation_items=collection)  # type: ignore[attr-defined]

    asyncio.run(
        repository.replace_quality_evaluation_items(
            run_id="eval_1",
            job_id="job_1",
            items=[{"record_id": "rec_9", "match_status": "matched"}],
        )
    )

    operation = collection.bulk_ops[0]
    assert operation._filter == {"_id": "qualityitem_eval_1_rec_9"}  # type: ignore[attr-defined]
    assert collection.delete_query == {
        "evaluation_id": "eval_1",
        "_id": {"$nin": ["qualityitem_eval_1_rec_9"]},
    }


def test_unique_sha256_index_is_left_alone_when_already_unique() -> None:
    class Documents:
        async def index_information(self):
            return {"unique_document_sha256": {"key": [("sha256", 1)], "unique": True}}

        async def create_index(self, *_: object, **__: object):
            raise AssertionError("existing unique sha256 index must not be recreated")

    mongo = MongoDatabase.__new__(MongoDatabase)
    mongo.database = SimpleNamespace(documents=Documents())  # type: ignore[attr-defined]

    asyncio.run(mongo._ensure_unique_document_sha256_index())


def test_unique_sha256_index_falls_back_when_duplicate_hashes_exist() -> None:
    class Documents:
        def __init__(self) -> None:
            self.dropped: list[str] = []
            self.created: list[dict] = []

        async def index_information(self):
            return {"sha256_1": {"key": [("sha256", 1)]}}

        async def drop_index(self, name):  # noqa: ANN001
            self.dropped.append(name)

        async def create_index(self, keys, **kwargs):  # noqa: ANN001
            if kwargs.get("unique"):
                raise OperationFailure("E11000 duplicate key error", 11000)
            self.created.append({"keys": keys, **kwargs})

    documents = Documents()
    mongo = MongoDatabase.__new__(MongoDatabase)
    mongo.database = SimpleNamespace(documents=documents)  # type: ignore[attr-defined]

    asyncio.run(mongo._ensure_unique_document_sha256_index())

    assert documents.dropped == ["sha256_1"]
    assert documents.created[0]["name"] == "sha256_1"
    assert "unique" not in documents.created[0]
