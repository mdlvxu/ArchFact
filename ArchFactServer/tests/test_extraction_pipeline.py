import asyncio

import pytest

from app.core.errors import DomainError
from app.models.schemas import ExtractionConfig, ExtractionFieldSpec
from app.services.extraction_engine import LocalTextExtractionEngine, PageChunk
from app.services.extraction_pipeline import build_extraction_pipeline


def test_pipeline_resolves_stable_business_alias() -> None:
    pipeline = build_extraction_pipeline(LocalTextExtractionEngine(), "local")

    assert pipeline.resolve_id("default") == "local-text-v1"
    assert pipeline.resolve_id("local-text-v1") == "local-text-v1"
    with pytest.raises(DomainError):
        pipeline.resolve_id("unregistered-model")


def test_pipeline_normalizes_legacy_engine_records() -> None:
    pipeline = build_extraction_pipeline(LocalTextExtractionEngine(), "local")
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    result = asyncio.run(
        pipeline.extract_page(
            PageChunk(
                chunk_id="job:page:1",
                page_no=1,
                text="M12:3",
                blocks=[{"region_id": "region-1", "text": "M12:3", "bbox": [0.1, 0.1, 0.3, 0.2]}],
            ),
            config,
        )
    )

    evidence = result.records[0]["fields"]["page_text"]["evidence"][0]
    assert evidence["region_id"] == "region-1"
    assert evidence["source"] == "local_text"
