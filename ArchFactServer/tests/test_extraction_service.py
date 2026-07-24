import asyncio
import shutil
from pathlib import Path
from typing import Any

import fitz

from app.core.config import Settings
from app.infrastructure.local_image_storage import LocalImageStorage
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.services.artifact_entity_linker import ArtifactEntityLinker
from app.services.detection_engine import DisabledYoloDetectionEngine
from app.services.document_text_index import DocumentTextIndexer
from app.services.extraction_engine import LocalTextExtractionEngine
from app.services.extraction_service import ExtractionService
from app.services.ocr_engine import (
    DisabledOcrEngine,
    OcrEngine,
    OcrPageInput,
    OcrPageResult,
)
from app.services.page_discovery import PageDiscoveryService
from app.services.page_preprocessor import PagePreprocessor
from app.services.pdf_parser import PdfParser
from app.services.post_processor import PostProcessor
from app.services.region_processor import RegionProcessor
from app.services.relation_matcher import RelationMatcher, RelationMatcherConfig
from app.services.result_fusion import ResultFusionService


class FakeRepository:
    def __init__(self) -> None:
        self.job: dict[str, Any] = {
            "_id": "job_test",
            "document_id": "doc_test",
            "pages": [1],
            "config": {
                "schema_version": "1.0",
                "template_id": "basic",
                "template_name": "Basic",
                "fields": [
                    {
                        "key": "artifact_id",
                        "label": "器物编号",
                        "type": "string",
                        "required": False,
                    }
                ],
                "post_processing_rules": [],
            },
            "status": "queued",
            "cancel_requested": False,
        }
        self.document: dict[str, Any] = {
            "_id": "doc_test",
            "storage": {"file_id": "gridfs_test"},
        }
        self.records: list[dict[str, Any]] = []
        self.entities: list[dict[str, Any]] = []
        self.pages: list[dict[str, Any]] = []
        self.regions: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.model_runs: list[dict[str, Any]] = []
        self.page_index: list[dict[str, Any]] = []
        self.text_chunks: list[dict[str, Any]] = []
        self.images: list[dict[str, Any]] = []
        self.page_runs: dict[int, dict[str, Any]] = {}

    async def get_job(self, _: str) -> dict[str, Any]:
        return self.job

    async def get_document(self, _: str) -> dict[str, Any]:
        return self.document

    async def update_job(self, _: str, **fields: Any) -> None:
        self.job.update(fields)

    async def update_document(self, _: str, **fields: Any) -> None:
        self.document.update(fields)

    async def request_cancel(self, _: str) -> dict[str, Any]:
        self.job.update(
            cancel_requested=True,
            status="cancelling",
            stage="cancelling",
        )
        return self.job

    async def upsert_pages(self, _: str, pages: list[dict[str, Any]]) -> None:
        pages_by_number = {int(page["page_no"]): page for page in self.pages}
        pages_by_number.update({int(page["page_no"]): page for page in pages})
        self.pages = [pages_by_number[page_no] for page_no in sorted(pages_by_number)]

    async def list_document_pages(self, _: str) -> list[dict[str, Any]]:
        return self.pages

    async def list_document_images(self, _: str) -> list[dict[str, Any]]:
        return self.images

    async def list_document_page_index(
        self,
        *,
        document_id: str,
        index_version: str,
    ) -> list[dict[str, Any]]:
        del document_id, index_version
        return self.page_index

    async def replace_document_page_index(
        self,
        *,
        document_id: str,
        index_version: str,
        pages: list[dict[str, Any]],
    ) -> None:
        del document_id, index_version
        self.page_index = pages

    async def upsert_document_image(self, image: dict[str, Any]) -> dict[str, Any]:
        stored = {"_id": image["id"], **image}
        self.images = [
            existing
            for existing in self.images
            if not (
                existing.get("page_no") == image.get("page_no")
                and existing.get("image_type") == image.get("image_type")
            )
        ]
        self.images.append(stored)
        return stored

    async def replace_document_text_chunks(
        self,
        *,
        job_id: str,
        document_id: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        del job_id, document_id
        self.text_chunks = chunks

    async def upsert_job_page_run(
        self,
        *,
        job_id: str,
        document_id: str,
        page_no: int,
        **fields: Any,
    ) -> None:
        self.page_runs[page_no] = {
            **self.page_runs.get(page_no, {}),
            **fields,
            "job_id": job_id,
            "document_id": document_id,
            "page_no": page_no,
        }

    async def append_event(self, *_: Any) -> None:
        return None

    async def replace_job_records(
        self,
        _: str,
        records: list[dict[str, Any]],
        model_run_ids: list[str] | None = None,
        preserve_reviews: bool = False,
    ) -> None:
        del preserve_reviews
        self.records = records
        for record in self.records:
            record.setdefault("model_run_ids", model_run_ids or [])

    async def replace_job_entities(
        self,
        *,
        job_id: str,
        document_id: str,
        entities: list[dict[str, Any]],
    ) -> None:
        del job_id, document_id
        self.entities = entities

    async def replace_job_regions(self, _: str, regions: list[dict[str, Any]]) -> None:
        self.regions = regions

    async def replace_job_relations(self, _: str, relations: list[dict[str, Any]]) -> None:
        self.relations = relations

    async def list_job_regions(self, _: str) -> list[dict[str, Any]]:
        return self.regions

    async def list_job_relations(self, _: str) -> list[dict[str, Any]]:
        return self.relations

    async def list_job_records(self, _: str) -> list[dict[str, Any]]:
        return self.records

    async def list_job_page_runs(self, _: str) -> list[dict[str, Any]]:
        return list(self.page_runs.values())

    async def create_model_run(self, **fields: Any) -> dict[str, Any]:
        run = {"_id": f"run_{len(self.model_runs) + 1}", "status": "running", **fields}
        self.model_runs.append(run)
        return run

    async def finish_model_run(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        run = next(item for item in self.model_runs if item["_id"] == run_id)
        run.update(status=status, error=error)


class FakeStorage:
    def __init__(self, source: Path) -> None:
        self.source = source

    async def download_to_path(self, _: str, target: Path) -> None:
        shutil.copyfile(self.source, target)


class RecordingDispatcher(LocalJobDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self.dispatched: list[str] = []

    async def dispatch(self, job_id: str) -> None:
        self.dispatched.append(job_id)


def create_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "M12:3 gray pottery jar")
    document.save(path)
    document.close()


def build_service(
    *,
    tmp_path: Path,
    pdf_path: Path,
    repository: FakeRepository,
    engine: LocalTextExtractionEngine | None = None,
    ocr_engine: OcrEngine | None = None,
    dispatcher: LocalJobDispatcher | None = None,
) -> ExtractionService:
    settings = Settings(app_env="test", file_storage_root=tmp_path / "files")
    return ExtractionService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        storage=FakeStorage(pdf_path),  # type: ignore[arg-type]
        preprocessor=PagePreprocessor(
            settings=settings,
            parser=PdfParser(settings),
            repository=repository,  # type: ignore[arg-type]
            image_storage=LocalImageStorage(settings),
            ocr_engine=ocr_engine or DisabledOcrEngine(),
        ),
        engine=engine or LocalTextExtractionEngine(),
        detector=DisabledYoloDetectionEngine(),
        region_processor=RegionProcessor(
            settings=settings,
            image_storage=LocalImageStorage(settings),
            ocr_engine=ocr_engine or DisabledOcrEngine(),
        ),
        relation_matcher=RelationMatcher(RelationMatcherConfig.from_settings(settings)),
        result_fusion=ResultFusionService(),
        entity_linker=ArtifactEntityLinker(),
        document_text_indexer=DocumentTextIndexer(),
        page_discovery=PageDiscoveryService(
            settings,
            ocr_engine or DisabledOcrEngine(),
        ),
        post_processor=PostProcessor(),
        dispatcher=dispatcher or LocalJobDispatcher(),
    )


def test_job_orchestration_reaches_completed_result(tmp_path: Path) -> None:
    pdf_path = tmp_path / "report.pdf"
    create_pdf(pdf_path)
    repository = FakeRepository()
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
    )

    asyncio.run(service.run_job("job_test"))

    assert repository.job["status"] == "completed"
    assert repository.job["progress"]["percent"] == 100
    assert repository.document["status"] == "ready"
    assert repository.pages[0]["page_no"] == 1
    assert repository.pages[0]["image_object_key"].endswith("rendered/page.png")
    assert repository.pages[0]["blocks"][0]["region_id"].startswith("reg_")
    assert repository.text_chunks[0]["artifact_ids"] == ["M12:3"]
    assert repository.job["document_text_chunk_count"] == 1
    assert repository.regions[0]["kind"] == "text"
    assert [run["status"] for run in repository.model_runs] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    assert repository.records[0]["fields"]["page_text"]["value"].startswith("M12:3")
    assert repository.records[0]["fields"]["page_text"]["evidence"][0]["region_id"]
    assert repository.records[0]["entity_id"].startswith("ent_")
    assert repository.entities[0]["record_ids"] == [repository.records[0]["id"]]


