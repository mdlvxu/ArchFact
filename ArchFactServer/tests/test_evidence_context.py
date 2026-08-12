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
        return {"_id": job_id, "document_id": "document-1"}

    async def list_document_pages(self, document_id: str) -> list[dict]:
        return []

    async def get_record(self, job_id: str, record_id: str) -> dict:
        return deepcopy(self.record)

    async def list_page_regions(self, job_id: str, page_no: int) -> list[dict]:
        return deepcopy(
            [region for region in self.regions if int(region.get("page", -1)) == int(page_no)]
        )

    async def patch_record_paragraph_enrichment(self, **_kwargs) -> None:
        return None

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
    assert response.data.text_record.id == "record-m13-8"
    assert response.data.primary_text_page == 160


class EntityEvidenceContextRepository(EvidenceContextRepository):
    def __init__(self) -> None:
        super().__init__()
        self.record["entity_id"] = "entity-m1-19"
        self.record["source_pages"] = [26]
        self.record["fields"] = {
            "artifact_id": {
                "value": "M1:19",
                "status": "valid",
                "evidence": [
                    {
                        "page": 26,
                        "quote": "M1:19",
                        "bbox": [0.1, 0.8, 0.2, 0.84],
                        "region_id": "color-caption",
                        "kind": "text",
                    }
                ],
            },
            "figure_caption": {
                "value": "彩版九",
                "status": "valid",
                "evidence": [
                    {
                        "page": 26,
                        "quote": "彩版九",
                        "bbox": [0.1, 0.8, 0.3, 0.84],
                        "region_id": "color-caption",
                        "kind": "text",
                    }
                ],
            },
        }
        self.text_record = deepcopy(self.record)
        self.text_record["_id"] = "record-m1-19-text"
        self.text_record["source_pages"] = [132]
        self.text_record["fields"] = {
            "artifact_id": {
                "value": "M1:19",
                "status": "valid",
                "evidence": [
                    {
                        "page": 132,
                        "quote": "M1:19",
                        "bbox": [0.1, 0.3, 0.2, 0.34],
                        "region_id": "body-text",
                        "kind": "text",
                    }
                ],
            },
            "morphological_description": {
                "value": "高领，折沿，折肩斜弧腹，圜底。",
                "status": "valid",
                "evidence": [
                    {
                        "page": 132,
                        "quote": "高领，折沿，折肩斜弧腹，圜底。",
                        "bbox": [0.1, 0.3, 0.8, 0.36],
                        "region_id": "body-text",
                        "kind": "text",
                    }
                ],
            },
            "measurements": {
                "value": {"height_cm": 22},
                "status": "valid",
                "evidence": [
                    {
                        "page": 132,
                        "quote": "高22厘米",
                        "bbox": [0.1, 0.35, 0.3, 0.39],
                        "region_id": "body-text",
                        "kind": "text",
                    }
                ],
            },
        }
        for candidate in (self.record, self.text_record):
            candidate["primary_number_region_id"] = "artifact-number"
            candidate["primary_artifact_region_id"] = "artifact-line"
            candidate["thumbnail_region_id"] = "artifact-line"
            candidate["primary_relation_id"] = "relation-number-of"
            candidate["relation_ids"] = [
                "relation-number-of",
                "relation-color-plate",
                "relation-unrelated",
            ]
        now = datetime.now(UTC)
        self.regions = [
            {
                "_id": "color-caption",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "page": 26,
                "kind": "text",
                "bbox": [0.1, 0.8, 0.3, 0.84],
                "text": "M1:19 彩版九",
                "source": "paddleocr",
                "created_at": now,
            },
            {
                "_id": "body-text",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "page": 132,
                "kind": "text",
                "bbox": [0.1, 0.3, 0.8, 0.39],
                "text": "M1:19 高领，折沿，折肩斜弧腹，圜底。高22厘米。",
                "source": "paddleocr",
                "created_at": now,
            },
            {
                "_id": "artifact-number",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "page": 126,
                "kind": "number",
                "bbox": [0.45, 0.8, 0.55, 0.84],
                "text": "M1:19",
                "source": "yolo",
                "created_at": now,
            },
            {
                "_id": "artifact-line",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "page": 126,
                "kind": "artifact",
                "bbox": [0.35, 0.2, 0.65, 0.75],
                "text": "",
                "source": "yolo",
                "created_at": now,
            },
            {
                "_id": "color-page",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "page": 26,
                "kind": "color_plate",
                "bbox": [0.1, 0.1, 0.9, 0.75],
                "text": "M1:19",
                "source": "ocr_identifier_inference",
                "created_at": now,
            },
            {
                "_id": "unrelated-artifact",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "page": 177,
                "kind": "artifact",
                "bbox": [0.1, 0.1, 0.4, 0.4],
                "text": "M9:9",
                "source": "yolo",
                "created_at": now,
            },
        ]
        self.relations = [
            {
                "_id": "relation-number-of",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "source_region_id": "artifact-number",
                "target_region_id": "artifact-line",
                "relation_type": "number_of",
                "score": 0.95,
                "method": "exact_identifier",
                "created_at": now,
            },
            {
                "_id": "relation-color-plate",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "source_region_id": "color-page",
                "target_region_id": "artifact-line",
                "relation_type": "color_plate_of",
                "score": 0.96,
                "method": "exact_artifact_id_color_page",
                "created_at": now,
            },
            {
                "_id": "relation-unrelated",
                "job_id": "job-m13-8",
                "document_id": "document-1",
                "source_region_id": "body-text",
                "target_region_id": "unrelated-artifact",
                "relation_type": "evidence_for",
                "score": 0.81,
                "method": "broad_entity_match",
                "created_at": now,
            },
        ]

    async def get_entity(self, job_id: str, entity_id: str) -> dict:
        return {
            "_id": entity_id,
            "job_id": job_id,
            "document_id": "document-1",
            "record_ids": [self.record["_id"], self.text_record["_id"]],
            "region_ids": [
                "color-caption",
                "body-text",
                "artifact-number",
                "artifact-line",
                "color-page",
                "unrelated-artifact",
            ],
            "relation_ids": [relation["_id"] for relation in self.relations],
            "associated_pages": [26, 126, 132, 177],
        }

    async def list_records_by_ids(
        self,
        job_id: str,
        record_ids: list[str],
    ) -> list[dict]:
        return [deepcopy(self.record), deepcopy(self.text_record)]

    async def list_relations_by_ids(
        self,
        job_id: str,
        relation_ids: list[str],
    ) -> list[dict]:
        selected = set(relation_ids)
        return deepcopy(
            [relation for relation in self.relations if relation["_id"] in selected]
        )


