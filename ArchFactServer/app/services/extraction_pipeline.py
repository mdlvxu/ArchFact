from dataclasses import dataclass, field
from typing import Any

from app.core.errors import DomainError
from app.models.schemas import ExtractionConfig
from app.services.extraction_engine import ExtractionEngine, PageChunk


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """A stable stage descriptor exposed to jobs and model-run provenance."""

    key: str
    provider: str
    model: str
    version: str = "1"


@dataclass(slots=True)
class PageExtractionResult:
    """Normalized page output accepted from any future model adapter."""

    records: list[dict[str, Any]]
    regions: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)


class ExtractionPipeline:
    """Keeps the task API stable while the model implementation remains replaceable."""

    def __init__(
        self,
        *,
        pipeline_id: str,
        engine: ExtractionEngine,
        extraction_stage: PipelineStage,
    ) -> None:
        self.id = pipeline_id
        self.engine = engine
        self.extraction_stage = extraction_stage

    def resolve_id(self, requested_id: str) -> str:
        if requested_id in {"default", self.id}:
            return self.id
        raise DomainError(
            f"当前服务未注册抽取流水线：{requested_id}",
            code=4008,
            status_code=400,
        )

    async def extract_page(
        self,
        chunk: PageChunk,
        config: ExtractionConfig,
    ) -> PageExtractionResult:
        output = await self.engine.extract(chunk, config)
        if isinstance(output, PageExtractionResult):
            return output
        return PageExtractionResult(records=output)


def build_extraction_pipeline(engine: ExtractionEngine, engine_name: str) -> ExtractionPipeline:
    if engine_name in {"coze", "coze_http"}:
        return ExtractionPipeline(
            pipeline_id="coze-semantic-v1",
            engine=engine,
            extraction_stage=PipelineStage(
                key="semantic_extraction",
                provider="coze",
                model="configured-workflow",
            ),
        )
    if engine_name == "llm":
        return ExtractionPipeline(
            pipeline_id="llm-semantic-v1",
            engine=engine,
            extraction_stage=PipelineStage(
                key="semantic_extraction",
                provider=getattr(engine, "provider_name", "llm"),
                model=getattr(engine, "_model", "configured-model"),
            ),
        )
    return ExtractionPipeline(
        pipeline_id="local-text-v1",
        engine=engine,
        extraction_stage=PipelineStage(
            key="semantic_extraction",
            provider="archfact",
            model="local-text",
        ),
    )