def test_scanned_page_completes_with_ocr_warning(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    repository = FakeRepository()
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
    )

    asyncio.run(service.run_job("job_test"))

    assert repository.job["status"] == "completed_with_warnings"
    assert repository.job["succeeded_pages"] == 1
    assert repository.job["failed_pages"] == 0
    assert repository.pages[0]["needs_ocr"] is True
    assert repository.job["page_issues"][0]["stage"] == "ocr"
    assert repository.records == []


class FakeOcrEngine:
    enabled = True
    provider = "test-ocr"
    model = "fake-chinese-ocr"
    version = "1"
    config = {"adapter": "fake"}

    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, page: OcrPageInput) -> OcrPageResult:
        del page
        self.calls += 1
        return OcrPageResult(
            text="M12:7 灰陶罐",
            blocks=[
                {
                    "text": "M12:7 灰陶罐",
                    "bbox": [0.1, 0.1, 0.6, 0.2],
                    "bbox_px": [100, 100, 600, 200],
                    "confidence": 0.93,
                    "source": "test_ocr",
                }
            ],
        )


def test_scanned_page_uses_ocr_result_for_semantic_extraction(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan-with-text.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    repository = FakeRepository()
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
        ocr_engine=FakeOcrEngine(),
    )

    asyncio.run(service.run_job("job_test"))

    assert repository.job["status"] == "completed"
    assert repository.pages[0]["needs_ocr"] is False
    assert repository.pages[0]["parse_method"] == "ocr"
    assert repository.pages[0]["ocr_provider"] == "test-ocr"
    assert repository.regions[0]["source"] == "test_ocr"
    assert repository.regions[0]["confidence"] == 0.93
    assert repository.records[0]["fields"]["page_text"]["value"] == "M12:7 灰陶罐"
    assert [run["stage"] for run in repository.model_runs] == [
        "pdf_parse",
        "page_ocr",
        "semantic_extraction",
        "relation_matching",
        "result_fusion",
        "entity_linking",
    ]


