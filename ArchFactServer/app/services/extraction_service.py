import asyncio
import copy
import hashlib
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz

from app.core.config import Settings
from app.core.errors import ConflictError
from app.infrastructure.gridfs_storage import GridFsStorage
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.models.schemas import ExtractionConfig, ExtractionJobCreate
from app.repositories.mongo_repository import MongoRepository
from app.services.artifact_entity_linker import ArtifactEntityLinker
from app.services.detection_engine import DetectionEngine, PageImageInput
from app.services.document_text_index import DocumentTextIndex, DocumentTextIndexer
from app.services.extraction_engine import ExtractionEngine, PageChunk
from app.services.extraction_pipeline import PageExtractionResult, build_extraction_pipeline
from app.services.page_discovery import PageDiscoveryService
from app.services.page_preprocessor import PagePreprocessor
from app.services.post_processor import PostProcessor
from app.services.region_processor import RegionProcessor
from app.services.relation_matcher import RelationMatcher
from app.services.result_fusion import ResultFusionService


class ExtractionService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: MongoRepository,
        storage: GridFsStorage,
        preprocessor: PagePreprocessor,
        engine: ExtractionEngine,
        detector: DetectionEngine,
        region_processor: RegionProcessor,
        relation_matcher: RelationMatcher,
        result_fusion: ResultFusionService,
        entity_linker: ArtifactEntityLinker,
        document_text_indexer: DocumentTextIndexer,
        page_discovery: PageDiscoveryService,
        post_processor: PostProcessor,
        dispatcher: LocalJobDispatcher,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._storage = storage
        self._preprocessor = preprocessor
        self._engine = engine
        self._detector = detector
        self._region_processor = region_processor
        self._relation_matcher = relation_matcher
        self._result_fusion = result_fusion
        self._entity_linker = entity_linker
        self._document_text_indexer = document_text_indexer
        self._page_discovery = page_discovery
        self._pipeline = build_extraction_pipeline(engine, settings.extraction_engine)
        self._post_processor = post_processor
        self._dispatcher = dispatcher

    async def close(self) -> None:
        close = getattr(self._engine, "aclose", None)
        if close is not None:
            await close()
        close_ocr = getattr(self._preprocessor.ocr_engine, "aclose", None)
        if close_ocr is not None:
            await close_ocr()

    async def create_job(
        self,
        request: ExtractionJobCreate,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        await self._repository.get_document(request.document_id)
        pipeline_id = self._pipeline.resolve_id(request.pipeline_id)
        job = await self._repository.create_job(
            document_id=request.document_id,
            pages=request.pages,
            pipeline_id=pipeline_id,
            config=request.config.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        if job.pop("_was_created", False):
            await self._repository.append_event(job["_id"], "INFO", "抽取任务已进入队列")
            await self._dispatcher.dispatch(job["_id"])
        return job

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = await self._repository.get_job(job_id)
        if job["status"] in {"completed", "completed_with_warnings", "failed", "cancelled"}:
            raise ConflictError("当前任务已经结束，不能取消")
        await self._repository.append_event(job_id, "INFO", "正在取消抽取任务")
        await self._repository.request_cancel(job_id)
        cancelled_running_task = await self._dispatcher.cancel(job_id)
        current = await self._repository.get_job(job_id)
        if not cancelled_running_task or current.get("status") != "cancelled":
            await self._finalize_cancelled_job(job_id)
        return await self._repository.get_job(job_id)

    async def retry_failed_pages(self, job_id: str) -> dict[str, Any]:
        job = await self._repository.get_job(job_id)
        if job["status"] not in {
            "completed",
            "completed_with_warnings",
            "failed",
            "cancelled",
        }:
            raise ConflictError("当前抽取任务仍在运行，不能重试失败页面")

        page_runs = await self._repository.list_job_page_runs(job_id)
        retry_pages = {
            int(run["page_no"])
            for run in page_runs
            if run.get("status") == "failed" and int(run.get("page_no", 0)) > 0
        }
        if not retry_pages:
            retry_pages = {
                int(issue["page"])
                for issue in job.get("page_issues", [])
                if issue.get("severity") == "error" and int(issue.get("page", 0)) > 0
            }
        if not retry_pages:
            raise ConflictError("当前任务没有可重试的失败页面")

        ordered_pages = sorted(retry_pages)
        attempt_started_at = datetime.now(UTC)
        await self._repository.update_job(
            job_id,
            status="queued",
            stage="retry_waiting",
            progress={"current": 0, "total": len(ordered_pages), "percent": 0},
            cancel_requested=False,
            error=None,
            retry_pages=ordered_pages,
            attempt_started_at=attempt_started_at,
            retry_attempt=int(job.get("retry_attempt", 0)) + 1,
        )
        await self._repository.append_event(
            job_id,
            "INFO",
            f"已提交失败页恢复任务，仅重新处理 {len(ordered_pages)} 页："
            + "、".join(str(page) for page in ordered_pages),
        )
        await self._dispatcher.dispatch(job_id)
        return await self._repository.get_job(job_id)

    async def run_job(self, job_id: str) -> None:
        job = await self._repository.get_job(job_id)
        document = await self._repository.get_document(job["document_id"])
        config = ExtractionConfig.model_validate(job["config"])
        self._pipeline.resolve_id(job.get("pipeline_id", "default"))
        records: list[dict[str, Any]] = []
        regions: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        model_run_ids: list[str] = []
        active_model_run_ids: set[str] = set()
        page_issues: list[dict[str, Any]] = []
        page_metrics: list[dict[str, Any]] = []
        document_ready = False
        semantic_tasks: dict[int, asyncio.Task[Any]] = {}
        semantic_elapsed_ms: dict[int, int] = {}
        semantic_cache_hits: dict[int, bool] = {}
        semantic_request_metrics: dict[int, dict[str, int]] = {}
        extraction_stage = self._pipeline.extraction_stage
        extraction_run_id: str | None = None
        ocr_run_id: str | None = None
        retry_pages = {
            int(page)
            for page in job.get("retry_pages", [])
            if isinstance(page, int) or str(page).isdigit()
        }
        retry_mode = bool(retry_pages)
        execution_pages = sorted(retry_pages) if retry_mode else job.get("pages")
        base_succeeded_pages = int(job.get("succeeded_pages", 0)) if retry_mode else 0
        if retry_mode:
            stored_regions = await self._repository.list_job_regions(job_id)
            regions = [
                self._restore_stored_output(region)
                for region in stored_regions
                if int(region.get("page", 0)) not in retry_pages
            ]
            retained_region_ids = {str(region["id"]) for region in regions}
            stored_relations = await self._repository.list_job_relations(job_id)
            relations = [
                self._restore_stored_output(relation)
                for relation in stored_relations
                if str(relation.get("source_region_id", "")) in retained_region_ids
                and str(relation.get("target_region_id", "")) in retained_region_ids
            ]
            stored_records = await self._repository.list_job_records(job_id)
            records = [
                self._restore_stored_output(record)
                for record in stored_records
                if not self._record_touches_pages(record, retry_pages)
            ]
            page_issues = [
                copy.deepcopy(issue)
                for issue in job.get("page_issues", [])
                if int(issue.get("page", 0)) not in retry_pages
            ]
        semantic_cache_enabled = bool(
            self._settings.semantic_cache_enabled
            and self._settings.extraction_engine == "llm"
            and getattr(self._repository, "get_semantic_extraction_cache", None)
            and getattr(self._repository, "upsert_semantic_extraction_cache", None)
        )
        semantic_page_semaphore = asyncio.Semaphore(self._settings.semantic_page_concurrency)

        async def ensure_ocr_run() -> str:
            nonlocal ocr_run_id
            if ocr_run_id is not None:
                return ocr_run_id
            ocr_engine = self._preprocessor.ocr_engine
            ocr_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="page_ocr",
                provider=ocr_engine.provider,
                model=ocr_engine.model,
                version=ocr_engine.version,
                config={
                    **ocr_engine.config,
                    "pages": execution_pages or "all",
                },
            )
            ocr_run_id = ocr_run["_id"]
            active_model_run_ids.add(ocr_run_id)
            model_run_ids.append(ocr_run_id)
            return ocr_run_id

        async def ensure_extraction_run() -> str:
            nonlocal extraction_run_id
            if extraction_run_id is not None:
                return extraction_run_id
            extraction_run = await self._repository.create_model_run(
                job_id=job_id,
                stage=extraction_stage.key,
                provider=extraction_stage.provider,
                model=extraction_stage.model,
                version=extraction_stage.version,
                config={
                    "pipeline_id": self._pipeline.id,
                    "template_id": config.template_id,
                    "schema_version": config.schema_version,
                },
            )
            extraction_run_id = extraction_run["_id"]
            active_model_run_ids.add(extraction_run_id)
            model_run_ids.append(extraction_run_id)
            await self._repository.append_event(
                job_id,
                "INFO",
                "已启动 OCR 与语义抽取流水线，完成页面将提前进入结构化处理",
            )
            return extraction_run_id

        async def extract_semantic_page(page: dict[str, Any]) -> Any:
            async with semantic_page_semaphore:
                page_no = int(page["page_no"])
                chunk_id = f"{job_id}:page:{page_no}"
                chunk = PageChunk(
                    chunk_id=chunk_id,
                    page_no=page_no,
                    text=page["text"],
                    blocks=page.get("blocks", []),
                )
                cache_key, schema_hash, text_hash = self._semantic_cache_identity(
                    document_sha256=str(document.get("sha256") or document["_id"]),
                    page_no=page_no,
                    text=chunk.text,
                    config=config,
                )
                started = time.perf_counter()
                try:
                    if semantic_cache_enabled:
                        cached = await self._repository.get_semantic_extraction_cache(cache_key)
                        if cached is not None and isinstance(cached.get("records"), list):
                            semantic_cache_hits[page_no] = True
                            return PageExtractionResult(
                                records=self._rebind_cached_records(
                                    cached["records"],
                                    page_no=page_no,
                                    blocks=chunk.blocks,
                                )
                            )

                    semantic_cache_hits[page_no] = False
                    result = await self._pipeline.extract_page(chunk, config)
                    if semantic_cache_enabled:
                        await self._repository.upsert_semantic_extraction_cache(
                            cache_key=cache_key,
                            document_id=document["_id"],
                            page_no=page_no,
                            provider=extraction_stage.provider,
                            model=extraction_stage.model,
                            schema_hash=schema_hash,
                            text_hash=text_hash,
                            records=result.records,
                        )
                    return result
                finally:
                    semantic_elapsed_ms[page_no] = round(
                        (time.perf_counter() - started) * 1000
                    )
                    consume_metrics = getattr(self._engine, "consume_metrics", None)
                    if consume_metrics is not None:
                        semantic_request_metrics[page_no] = consume_metrics(chunk_id)

        async def schedule_semantic_page(page: dict[str, Any]) -> None:
            page_no = int(page["page_no"])
            if (
                page_no in semantic_tasks
                or page.get("status") != "ready"
                or page.get("needs_ocr")
                or page.get("discovery_only")
            ):
                return
            await ensure_extraction_run()
            self._assign_text_region_ids(job_id=job_id, page=page)
            semantic_tasks[page_no] = asyncio.create_task(extract_semantic_page(page))

        try:
            if self._settings.extraction_engine == "local" and any(
                rule.handler == "instruction" for rule in config.post_processing_rules
            ):
                await self._repository.append_event(
                    job_id,
                    "INFO",
                    "自定义自然语言后处理规则仅在 Coze 模式执行；local 模式只执行内置规则",
                )
            if await self._cancel_if_requested(job_id):
                return
            await self._repository.update_job(
                job_id,
                status="preparing",
                stage="pdf_parsing",
                progress={
                    "current": 0,
                    "total": len(execution_pages or []),
                    "percent": 5,
                },
            )
            await self._repository.update_document(document["_id"], status="parsing", error=None)
            await self._repository.append_event(job_id, "INFO", "正在解析 PDF 文本层")
            parse_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="pdf_parse",
                provider="pymupdf",
                model="text-layer-parser",
                version="1",
                config={"pages": execution_pages},
            )
            parse_run_id = parse_run["_id"]
            active_model_run_ids.add(parse_run_id)
            model_run_ids.append(parse_run_id)

            last_preparation_percent = 5
            last_preparation_current = 0
            last_preparation_total = len(execution_pages or [])

            async def report_preparation_progress(
                current: int,
                total: int,
                page: dict[str, Any],
            ) -> None:
                nonlocal last_preparation_current
                nonlocal last_preparation_percent
                nonlocal last_preparation_total
                last_preparation_current = max(last_preparation_current, current)
                last_preparation_total = max(last_preparation_total, total)
                last_preparation_percent = max(
                    last_preparation_percent,
                    5 + round(current / max(total, 1) * 30),
                )
                await self._repository.update_job(
                    job_id,
                    status="preparing",
                    stage="page_rendering",
                    progress={
                        "current": last_preparation_current,
                        "total": last_preparation_total,
                        "percent": last_preparation_percent,
                    },
                )
                if page.get("ocr_attempted"):
                    current_ocr_run_id = await ensure_ocr_run()
                    if page.get("ocr_status") == "completed":
                        page["text_model_run_id"] = current_ocr_run_id
                await self._repository.upsert_job_page_run(
                    job_id=job_id,
                    document_id=document["_id"],
                    page_no=int(page["page_no"]),
                    status=("failed" if page.get("status") == "failed" else "prepared"),
                    render_status=(
                        "failed" if page.get("status") == "failed" else "completed"
                    ),
                    render_cache_hit=bool(page.get("render_cache_hit")),
                    ocr_status=page.get("ocr_status", "not_requested"),
                    ocr_cache_hit=bool(page.get("ocr_cache_hit")),
                    error=page.get("error") or page.get("ocr_error"),
                )
                await schedule_semantic_page(page)
                if page.get("status") != "failed":
                    await self._repository.append_event(
                        job_id,
                        "SUCCESS",
                        f"第 {page['page_no']} 页预处理完成",
                    )

            requested_pages = set(execution_pages or [])
            discovered_pages: list[int] = []
            discovery_index: list[dict[str, Any]] = []
            with tempfile.TemporaryDirectory(prefix="archfact-") as temp_dir:
                pdf_path = Path(temp_dir) / "document.pdf"
                await self._storage.download_to_path(document["storage"]["file_id"], pdf_path)
                pdf_page_count = await asyncio.to_thread(self._pdf_page_count, pdf_path)
                full_document_request = not execution_pages or requested_pages == set(
                    range(1, pdf_page_count + 1)
                )

                discovery_run_id: str | None = None
                if (
                    self._page_discovery.enabled
                    and requested_pages
                    and not full_document_request
                    and not retry_mode
                ):
                    discovery_run = await self._repository.create_model_run(
                        job_id=job_id,
                        stage="page_discovery",
                        provider=self._page_discovery.provider,
                        model=self._page_discovery.model,
                        version=self._page_discovery.version,
                        config={
                            "thumbnail_scale": self._settings.discovery_thumbnail_scale,
                            "ocr_render_scale": self._settings.discovery_ocr_render_scale,
                            "ocr_max_pages": self._settings.discovery_ocr_max_pages,
                        },
                    )
                    discovery_run_id = discovery_run["_id"]
                    active_model_run_ids.add(discovery_run_id)
                    model_run_ids.append(discovery_run_id)
                    await self._repository.update_job(
                        job_id,
                        status="preparing",
                        stage="page_discovery",
                        progress={
                            "current": 0,
                            "total": len(requested_pages),
                            "percent": 3,
                        },
                    )
                    await self._repository.append_event(
                        job_id,
                        "INFO",
                        "正在建立整本 PDF 轻量页面索引",
                    )
                    try:
                        cached_index = await self._repository.list_document_page_index(
                            document_id=document["_id"],
                            index_version=self._page_discovery.version,
                        )
                        cached_page_count = max(
                            (int(page["page_no"]) for page in cached_index),
                            default=0,
                        )
                        cache_valid = bool(cached_index) and len(cached_index) == cached_page_count
                        if document.get("page_count"):
                            cache_valid = cache_valid and (
                                cached_page_count == int(document["page_count"])
                            )
                        if cache_valid:
                            discovery_index = cached_index
                            await self._repository.append_event(
                                job_id,
                                "SUCCESS",
                                f"已复用 {len(discovery_index)} 页全文发现索引",
                            )
                        else:
                            discovery_result = await self._page_discovery.scan(pdf_path)
                            discovery_index = discovery_result.pages
                            await self._repository.replace_document_page_index(
                                document_id=document["_id"],
                                index_version=self._page_discovery.version,
                                pages=discovery_index,
                            )
                            await self._repository.append_event(
                                job_id,
                                "SUCCESS",
                                (
                                    f"全文轻量索引完成，共扫描 {discovery_result.page_count} 页，"
                                    f"耗时 {discovery_result.elapsed_ms / 1000:.2f}s"
                                ),
                            )
                    except Exception as exc:
                        page_issues.append(
                            self._page_issue(0, "page_discovery", "warning", str(exc))
                        )
                        await self._repository.append_event(
                            job_id,
                            "WARNING",
                            f"全文轻量索引失败，将仅处理用户选择页：{exc}",
                        )

                prepared = await self._preprocessor.prepare(
                    pdf_path=pdf_path,
                    document_id=document["_id"],
                    selected_pages=execution_pages,
                    on_progress=report_preparation_progress,
                )

                if discovery_index and requested_pages:
                    try:
                        requested_references = self._page_discovery.references_from_pages(
                            prepared.pages
                        )
                        if requested_references:
                            visual_references = {
                                reference
                                for reference in requested_references
                                if reference.startswith(("figure:", "plate:"))
                            }
                            discovery_index = await self._page_discovery.enrich_references(
                                pdf_path=pdf_path,
                                pages=discovery_index,
                                requested_references=(visual_references or requested_references),
                                requested_pages=requested_pages,
                            )
                            recall = self._page_discovery.recall(
                                pages=discovery_index,
                                requested_references=requested_references,
                                requested_pages=requested_pages,
                            )
                            discovered_pages = recall.pages
                            await self._repository.replace_document_page_index(
                                document_id=document["_id"],
                                index_version=self._page_discovery.version,
                                pages=discovery_index,
                            )
                            if discovered_pages:
                                await self._repository.append_event(
                                    job_id,
                                    "SUCCESS",
                                    (
                                        "全文索引自动召回关联候选页："
                                        + ", ".join(str(page) for page in discovered_pages)
                                    ),
                                )
                                additional = await self._preprocessor.prepare(
                                    pdf_path=pdf_path,
                                    document_id=document["_id"],
                                    selected_pages=discovered_pages,
                                    on_progress=report_preparation_progress,
                                )
                                for page in additional.pages:
                                    page["discovery_only"] = True
                                prepared.pages.extend(additional.pages)
                                prepared.pages.sort(key=lambda page: int(page["page_no"]))
                            unresolved_visual_references = [
                                reference
                                for reference in recall.unresolved_references
                                if reference.startswith(("figure:", "plate:"))
                            ]
                            if unresolved_visual_references:
                                await self._repository.append_event(
                                    job_id,
                                    "WARNING",
                                    (
                                        "以下跨页引用暂未找到明确候选页："
                                        + ", ".join(unresolved_visual_references)
                                    ),
                                )
                        else:
                            await self._repository.append_event(
                                job_id,
                                "INFO",
                                "用户选择页中未识别到明确图号、彩版号或器物编号，跳过跨页召回",
                            )
                    except Exception as exc:
                        page_issues.append(
                            self._page_issue(0, "candidate_recall", "warning", str(exc))
                        )
                        await self._repository.append_event(
                            job_id,
                            "WARNING",
                            f"跨页候选召回失败，将继续处理用户选择页：{exc}",
                        )

                elif self._page_discovery.enabled and full_document_request:
                    await self._repository.append_event(
                        job_id,
                        "INFO",
                        "当前任务覆盖整本 PDF，已跳过重复的跨页候选召回",
                    )

                if discovery_run_id is not None:
                    await self._repository.finish_model_run(
                        discovery_run_id,
                        status="completed",
                    )
                    active_model_run_ids.discard(discovery_run_id)

            preparation_update: dict[str, Any] = {
                "progress": {
                    "current": round(len(prepared.pages) * 0.35),
                    "total": len(prepared.pages),
                    "percent": 35,
                }
            }
            if not retry_mode:
                preparation_update.update(
                    requested_pages=sorted(requested_pages),
                    discovered_pages=discovered_pages,
                    effective_pages=[int(page["page_no"]) for page in prepared.pages],
                )
            await self._repository.update_job(job_id, **preparation_update)

            ocr_attempted_pages = [page for page in prepared.pages if page.get("ocr_attempted")]
            if ocr_attempted_pages:
                current_ocr_run_id = await ensure_ocr_run()
                ocr_success_pages = [
                    page for page in ocr_attempted_pages if page.get("ocr_status") == "completed"
                ]
                for page in ocr_success_pages:
                    page["text_model_run_id"] = current_ocr_run_id
                if ocr_success_pages:
                    await self._repository.finish_model_run(
                        current_ocr_run_id,
                        status="completed",
                    )
                    active_model_run_ids.discard(current_ocr_run_id)
                    await self._repository.append_event(
                        job_id,
                        "SUCCESS",
                        (
                            f"扫描页 OCR 完成，成功识别 {len(ocr_success_pages)}/"
                            f"{len(ocr_attempted_pages)} 页"
                        ),
                    )
                else:
                    await self._repository.finish_model_run(
                        current_ocr_run_id,
                        status="failed",
                        error="所有扫描页均未识别到有效文字",
                    )
                    active_model_run_ids.discard(current_ocr_run_id)
                    await self._repository.append_event(
                        job_id,
                        "WARNING",
                        "扫描页 OCR 未识别到有效文字",
                    )

            regions.extend(
                self._attach_text_regions(
                    job_id=job_id,
                    document_id=document["_id"],
                    pages=prepared.pages,
                    model_run_id=parse_run_id,
                )
            )
            await self._repository.upsert_pages(document["_id"], prepared.pages)
            index_pages = prepared.pages
            if retry_mode:
                indexed_page_numbers = {
                    int(page)
                    for page in (
                        job.get("effective_pages")
                        or job.get("requested_pages")
                        or job.get("pages")
                        or []
                    )
                    if isinstance(page, int) or str(page).isdigit()
                }
                indexed_page_numbers.update(retry_pages)
                stored_pages = await self._repository.list_document_pages(document["_id"])
                index_pages = [
                    page
                    for page in stored_pages
                    if int(page.get("page_no", 0)) in indexed_page_numbers
                ]
            document_text_index: DocumentTextIndex = self._document_text_indexer.build(
                job_id=job_id,
                document_id=document["_id"],
                pages=index_pages,
            )
            await self._repository.replace_document_text_chunks(
                job_id=job_id,
                document_id=document["_id"],
                chunks=document_text_index.chunks,
            )
            await self._repository.update_job(
                job_id,
                document_text_index_version=self._document_text_indexer.version,
                document_text_chunk_count=len(document_text_index.chunks),
            )
            await self._repository.append_event(
                job_id,
                "SUCCESS",
                (
                    "全文 OCR 逻辑索引已生成，"
                    f"共 {len(document_text_index.chunks)} 个可追溯文本块"
                ),
            )
            await self._repository.replace_job_regions(job_id, regions)
            await self._repository.finish_model_run(parse_run_id, status="completed")
            active_model_run_ids.discard(parse_run_id)
            await self._repository.update_document(
                document["_id"],
                status="ready",
                page_count=prepared.page_count,
                error=None,
            )
            document_ready = True
            total = len(prepared.pages)
            for page in prepared.pages:
                if page.get("status") == "failed":
                    issue = self._page_issue(
                        page["page_no"],
                        "page_preparation",
                        "error",
                        page.get("error") or "页面预处理失败",
                    )
                    page_issues.append(issue)
                    await self._repository.append_event(
                        job_id,
                        "ERROR",
                        f"第 {page['page_no']} 页预处理失败：{issue['message']}",
                    )
                elif page.get("needs_ocr"):
                    if page.get("ocr_attempted"):
                        message = page.get("ocr_error") or "OCR 未识别到有效文字"
                    else:
                        message = "该页没有文本层，已保留分页图片并等待后续 OCR"
                    issue = self._page_issue(
                        page["page_no"],
                        "ocr",
                        "warning",
                        message,
                    )
                    page_issues.append(issue)
                    await self._repository.append_event(
                        job_id,
                        "WARNING",
                        f"第 {page['page_no']} 页 OCR 未完成：{message}",
                    )

                elif page.get("ocr_attempted") and page.get("ocr_status") != "completed":
                    message = page.get("ocr_error") or "OCR 未完成，已使用 PDF 文本层回退"
                    issue = self._page_issue(
                        page["page_no"],
                        "ocr",
                        "warning",
                        message,
                    )
                    page_issues.append(issue)
                    await self._repository.append_event(
                        job_id,
                        "WARNING",
                        f"第 {page['page_no']} 页 OCR 未完成：{message}",
                    )

            await self._repository.update_job(
                job_id,
                status="extracting",
                stage="model_extraction",
                progress={
                    "current": round(total * 0.35),
                    "total": total,
                    "percent": 35,
                },
                page_issues=page_issues,
            )

            detection_run_id: str | None = None
            if self._detector.enabled and any(
                page.get("status") != "failed" for page in prepared.pages
            ):
                detection_run = await self._repository.create_model_run(
                    job_id=job_id,
                    stage="image_detection",
                    provider=self._detector.provider,
                    model=self._detector.model,
                    version=self._detector.version,
                    config=self._detector.config,
                )
                detection_run_id = detection_run["_id"]
                active_model_run_ids.add(detection_run_id)
                model_run_ids.append(detection_run_id)
            else:
                await self._repository.append_event(
                    job_id,
                    "INFO",
                    "YOLO 检测适配器未启用，跳过图片检测阶段",
                )

            region_ocr_run_id: str | None = None
            if detection_run_id is not None and self._region_processor.ocr_engine.enabled:
                region_ocr_engine = self._region_processor.ocr_engine
                region_ocr_run = await self._repository.create_model_run(
                    job_id=job_id,
                    stage="region_ocr",
                    provider=region_ocr_engine.provider,
                    model=region_ocr_engine.model,
                    version=region_ocr_engine.version,
                    config={
                        **region_ocr_engine.config,
                        "region_kinds": ["number", "caption"],
                    },
                )
                region_ocr_run_id = region_ocr_run["_id"]
                active_model_run_ids.add(region_ocr_run_id)
                model_run_ids.append(region_ocr_run_id)

            if any(
                page.get("status") == "ready"
                and not page.get("needs_ocr")
                and not page.get("discovery_only")
                for page in prepared.pages
            ):
                await ensure_extraction_run()
                for page in prepared.pages:
                    await schedule_semantic_page(page)

            succeeded_pages = base_succeeded_pages
            failed_pages = 0

            for index, page in enumerate(prepared.pages, start=1):
                if await self._cancel_if_requested(job_id):
                    await self._cancel_tasks(semantic_tasks.values())
                    for run_id in active_model_run_ids:
                        await self._repository.finish_model_run(run_id, status="cancelled")
                    active_model_run_ids.clear()
                    await self._repository.replace_job_regions(job_id, regions)
                    await self._repository.replace_job_relations(job_id, relations)
                    await self._repository.replace_job_records(
                        job_id,
                        records,
                        model_run_ids=model_run_ids,
                        preserve_reviews=retry_mode,
                    )
                    return
                page_no = page["page_no"]
                if page.get("status") == "failed":
                    failed_pages += 1
                    await self._repository.update_job(
                        job_id,
                        progress={
                            "current": round(total * 0.35 + index / max(total, 1) * total * 0.65),
                            "total": total,
                            "percent": 35 + round(index / max(total, 1) * 55),
                        },
                        page_issues=page_issues,
                    )
                    continue

                page_has_output = bool(page.get("needs_ocr"))
                page_had_error = False
                detection_had_error = False
                semantic_had_error = False
                page_started = time.perf_counter()
                detection_ms = 0
                region_processing_ms = 0
                semantic_extraction_ms = 0
                detected_region_count = 0
                crop_count = 0
                await self._repository.append_event(job_id, "INFO", f"正在处理第 {page_no} 页")

                if detection_run_id is not None:
                    try:
                        await self._repository.update_job(job_id, stage="image_detection")
                        page_image = PageImageInput(
                            job_id=job_id,
                            document_id=document["_id"],
                            page_no=page_no,
                            image_path=Path(page["image_path"]),
                            object_key=page["image_object_key"],
                            width=page["image_width"],
                            height=page["image_height"],
                        )
                        stage_started = time.perf_counter()
                        try:
                            detected_regions = await self._detector.detect(page_image)
                        finally:
                            detection_ms = round((time.perf_counter() - stage_started) * 1000)
                        detected_region_count = len(detected_regions)
                        for region in detected_regions:
                            region["image_id"] = page.get("image_id")
                        normalized_regions = self._normalize_output_regions(
                            detected_regions,
                            job_id=job_id,
                            document_id=document["_id"],
                            page_no=page_no,
                            model_run_id=detection_run_id,
                        )
                        stage_started = time.perf_counter()
                        try:
                            processed_regions = await self._region_processor.process(
                                page=page_image,
                                regions=normalized_regions,
                                ocr_model_run_id=region_ocr_run_id,
                                page_ocr_blocks=page.get("ocr_blocks") or page.get("blocks", []),
                                page_ocr_model_run_id=page.get("text_model_run_id"),
                            )
                        finally:
                            region_processing_ms = round(
                                (time.perf_counter() - stage_started) * 1000
                            )
                        regions.extend(processed_regions)
                        crop_count = sum(
                            bool(region.get("crop_object_key")) for region in processed_regions
                        )
                        if crop_count:
                            await self._repository.append_event(
                                job_id,
                                "SUCCESS",
                                f"第 {page_no} 页已保存 {crop_count} 个检测区域裁剪图",
                            )
                        page_has_output = True
                    except Exception as exc:
                        page_had_error = True
                        detection_had_error = True
                        page_issues.append(
                            self._page_issue(page_no, "image_detection", "error", str(exc))
                        )
                        await self._repository.append_event(
                            job_id,
                            "ERROR",
                            f"第 {page_no} 页 YOLO 检测失败：{exc}",
                        )

                if (
                    extraction_run_id is not None
                    and not page.get("needs_ocr")
                    and not page.get("discovery_only")
                ):
                    try:
                        await self._repository.update_job(job_id, stage="semantic_extraction")
                        stage_started = time.perf_counter()
                        try:
                            page_result = await semantic_tasks[int(page_no)]
                        finally:
                            semantic_extraction_ms = semantic_elapsed_ms.get(
                                int(page_no),
                                round((time.perf_counter() - stage_started) * 1000),
                            )
                        regions.extend(
                            self._normalize_output_regions(
                                page_result.regions,
                                job_id=job_id,
                                document_id=document["_id"],
                                page_no=page_no,
                                model_run_id=extraction_run_id,
                            )
                        )
                        relations.extend(
                            self._normalize_output_relations(
                                page_result.relations,
                                job_id=job_id,
                            )
                        )
                        records.extend(
                            self._post_processor.apply(
                                page_result.records,
                                config.post_processing_rules,
                            )
                        )
                        page_has_output = True
                    except Exception as exc:
                        page_had_error = True
                        semantic_had_error = True
                        page_issues.append(
                            self._page_issue(page_no, "semantic_extraction", "error", str(exc))
                        )
                        await self._repository.append_event(
                            job_id,
                            "ERROR",
                            f"第 {page_no} 页语义抽取失败：{exc}",
                        )

                if page_had_error:
                    failed_pages += 1
                elif page_has_output:
                    succeeded_pages += 1
                else:
                    failed_pages += 1
                finalization_ms = round((time.perf_counter() - page_started) * 1000)
                total_ms = max(
                    finalization_ms,
                    detection_ms + region_processing_ms + semantic_extraction_ms,
                )
                page_metrics.append(
                    {
                        "page": page_no,
                        "text_chars": len(page.get("text", "")),
                        "ocr_ms": int(page.get("ocr_ms") or 0),
                        "render_cache_hit": bool(page.get("render_cache_hit")),
                        "ocr_cache_hit": bool(page.get("ocr_cache_hit")),
                        "detected_regions": detected_region_count,
                        "saved_crops": crop_count,
                        "detection_ms": detection_ms,
                        "region_processing_ms": region_processing_ms,
                        "semantic_extraction_ms": semantic_extraction_ms,
                        "semantic_cache_hit": semantic_cache_hits.get(page_no, False),
                        "semantic_requests": semantic_request_metrics.get(page_no, {}),
                        "finalization_ms": finalization_ms,
                        "total_ms": total_ms,
                    }
                )
                await self._repository.upsert_job_page_run(
                    job_id=job_id,
                    document_id=document["_id"],
                    page_no=int(page_no),
                    status="failed" if page_had_error else "completed",
                    detection_status=(
                        "failed"
                        if detection_had_error
                        else "completed"
                        if detection_run_id is not None
                        else "not_requested"
                    ),
                    semantic_status=(
                        "failed"
                        if semantic_had_error
                        else "completed"
                        if int(page_no) in semantic_tasks
                        else "not_requested"
                    ),
                    metrics=page_metrics[-1],
                    error=(
                        next(
                            (
                                issue["message"]
                                for issue in reversed(page_issues)
                                if int(issue.get("page", 0)) == int(page_no)
                            ),
                            None,
                        )
                        if page_had_error
                        else None
                    ),
                )
                percent = 35 + round(index / max(total, 1) * 55)
                await self._repository.update_job(
                    job_id,
                    progress={
                        "current": round(total * 0.35 + index / max(total, 1) * total * 0.65),
                        "total": total,
                        "percent": percent,
                    },
                    page_issues=page_issues,
                    page_metrics=page_metrics,
                    succeeded_pages=succeeded_pages,
                    failed_pages=failed_pages,
                )
                await self._repository.append_event(
                    job_id,
                    "INFO",
                    (
                        f"第 {page_no} 页耗时：YOLO {detection_ms / 1000:.2f}s，"
                        f"区域处理 {region_processing_ms / 1000:.2f}s，"
                        f"语义抽取 {semantic_extraction_ms / 1000:.2f}s，"
                        f"合计 {total_ms / 1000:.2f}s"
                    ),
                )
                if not page_had_error:
                    await self._repository.append_event(
                        job_id,
                        "SUCCESS",
                        f"第 {page_no} 页处理完成",
                    )

            for run_id in list(active_model_run_ids):
                await self._repository.finish_model_run(run_id, status="completed")
                active_model_run_ids.discard(run_id)
            await self._repository.update_job(
                job_id,
                status="matching",
                stage="relation_matching",
                progress={"current": total, "total": total, "percent": 92},
            )
            matching_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="relation_matching",
                provider=self._relation_matcher.provider,
                model=self._relation_matcher.model,
                version=self._relation_matcher.version,
                config=self._relation_matcher.config.as_dict(),
            )
            matching_run_id = matching_run["_id"]
            active_model_run_ids.add(matching_run_id)
            model_run_ids.append(matching_run_id)
            relation_by_id = {relation["id"]: relation for relation in relations}
            for page in prepared.pages:
                if page.get("status") == "failed":
                    continue
                page_no = int(page["page_no"])
                try:
                    matched_relations = self._relation_matcher.match_page(
                        job_id=job_id,
                        page_no=page_no,
                        regions=regions,
                    )
                    for relation in matched_relations:
                        relation["model_run_id"] = matching_run_id
                        relation_by_id[relation["id"]] = relation
                except Exception as exc:
                    page_issues.append(
                        self._page_issue(page_no, "relation_matching", "error", str(exc))
                    )
                    await self._repository.append_event(
                        job_id,
                        "ERROR",
                        f"第 {page_no} 页关系匹配失败：{exc}",
                    )
            relations = list(relation_by_id.values())
            await self._repository.finish_model_run(matching_run_id, status="completed")
            active_model_run_ids.discard(matching_run_id)
            await self._repository.append_event(
                job_id,
                "SUCCESS",
                f"区域关系匹配完成，共生成 {len(relations)} 条关系",
            )
            await self._repository.update_job(
                job_id,
                status="merging",
                stage="result_fusion",
                progress={"current": total, "total": total, "percent": 95},
            )
            fusion_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="result_fusion",
                provider=self._result_fusion.provider,
                model=self._result_fusion.model,
                version=self._result_fusion.version,
                config={"schema_version": config.schema_version},
            )
            fusion_run_id = fusion_run["_id"]
            active_model_run_ids.add(fusion_run_id)
            model_run_ids.append(fusion_run_id)
            records = self._document_text_indexer.enrich_records(
                records,
                document_text_index,
            )
            await self._repository.append_event(
                job_id,
                "INFO",
                f"正在融合 {len(records)} 条结构化记录与 {len(relations)} 条区域关系",
            )
            fusion_output = await self._fuse_with_heartbeat(
                job_id=job_id,
                records=records,
                regions=regions,
                relations=relations,
                config=config,
                model_run_id=fusion_run_id,
                total=total,
            )
            records = fusion_output.records
            relations = fusion_output.relations
            await self._repository.finish_model_run(fusion_run_id, status="completed")
            active_model_run_ids.discard(fusion_run_id)
            linked_records = sum(
                record.get("fusion_status") in {"linked", "partial"} for record in records
            )
            await self._repository.append_event(
                job_id,
                "SUCCESS",
                f"结果融合完成，{linked_records}/{len(records)} 条记录已关联视觉区域",
            )
            await self._repository.update_job(
                job_id,
                status="merging",
                stage="entity_linking",
                progress={"current": total, "total": total, "percent": 96},
            )
            entity_run = await self._repository.create_model_run(
                job_id=job_id,
                stage="entity_linking",
                provider=self._entity_linker.provider,
                model=self._entity_linker.model,
                version=self._entity_linker.version,
                config={"scope": "all_processed_pages"},
            )
            entity_run_id = entity_run["_id"]
            active_model_run_ids.add(entity_run_id)
            model_run_ids.append(entity_run_id)
            entity_output = self._entity_linker.link(
                job_id=job_id,
                document_id=document["_id"],
                records=records,
                regions=regions,
            )
            records = entity_output.records
            entities = entity_output.entities
            await self._repository.finish_model_run(entity_run_id, status="completed")
            active_model_run_ids.discard(entity_run_id)
            linked_entities = sum(entity.get("link_status") == "linked" for entity in entities)
            await self._repository.append_event(
                job_id,
                "SUCCESS",
                f"文档级器物实体归并完成，共生成 {len(entities)} 个实体，"
                f"其中 {linked_entities} 个已关联视觉证据",
            )
            await self._repository.update_job(
                job_id,
                status="post_processing",
                stage="saving_results",
                progress={"current": total, "total": total, "percent": 97},
            )
            await self._repository.replace_job_regions(job_id, regions)
            await self._repository.replace_job_relations(job_id, relations)
            await self._repository.replace_job_records(
                job_id,
                records,
                model_run_ids=model_run_ids,
                preserve_reviews=retry_mode,
            )
            await self._repository.replace_job_entities(
                job_id=job_id,
                document_id=document["_id"],
                entities=entities,
            )
            if succeeded_pages == 0 and failed_pages > 0:
                final_status = "failed"
                final_stage = "failed"
                final_error = f"全部 {failed_pages} 页处理失败"
                event_level = "ERROR"
            elif page_issues:
                final_status = "completed_with_warnings"
                final_stage = "completed_with_warnings"
                final_error = None
                event_level = "WARNING"
            else:
                final_status = "completed"
                final_stage = "completed"
                final_error = None
                event_level = "SUCCESS"
            await self._repository.update_job(
                job_id,
                status=final_status,
                stage=final_stage,
                error=final_error,
                page_issues=page_issues,
                succeeded_pages=succeeded_pages,
                failed_pages=failed_pages,
                retry_pages=[],
                last_retry_pages=sorted(retry_pages) if retry_mode else [],
                progress={"current": total, "total": total, "percent": 100},
            )
            await self._repository.append_event(
                job_id,
                event_level,
                (
                    f"抽取结束，共生成 {len(records)} 条记录；"
                    f"成功 {succeeded_pages} 页，失败 {failed_pages} 页"
                ),
            )
        except asyncio.CancelledError:
            await self._cancel_tasks(semantic_tasks.values())
            for run_id in active_model_run_ids:
                await self._repository.finish_model_run(run_id, status="cancelled")
            active_model_run_ids.clear()
            if not document_ready:
                await self._repository.update_document(
                    document["_id"],
                    status="uploaded",
                    error=None,
                )
            await self._finalize_cancelled_job(job_id)
            raise
        except Exception as exc:
            await self._cancel_tasks(semantic_tasks.values())
            for run_id in active_model_run_ids:
                await self._repository.finish_model_run(
                    run_id,
                    status="failed",
                    error=str(exc),
                )
            if not document_ready:
                await self._repository.update_document(
                    document["_id"],
                    status="failed",
                    error=str(exc),
                )
            await self._repository.update_job(
                job_id,
                status="failed",
                stage="failed",
                error=str(exc),
                page_issues=page_issues,
            )
            await self._repository.append_event(job_id, "ERROR", f"抽取失败：{exc}")

    def _semantic_cache_identity(
        self,
        *,
        document_sha256: str,
        page_no: int,
        text: str,
        config: ExtractionConfig,
    ) -> tuple[str, str, str]:
        schema_json = config.model_dump_json()
        schema_hash = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload = {
            "cache_version": "semantic-compact-v1",
            "document_sha256": document_sha256,
            "page_no": int(page_no),
            "text_hash": text_hash,
            "schema_hash": schema_hash,
            "pipeline_id": self._pipeline.id,
            "provider": self._pipeline.extraction_stage.provider,
            "model": self._pipeline.extraction_stage.model,
        }
        cache_key = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return cache_key, schema_hash, text_hash

    @classmethod
    def _rebind_cached_records(
        cls,
        records: list[dict[str, Any]],
        *,
        page_no: int,
        blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rebound = copy.deepcopy(records)
        for record in rebound:
            fields = record.get("fields", {})
            if isinstance(fields, dict):
                for field in fields.values():
                    if isinstance(field, dict):
                        cls._rebind_evidence(
                            field.get("evidence"),
                            page_no=page_no,
                            blocks=blocks,
                        )

            linkage = record.get("linkage", {})
            visual_link = linkage.get("visual_link", {}) if isinstance(linkage, dict) else {}
            if not isinstance(visual_link, dict):
                continue
            visual_evidence = visual_link.get("evidence")
            cls._rebind_evidence(
                visual_evidence,
                page_no=page_no,
                blocks=blocks,
            )
            visual_link["evidence_block_ids"] = cls._unique_nonempty_strings(
                [
                    evidence.get("region_id")
                    for evidence in visual_evidence
                    if isinstance(evidence, dict)
                ]
                if isinstance(visual_evidence, list)
                else []
            )
        return rebound

    @classmethod
    def _rebind_evidence(
        cls,
        evidence_items: Any,
        *,
        page_no: int,
        blocks: list[dict[str, Any]],
    ) -> None:
        if not isinstance(evidence_items, list):
            return
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                continue
            quote = str(evidence.get("quote") or "").strip()
            block = cls._find_evidence_block(quote, blocks)
            evidence["page"] = int(page_no)
            evidence["region_id"] = block.get("region_id") if block else None
            evidence["bbox"] = block.get("bbox") if block else None

    @staticmethod
    def _find_evidence_block(
        quote: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not quote:
            return None
        compact_quote = "".join(quote.split())
        for block in blocks:
            text = str(block.get("text") or "")
            if quote in text or (compact_quote and compact_quote in "".join(text.split())):
                return block
        return None

    @staticmethod
    def _unique_nonempty_strings(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def _restore_stored_output(document: dict[str, Any]) -> dict[str, Any]:
        restored = copy.deepcopy(document)
        restored["id"] = str(restored.pop("_id", restored.get("id", "")))
        for key in ("job_id", "created_at", "updated_at"):
            restored.pop(key, None)
        return restored

    @staticmethod
    def _record_touches_pages(record: dict[str, Any], pages: set[int]) -> bool:
        record_pages: set[int] = set()
        for key in ("source_pages", "associated_pages", "document_context_pages"):
            for page in record.get(key, []) or []:
                if isinstance(page, int) or str(page).isdigit():
                    record_pages.add(int(page))
        for field in (record.get("fields") or {}).values():
            if not isinstance(field, dict):
                continue
            for evidence in field.get("evidence", []) or []:
                if not isinstance(evidence, dict):
                    continue
                page = evidence.get("page")
                if isinstance(page, int) or str(page).isdigit():
                    record_pages.add(int(page))
        return bool(record_pages & pages)

    async def _fuse_with_heartbeat(
        self,
        *,
        job_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        config: ExtractionConfig,
        model_run_id: str,
        total: int,
    ) -> Any:
        fusion_task = asyncio.create_task(
            asyncio.to_thread(
                self._result_fusion.fuse,
                job_id=job_id,
                records=records,
                regions=regions,
                relations=relations,
                config=config,
                model_run_id=model_run_id,
            )
        )
        elapsed_seconds = 0
        try:
            while True:
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(fusion_task),
                        timeout=15,
                    )
                except TimeoutError:
                    elapsed_seconds += 15
                    await self._repository.update_job(
                        job_id,
                        status="merging",
                        stage="result_fusion",
                        progress={"current": total, "total": total, "percent": 95},
                        fusion_heartbeat_at=datetime.now(UTC),
                    )
                    if elapsed_seconds % 30 == 0:
                        await self._repository.append_event(
                            job_id,
                            "INFO",
                            f"结果融合仍在进行，已计算 {elapsed_seconds} 秒",
                        )
        except asyncio.CancelledError:
            fusion_task.cancel()
            await asyncio.gather(fusion_task, return_exceptions=True)
            raise

    async def _cancel_if_requested(
        self,
        job_id: str,
        partial_records: list[dict[str, Any]] | None = None,
    ) -> bool:
        current = await self._repository.get_job(job_id)
        if not current.get("cancel_requested"):
            return False
        if partial_records:
            await self._repository.replace_job_records(job_id, partial_records)
        await self._finalize_cancelled_job(job_id)
        return True

    async def _finalize_cancelled_job(self, job_id: str) -> None:
        current = await self._repository.get_job(job_id)
        if current.get("status") == "cancelled":
            return
        await self._repository.update_job(
            job_id,
            status="cancelled",
            stage="cancelled",
            cancel_requested=True,
            error=None,
        )
        await self._repository.append_event(job_id, "INFO", "抽取任务已立即停止")

    @staticmethod
    def _pdf_page_count(pdf_path: Path) -> int:
        with fitz.open(pdf_path) as document:
            return int(document.page_count)

    @staticmethod
    async def _cancel_tasks(tasks: Any) -> None:
        all_tasks = list(tasks)
        pending = [task for task in all_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

    @staticmethod
    def _page_issue(
        page_no: int,
        stage: str,
        severity: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "page": int(page_no),
            "stage": stage,
            "severity": severity,
            "message": message,
        }

    def _attach_text_regions(
        self,
        *,
        job_id: str,
        document_id: str,
        pages: list[dict[str, Any]],
        model_run_id: str,
    ) -> list[dict[str, Any]]:
        regions: list[dict[str, Any]] = []
        for page in pages:
            self._assign_text_region_ids(job_id=job_id, page=page)
            page_no = int(page["page_no"])
            for block in page.get("blocks", []):
                bbox = block.get("bbox")
                if not self._is_bbox(bbox):
                    continue
                region_id = str(block["region_id"])
                regions.append(
                    {
                        "id": region_id,
                        "document_id": document_id,
                        "page": page_no,
                        "kind": "text",
                        "bbox": [float(value) for value in bbox],
                        "bbox_px": block.get("bbox_px"),
                        "text": str(block.get("text", "")),
                        "confidence": block.get("confidence", 1.0),
                        "source": block.get("source", "pdf_text_layer"),
                        "model_run_id": page.get("text_model_run_id", model_run_id),
                        "image_id": None,
                        "crop_object_key": None,
                    }
                )
        return regions

    def _assign_text_region_ids(self, *, job_id: str, page: dict[str, Any]) -> None:
        job_suffix = job_id.removeprefix("job_")
        page_no = int(page["page_no"])
        for index, block in enumerate(page.get("blocks", [])):
            if self._is_bbox(block.get("bbox")):
                block["region_id"] = f"reg_{job_suffix}_{page_no}_{index}"

    def _normalize_output_regions(
        self,
        output_regions: list[dict[str, Any]],
        *,
        job_id: str,
        document_id: str,
        page_no: int,
        model_run_id: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, region in enumerate(output_regions):
            if not self._is_bbox(region.get("bbox")):
                continue
            normalized.append(
                {
                    **region,
                    "id": region.get("id") or f"reg_{job_id}_{page_no}_model_{index}",
                    "document_id": document_id,
                    "page": page_no,
                    "kind": region.get("kind", "other"),
                    "source": region.get("source", self._pipeline.extraction_stage.provider),
                    "model_run_id": model_run_id,
                }
            )
        return normalized

    def _normalize_output_relations(
        self,
        output_relations: list[dict[str, Any]],
        *,
        job_id: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                **relation,
                "id": relation.get("id") or f"rel_{job_id}_{index}",
                "relation_type": relation.get("relation_type", "related_to"),
                "method": relation.get("method", "model"),
                "version": relation.get("version", "1"),
                "review_status": relation.get("review_status", "unreviewed"),
            }
            for index, relation in enumerate(output_relations)
            if relation.get("source_region_id") and relation.get("target_region_id")
        ]

    def _is_bbox(self, bbox: Any) -> bool:
        return (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in bbox)
            and bbox[0] < bbox[2]
            and bbox[1] < bbox[3]
        )
