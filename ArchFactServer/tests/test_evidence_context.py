import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from app.api.v1.extraction_jobs import get_record_evidence_context


class EvidenceContextRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.record = {
            "_id": "record-m13-8",
            "job_id": "job-m13-8",
            "record_type": "artifact",
            "source_pages": [160],
            "fields": {
                "artifact_id": {
                    "value": "M13:8",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 160,
                            "quote": "M13：8",
                            "bbox": [0.1013, 0.5697, 0.8780, 0.5887],
                            "region_id": "text-main",
                        }
                    ],
                },
                "figure_caption": {
                    "raw_value": "图3-14B",
                    "value": "图3-14B",
                    "status": "valid",
                    "evidence": [
                        {
                            "page": 160,
                            "quote": "图3-14B",
                            "bbox": [0.1013, 0.5697, 0.8780, 0.5887],
                            "region_id": "text-main",
                        }
                    ],
                },
            },
            "linkage": {},
            "link_hints": {"caption_texts": ["图3-14B"]},
            "warnings": [],
            "region_ids": ["text-main"],
            "relation_ids": [],
            "created_at": now,
        }
        common = {
            "job_id": "job-m13-8",
            "document_id": "document-1",
            "page": 160,
            "kind": "text",
            "source": "paddleocr",
            "created_at": now,
        }
        self.regions = [
            {
                **common,
                "_id": "text-main",
                "text": (
                    "M13：8，玉珠。白色闪玉。腰鼓形，一端破损。"
                    "高1.8、直径1.4厘米。（图3-14B；"
                ),
                "bbox": [0.1013, 0.5697, 0.8780, 0.5887],
            },
            {
                **common,
                "_id": "text-continuation",
                "text": "彩版四九，2）",
                "bbox": [0.0607, 0.5916, 0.1940, 0.6116],
            },
            {
                **common,
                "_id": "text-next-artifact",
                "text": "M13：9，玉镯。乳白色闪玉。",
                "bbox": [0.1027, 0.6188, 0.8887, 0.6331],
            },
        ]

    async def get_job(self, job_id: str) -> dict:
        return {"_id": job_id}

    async def get_record(self, job_id: str, record_id: str) -> dict:
        return deepcopy(self.record)

    async def list_page_regions(self, job_id: str, page_no: int) -> list[dict]:
        return deepcopy(self.regions)

    async def list_relations_by_ids(
        self,
        job_id: str,
        relation_ids: list[str],
    ) -> list[dict]:
        return []

    async def list_regions_by_ids(
        self,
        job_id: str,
        region_ids: list[str],
    ) -> list[dict]:
        selected = set(region_ids)
        return deepcopy(
            [region for region in self.regions if region["_id"] in selected]
        )


def test_existing_evidence_context_completes_wrapped_figure_caption() -> None:
    repository = EvidenceContextRepository()

    response = asyncio.run(
        get_record_evidence_context(
            "job-m13-8",
            "record-m13-8",
            SimpleNamespace(repository=repository),
        )
    )

    caption = response.data.record.fields["figure_caption"]
    assert caption.raw_value == "（图3-14B；彩版四九，2）"
    assert caption.value == "图3-14B;彩版四九,2"
    assert [evidence.region_id for evidence in caption.evidence] == [
        "text-main",
        "text-continuation",
    ]
    assert [
        evidence.region_id for evidence in response.data.record.text_evidence
    ] == [
        "text-main",
        "text-continuation",
    ]
    assert {region.id for region in response.data.regions} == {
        "text-main",
        "text-continuation",
    }