def test_all_ocr_policy_keeps_pdf_text_and_uses_ocr_as_effective_text(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "text-layer.pdf"
    create_pdf(pdf_path)
    repository = FakeRepository()
    settings = Settings(
        app_env="test",
        file_storage_root=tmp_path / "files-all-ocr",
        ocr_policy="all",
    )
    preprocessor = PagePreprocessor(
        settings=settings,
        parser=PdfParser(settings),
        repository=repository,  # type: ignore[arg-type]
        image_storage=LocalImageStorage(settings),
        ocr_engine=FakeOcrEngine(),
    )

    prepared = asyncio.run(
        preprocessor.prepare(
            pdf_path=pdf_path,
            document_id="doc_test",
            selected_pages=[1],
        )
    )

    page = prepared.pages[0]
    assert page["pdf_text"].startswith("M12:3 gray pottery")
    assert page["ocr_text"] == page["text"]
    assert page["effective_text_source"] == "ocr"
    assert page["ocr_status"] == "completed"
    assert page["pdf_blocks"]
    assert page["ocr_blocks"]


def test_preprocessor_reuses_matching_render_and_ocr_cache(tmp_path: Path) -> None:
    pdf_path = tmp_path / "text-layer-cache.pdf"
    create_pdf(pdf_path)
    repository = FakeRepository()
    settings = Settings(
        app_env="test",
        file_storage_root=tmp_path / "files-cache",
        ocr_policy="all",
    )
    ocr = FakeOcrEngine()
    preprocessor = PagePreprocessor(
        settings=settings,
        parser=PdfParser(settings),
        repository=repository,  # type: ignore[arg-type]
        image_storage=LocalImageStorage(settings),
        ocr_engine=ocr,
    )

    first = asyncio.run(
        preprocessor.prepare(
            pdf_path=pdf_path,
            document_id="doc_test",
            selected_pages=[1],
        )
    )
    asyncio.run(repository.upsert_pages("doc_test", first.pages))
    second = asyncio.run(
        preprocessor.prepare(
            pdf_path=pdf_path,
            document_id="doc_test",
            selected_pages=[1],
        )
    )

    assert ocr.calls == 1
    assert second.pages[0]["render_cache_hit"] is True
    assert second.pages[0]["ocr_cache_hit"] is True


class FailingSecondPageEngine(LocalTextExtractionEngine):
    async def extract(self, chunk: Any, config: Any) -> list[dict[str, Any]]:
        if chunk.page_no == 2:
            raise RuntimeError("simulated page failure")
        return await super().extract(chunk, config)


class ConcurrentPageEngine(LocalTextExtractionEngine):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def extract(self, chunk: Any, config: Any) -> list[dict[str, Any]]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return await super().extract(chunk, config)
        finally:
            self.active -= 1


def test_semantic_extraction_prefetches_multiple_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "three-pages.pdf"
    document = fitz.open()
    for text in ["M12:1 gray pottery", "M12:2 red pottery", "M12:3 black pottery"]:
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(pdf_path)
    document.close()
    repository = FakeRepository()
    repository.job["pages"] = [1, 2, 3]
    engine = ConcurrentPageEngine()
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
        engine=engine,
    )

    asyncio.run(service.run_job("job_test"))

    assert repository.job["status"] == "completed"
    assert engine.max_active >= 2