def test_evidence_context_prefers_the_rich_body_text_page() -> None:
    repository = EntityEvidenceContextRepository()

    response = asyncio.run(
        get_record_evidence_context(
            "job-m13-8",
            "record-m13-8",
            SimpleNamespace(repository=repository),
        )
    )

    assert response.data.record.id == "record-m13-8"
    assert response.data.text_record.id == "record-m1-19-text"
    assert response.data.primary_text_page == 132
    assert response.data.page_numbers == [26, 126, 132]
    assert {relation.id for relation in response.data.relations} == {
        "relation-number-of",
        "relation-color-plate",
    }
    assert "unrelated-artifact" not in {
        region.id for region in response.data.regions
    }


def test_evidence_context_never_uses_color_plate_as_primary_text_page() -> None:
    """Color captions must stay in the third column, not replace the left page."""

    repository = EntityEvidenceContextRepository()
    # Make the selected color-page record look richer than the body-text sibling
    # so score alone would previously prefer page 26.
    repository.record["fields"]["figure_caption"] = {
        "value": "4.玉锥形饰（仲M4：3）",
        "status": "valid",
        "evidence": [
            {
                "page": 26,
                "quote": "4.玉锥形饰（仲M4：3）",
                "bbox": [0.1, 0.8, 0.4, 0.84],
                "region_id": "color-caption",
                "kind": "text",
            }
        ],
    }
    for key in ("category", "texture", "measurements"):
        repository.record["fields"][key] = {
            "value": f"color-{key}",
            "status": "valid",
            "evidence": [
                {
                    "page": 26,
                    "quote": f"color-{key}",
                    "bbox": [0.1, 0.8, 0.4, 0.84],
                    "region_id": "color-caption",
                    "kind": "text",
                }
            ],
        }

    response = asyncio.run(
        get_record_evidence_context(
            "job-m13-8",
            "record-m13-8",
            SimpleNamespace(repository=repository),
        )
    )

    assert response.data.primary_text_page == 132
    assert response.data.primary_text_page != 26
