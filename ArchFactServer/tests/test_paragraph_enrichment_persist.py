import asyncio
from typing import Any

from app.api.v1.extraction_jobs import (
    _record_card_fields_changed,
    enrich_records_with_paragraph_fields,
)
from app.services.result_fusion import ResultFusionService


def test_record_card_fields_changed_detects_morphology_upgrade() -> None:
    before = {
        "fields": {
            "morphological_description": {"value": "片状", "raw_value": "片状"},
        },
        "text_evidence": [],
    }
    after = {
        "fields": {
            "morphological_description": {
                "value": "强风化，片状，倒梯形",
                "raw_value": "强风化，片状，倒梯形",
            },
        },
        "text_evidence": [{"quote": "M1：37、玉梳背。片状，倒梯形"}],
    }
    assert _record_card_fields_changed(before, after) is True
    assert _record_card_fields_changed(after, after) is False


def test_enrich_records_persists_upgraded_morphology() -> None:
    class Repository:
        def __init__(self) -> None:
            self.patches: list[dict[str, Any]] = []
            self.regions = [
                {
                    "_id": "m1-37-l1",
                    "page": 137,
                    "kind": "text",
                    "text": "M1：37、玉梳背。南瓜黄闪玉，强风化，大部分受沁变白，局部尚存黄褐色原质。片状，",
                    "bbox": [0.1367, 0.1169, 0.9167, 0.1312],
                },
                {
                    "_id": "m1-37-l2",
                    "page": 137,
                    "kind": "text",
                    "text": "倒梯形、一面有线切割凹痕。顶端中央凹缺，缺口内作“弓”字形凸起，凸块下方有穿孔，",
                    "bbox": [0.12, 0.1374, 0.92, 0.152],
                },
                {
                    "_id": "m1-37-l3",
                    "page": 137,
                    "kind": "text",
                    "text": "双面钻。两侧边斜直，两下角弧切，底端有较宽的凸桦，上无穿孔。宽4.9、高3.7、厚0.4~0.6",
                    "bbox": [0.12, 0.1627, 0.92, 0.177],
                },
                {
                    "_id": "m1-37-l4",
                    "page": 137,
                    "kind": "text",
                    "text": "厘米。（图3-2D：彩版一二.8）",
                    "bbox": [0.12, 0.1875, 0.55, 0.202],
                },
                {
                    "_id": "m1-38-mashed",
                    "page": 137,
                    "kind": "text",
                    "text": "M138.玉锥形饰。南瓜黄玉，强风化。圆柱体较粗。长8、直径1.1厘米。（图",
                    "bbox": [0.134, 0.2085, 0.921, 0.227],
                },
            ]

        async def list_page_regions(self, job_id: str, page_no: int) -> list[dict]:
            assert job_id == "job-1"
            return [region for region in self.regions if region["page"] == page_no]

        async def patch_record_paragraph_enrichment(self, **kwargs: Any) -> None:
            self.patches.append(kwargs)

    class Container:
        def __init__(self) -> None:
            self.repository = Repository()

    record = {
        "_id": "rec-m1-37",
        "job_id": "job-1",
        "source_pages": [137],
        "region_ids": ["m1-37-l1"],
        "fields": {
            "artifact_id": {
                "value": "M1:37",
                "raw_value": "M1：37",
                "status": "valid",
                "evidence": [
                    {
                        "page": 137,
                        "quote": "M1：37",
                        "region_id": "m1-37-l1",
                        "kind": "text",
                    }
                ],
            },
            "category": {"value": "玉梳背", "raw_value": "玉梳背", "status": "valid"},
            "texture": {"value": "南瓜黄闪玉", "raw_value": "南瓜黄闪玉", "status": "valid"},
            "morphological_description": {
                "value": "片状",
                "raw_value": "片状",
                "status": "valid",
                "evidence": [],
            },
            "measurements": {
                "value": "宽 4.9 cm；高 3.7 cm；厚 0.4 cm；长 8 cm；直径 1.1 cm",
                "raw_value": "宽4.9、高3.7、厚0.4、长8、直径1.1厘米",
                "status": "valid",
                "evidence": [],
            },
        },
        "text_evidence": [],
    }
    container = Container()

    persisted = asyncio.run(
        enrich_records_with_paragraph_fields(container, "job-1", [record], persist=True)
    )

    assert persisted == 1
    assert "倒梯形" in record["fields"]["morphological_description"]["value"]
    assert "弓" in record["fields"]["morphological_description"]["value"]
    assert "长 8" not in record["fields"]["measurements"]["value"]
    assert "直径 1.1" not in record["fields"]["measurements"]["value"]
    assert record["paragraph_enrichment_version"] == ResultFusionService.version
    assert len(container.repository.patches) == 1
    patch = container.repository.patches[0]
    assert patch["record_id"] == "rec-m1-37"
    assert "倒梯形" in patch["fields"]["morphological_description"]["value"]
    assert patch["enrichment_version"] == ResultFusionService.version

    # Second pass should not rewrite once the in-memory record already matches.
    persisted_again = asyncio.run(
        enrich_records_with_paragraph_fields(container, "job-1", [record], persist=True)
    )
    assert persisted_again == 0
    assert len(container.repository.patches) == 1