def test_single_page_failure_keeps_successful_page_results(tmp_path: Path) -> None:
    pdf_path = tmp_path / "two-pages.pdf"
    document = fitz.open()
    for text in ["M12:1 gray pottery", "M12:2 red pottery"]:
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(pdf_path)
    document.close()
    repository = FakeRepository()
    repository.job["pages"] = [1, 2]
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
        engine=FailingSecondPageEngine(),
    )

    asyncio.run(service.run_job("job_test"))

    assert repository.job["status"] == "completed_with_warnings"
    assert repository.job["succeeded_pages"] == 1
    assert repository.job["failed_pages"] == 1
    assert len(repository.records) == 1
    assert repository.records[0]["source_pages"] == [1]
    assert repository.job["page_issues"][0]["page"] == 2


def test_retry_failed_pages_dispatches_only_failed_page_runs(tmp_path: Path) -> None:
    pdf_path = tmp_path / "retry-request.pdf"
    create_pdf(pdf_path)
    repository = FakeRepository()
    repository.job.update(
        status="completed_with_warnings",
        stage="completed_with_warnings",
        failed_pages=1,
        succeeded_pages=1,
        page_issues=[
            {
                "page": 2,
                "stage": "semantic_extraction",
                "severity": "error",
                "message": "invalid region_id",
            }
        ],
    )
    repository.page_runs[1] = {"page_no": 1, "status": "completed"}
    repository.page_runs[2] = {"page_no": 2, "status": "failed"}
    dispatcher = RecordingDispatcher()
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
        dispatcher=dispatcher,
    )

    retried = asyncio.run(service.retry_failed_pages("job_test"))

    assert retried["status"] == "queued"
    assert retried["retry_pages"] == [2]
    assert retried["progress"] == {"current": 0, "total": 1, "percent": 0}
    assert retried["cancel_requested"] is False
    assert dispatcher.dispatched == ["job_test"]


def test_retry_run_merges_recovered_page_with_successful_results(tmp_path: Path) -> None:
    pdf_path = tmp_path / "retry-merge.pdf"
    document = fitz.open()
    for text in ["M12:1 gray pottery", "M12:2 red pottery"]:
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(pdf_path)
    document.close()
    repository = FakeRepository()
    repository.job["pages"] = [1, 2]
    failing_service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
        engine=FailingSecondPageEngine(),
    )
    asyncio.run(failing_service.run_job("job_test"))
    retained_record_id = repository.records[0]["id"]

    repository.job.update(
        status="queued",
        stage="retry_waiting",
        retry_pages=[2],
        cancel_requested=False,
    )
    recovery_service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
    )
    asyncio.run(recovery_service.run_job("job_test"))

    assert repository.job["status"] == "completed"
    assert repository.job["succeeded_pages"] == 2
    assert repository.job["failed_pages"] == 0
    assert repository.job["retry_pages"] == []
    assert repository.job["last_retry_pages"] == [2]
    assert {tuple(record["source_pages"]) for record in repository.records} == {(1,), (2,)}
    assert retained_record_id in {record["id"] for record in repository.records}
    assert {page["page_no"] for page in repository.pages} == {1, 2}
    assert len(repository.text_chunks) == 2


class BlockingExtractionEngine(LocalTextExtractionEngine):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def extract(self, chunk: Any, config: Any) -> list[dict[str, Any]]:
        del chunk, config
        self.started.set()
        await asyncio.Event().wait()
        return []


def test_cancel_job_immediately_stops_running_orchestration(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cancel.pdf"
    create_pdf(pdf_path)
    repository = FakeRepository()
    dispatcher = LocalJobDispatcher()
    engine = BlockingExtractionEngine()
    service = build_service(
        tmp_path=tmp_path,
        pdf_path=pdf_path,
        repository=repository,
        engine=engine,
        dispatcher=dispatcher,
    )
    dispatcher.bind(service.run_job)

    async def run() -> dict[str, Any]:
        await dispatcher.dispatch("job_test")
        await asyncio.wait_for(engine.started.wait(), timeout=2)
        return await asyncio.wait_for(service.cancel_job("job_test"), timeout=2)

    cancelled_job = asyncio.run(run())

    assert cancelled_job["status"] == "cancelled"
    assert cancelled_job["stage"] == "cancelled"
    assert cancelled_job["cancel_requested"] is True
    assert any(run["status"] == "cancelled" for run in repository.model_runs)


def test_cached_semantic_evidence_rebinds_to_current_job_regions() -> None:
    cached_records = [
        {
            "record_type": "artifact",
            "fields": {
                "artifact_id": {
                    "value": "M3:4",
                    "raw_value": "M3:4",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 20,
                            "quote": "M3:4 石铲",
                            "bbox": [0.1, 0.1, 0.2, 0.2],
                            "region_id": "old-region",
                        }
                    ],
                }
            },
            "linkage": {
                "identity": {
                    "artifact_id_raw": "M3:4",
                    "artifact_id_normalized": "M3:4",
                },
                "visual_link": {
                    "evidence_block_ids": ["old-region"],
                    "evidence": [
                        {
                            "page": 20,
                            "quote": "M3:4 石铲",
                            "bbox": [0.1, 0.1, 0.2, 0.2],
                            "region_id": "old-region",
                        }
                    ],
                },
            },
        }
    ]
    blocks = [
        {
            "region_id": "current-region",
            "text": "M3:4 石铲，残长 12.3 厘米。",
            "bbox": [0.3, 0.4, 0.8, 0.5],
        }
    ]

    rebound = ExtractionService._rebind_cached_records(
        cached_records,
        page_no=20,
        blocks=blocks,
    )

    field_evidence = rebound[0]["fields"]["artifact_id"]["evidence"][0]
    visual_link = rebound[0]["linkage"]["visual_link"]
    assert field_evidence["region_id"] == "current-region"
    assert field_evidence["bbox"] == [0.3, 0.4, 0.8, 0.5]
    assert visual_link["evidence_block_ids"] == ["current-region"]
    assert cached_records[0]["fields"]["artifact_id"]["evidence"][0]["region_id"] == "old-region"
