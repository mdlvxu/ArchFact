import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import DomainError
from app.models.schemas import ExtractionConfig, ExtractionFieldSpec
from app.services.extraction_engine import (
    CozeExtractionEngine,
    CozeHttpExtractionEngine,
    LocalTextExtractionEngine,
    OpenAICompatibleExtractionEngine,
    PageChunk,
)


def test_local_engine_returns_stable_record_contract() -> None:
    engine = LocalTextExtractionEngine()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="器物编号", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(
                chunk_id="job:page:1",
                page_no=1,
                text="M12:3，泥质灰陶罐",
                blocks=[],
            ),
            config,
        )
    )

    assert records[0]["record_type"] == "page_text"
    assert records[0]["source_pages"] == [1]
    assert records[0]["fields"]["page_text"]["value"] == "M12:3，泥质灰陶罐"
    assert records[0]["fields"]["page_text"]["evidence"][0]["page"] == 1


def test_local_engine_marks_scanned_page_for_ocr() -> None:
    engine = LocalTextExtractionEngine()
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="器物编号", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(chunk_id="scan", page_no=3, text="", blocks=[]),
            config,
        )
    )

    assert records[0]["fields"]["page_text"]["status"] == "missing"
    assert "OCR" in records[0]["warnings"][0]


def test_coze_adapter_normalizes_missing_fields_and_evidence() -> None:
    engine = object.__new__(CozeExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="器物编号", type="string"),
            ExtractionFieldSpec(key="texture", label="质地", type="string"),
        ],
    )
    chunk = PageChunk(
        chunk_id="job:page:8",
        page_no=8,
        text="M12:3，泥质灰陶罐",
        blocks=[],
    )

    result = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "artifact_id": {
                    "raw_value": "M12:3",
                    "value": "M12:3",
                    "status": "valid",
                    "evidence": [{"page": 8, "quote": "M12:3，泥质灰陶罐", "bbox": None}],
                }
            },
        },
        chunk,
        config,
    )

    assert result["source_pages"] == [8]
    assert result["fields"]["artifact_id"]["value"] == "M12:3"
    assert result["fields"]["texture"]["status"] == "missing"


def test_coze_adapter_resolves_normalized_bbox_from_matching_pdf_block() -> None:
    engine = object.__new__(CozeExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    chunk = PageChunk(
        chunk_id="job:page:4",
        page_no=4,
        text="Artifact M12:3 was recovered from the feature.",
        blocks=[
            {
                "text": "Artifact M12:3 was recovered from the feature.",
                "bbox": [0.1, 0.2, 0.8, 0.3],
            }
        ],
    )

    result = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "artifact_id": {
                    "raw_value": "M12:3",
                    "value": "M12:3",
                    "status": "valid",
                    "evidence": [{"page": 4, "quote": "Artifact M12:3", "bbox": None}],
                }
            },
        },
        chunk,
        config,
    )

    assert result["fields"]["artifact_id"]["evidence"][0]["bbox"] == [0.1, 0.2, 0.8, 0.3]


def test_structured_adapter_grounds_quote_with_ocr_spacing_and_punctuation() -> None:
    engine = object.__new__(CozeExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="measurements", label="Measurements", type="string")],
    )
    source = "口径 15.6 cm，高 13.6 cm"
    chunk = PageChunk(
        chunk_id="job:page:19",
        page_no=19,
        text=source,
        blocks=[{"text": source, "bbox": [0.1, 0.2, 0.8, 0.3]}],
    )

    result = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "measurements": {
                    "value": "口径15.6cm，高13.6cm",
                    "status": "valid",
                    "evidence": [{"page": 19, "quote": "口径15.6cm,高13.6cm"}],
                }
            },
        },
        chunk,
        config,
    )

    evidence = result["fields"]["measurements"]["evidence"][0]
    assert evidence["quote"] == source
    assert evidence["bbox"] == [0.1, 0.2, 0.8, 0.3]
    assert result["fields"]["measurements"]["status"] == "valid"


def test_structured_adapter_keeps_record_when_one_field_evidence_cannot_be_grounded() -> None:
    engine = object.__new__(CozeExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="measurements", label="Measurements", type="string")],
    )
    chunk = PageChunk(
        chunk_id="job:page:19",
        page_no=19,
        text="器物 H125:1",
        blocks=[],
    )

    result = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "measurements": {
                    "value": "口径 15.6 cm",
                    "status": "valid",
                    "evidence": [{"page": 19, "quote": "原文不存在的尺寸"}],
                }
            },
        },
        chunk,
        config,
    )

    assert result["fields"]["measurements"]["value"] == "口径 15.6 cm"
    assert result["fields"]["measurements"]["status"] == "needs_review"
    assert result["fields"]["measurements"]["evidence"] == []
    assert "无法定位" in result["warnings"][0]


def test_llm_prompt_defines_fluent_but_grounded_field_value_policy() -> None:
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="measurements", label="Measurements", type="string"),
            ExtractionFieldSpec(
                key="morphological_description",
                label="Morphological Description",
                type="string",
            ),
        ],
    )
    chunk = PageChunk(
        chunk_id="job:page:19",
        page_no=19,
        text="T3，口径15.6厘米。侈口，弧腹。",
        blocks=[],
    )

    system_prompt = OpenAICompatibleExtractionEngine._system_prompt()
    user_prompt = json.loads(OpenAICompatibleExtractionEngine._user_prompt(chunk, config))

    assert "measurements.value" in system_prompt
    assert "morphological_description.value" in system_prompt
    assert "不得补充原文没有的器物事实" in system_prompt
    assert user_prompt["field_value_policy"]["measurements"]["value"].startswith("整理为简洁的")
    assert "按器物部位" in user_prompt["field_value_policy"]["morphological_description"]["value"]


def test_chunk_merge_discards_empty_or_unidentified_single_field_fragments() -> None:
    engine = object.__new__(OpenAICompatibleExtractionEngine)
    records = [
        {
            "record_type": "artifact",
            "source_pages": [19],
            "fields": {
                "artifact_id": {"value": None},
                "completeness": {"value": None},
            },
        },
        {
            "record_type": "artifact",
            "source_pages": [19],
            "fields": {
                "artifact_id": {"value": None},
                "completeness": {"value": "残"},
            },
        },
        {
            "record_type": "artifact",
            "source_pages": [19],
            "fields": {
                "artifact_id": {"value": None},
                "figure_caption": {"value": "图6"},
            },
        },
    ]

    merged = engine._merge_records(records)

    assert len(merged) == 1
    assert merged[0]["fields"]["figure_caption"]["value"] == "图6"


def test_structured_adapter_derives_internal_link_hints() -> None:
    engine = object.__new__(CozeExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="figure_caption", label="Figure", type="string"),
        ],
    )
    chunk = PageChunk(
        chunk_id="job:page:4",
        page_no=4,
        text="H125:1 is shown in Figure 5-2:85.",
        blocks=[],
    )

    result = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "artifact_id": {
                    "value": "H125:1",
                    "status": "valid",
                    "evidence": [{"page": 4, "quote": "H125:1"}],
                },
                "figure_caption": {
                    "value": "Figure 5-2:85",
                    "status": "valid",
                    "evidence": [{"page": 4, "quote": "Figure 5-2:85"}],
                },
            },
        },
        chunk,
        config,
    )

    assert result["link_hints"]["artifact_ids"] == ["H125:1"]
    assert result["link_hints"]["figure_refs"] == ["Figure 5-2:85"]


def test_structured_adapter_normalizes_system_linkage_and_grounded_hints() -> None:
    engine = object.__new__(CozeExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="figure_caption", label="Figure", type="string"),
        ],
    )
    source_text = "T3:3 见图6，图中序号3。图6 1—4为陶鬲足。"
    chunk = PageChunk(
        chunk_id="job:page:20",
        page_no=20,
        text=source_text,
        blocks=[
            {
                "region_id": "text-link-20",
                "text": source_text,
                "bbox": [0.1, 0.6, 0.8, 0.7],
            }
        ],
    )

    result = engine._normalize_record(
        {
            "record_type": "artifact",
            "linkage": {
                "identity": {
                    "artifact_id_raw": "T3:3",
                    "artifact_id_normalized": "T3:3",
                },
                "visual_link": {
                    "figure_no": "图6",
                    "figure_item_no": "3",
                    "caption_raw": "图6 1—4为陶鬲足",
                    "evidence_block_ids": ["text-link-20", "invented-block"],
                },
            },
            "link_hints": {"aliases": ["不存在的编号"]},
            "fields": {
                "artifact_id": {
                    "raw_value": "T3:3",
                    "value": "T3:3",
                    "status": "valid",
                    "evidence": [{"page": 20, "quote": "T3:3"}],
                },
                "figure_caption": {
                    "raw_value": "图6 1—4为陶鬲足",
                    "value": "图6 1—4为陶鬲足",
                    "status": "valid",
                    "evidence": [{"page": 20, "quote": "图6 1—4为陶鬲足"}],
                },
            },
        },
        chunk,
        config,
    )

    assert result["linkage"]["identity"]["artifact_id_normalized"] == "T3:3"
    assert result["linkage"]["visual_link"]["figure_no"] == "图6"
    assert result["linkage"]["visual_link"]["figure_item_no"] == "3"
    assert result["linkage"]["visual_link"]["evidence_block_ids"] == ["text-link-20"]
    assert result["linkage"]["visual_link"]["evidence"][0]["bbox"] == [0.1, 0.6, 0.8, 0.7]
    assert result["link_hints"]["figure_refs"] == ["图6", "图6-3", "图6:3", "图6 3"]
    assert result["link_hints"]["figure_item_nos"] == ["3"]
    assert result["link_hints"]["caption_texts"] == ["图6 1—4为陶鬲足"]
    assert result["link_hints"]["aliases"] == []


def test_coze_adapter_decodes_output_when_response_contains_run_id() -> None:
    engine = object.__new__(CozeExtractionEngine)

    payload = engine._decode_payload(
        {
            "output": json.dumps(
                {
                    "schema_version": "1.0",
                    "chunk_id": "job:page:1",
                    "records": [],
                }
            ),
            "run_id": "run-123",
        }
    )

    assert payload["chunk_id"] == "job:page:1"
    assert payload["records"] == []


def test_coze_http_engine_calls_deployed_service(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                200,
                json={
                    "output": json_module.dumps(
                        {
                            "schema_version": "1.0",
                            "chunk_id": "job:page:1",
                            "records": [
                                {
                                    "record_type": "artifact",
                                    "source_pages": [1],
                                    "fields": {
                                        "artifact_id": {
                                            "raw_value": "M1:1",
                                            "value": "M1:1",
                                            "status": "valid",
                                            "evidence": [
                                                {
                                                    "page": 1,
                                                    "quote": "Artifact M1:1",
                                                    "bbox": None,
                                                }
                                            ],
                                        }
                                    },
                                    "warnings": [],
                                }
                            ],
                        }
                    ),
                    "run_id": "run-123",
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        extraction_engine="coze_http",
        coze_http_url="https://example.test/run",
        coze_http_token="test-token",
        coze_http_timeout_seconds=180,
    )
    engine = CozeHttpExtractionEngine(settings)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(
                chunk_id="job:page:1",
                page_no=1,
                text="Artifact M1:1",
                blocks=[],
            ),
            config,
        )
    )

    assert captured["url"] == "https://example.test/run"
    assert captured["headers"] == {
        "Authorization": "Bearer test-token",
        "Accept": "application/json",
    }
    request_json = captured["json"]
    assert isinstance(request_json, dict)
    assert request_json["page_no"] == 1
    assert json.loads(str(request_json["schema_json"]))["template_id"] == "basic"
    assert records[0]["fields"]["artifact_id"]["value"] == "M1:1"


def test_coze_http_engine_rejects_unsuccessful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            del kwargs
            return httpx.Response(
                401,
                json={"message": "unauthorized"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        extraction_engine="coze_http",
        coze_http_url="https://example.test/run",
        coze_http_token="invalid-token",
    )
    engine = CozeHttpExtractionEngine(settings)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    with pytest.raises(DomainError) as exc_info:
        asyncio.run(
            engine.extract(
                PageChunk(chunk_id="job:page:1", page_no=1, text="", blocks=[]),
                config,
            )
        )

    assert exc_info.value.code == 5034
    assert exc_info.value.status_code == 502


def test_openai_compatible_engine_sends_ocr_blocks_and_parses_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> httpx.Response:
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json_module.dumps(
                                    {
                                        "schema_version": "1.0",
                                        "chunk_id": "job:page:1",
                                        "records": [
                                            {
                                                "record_type": "artifact",
                                                "link_hints": {
                                                    "artifact_ids": ["H125:1"],
                                                    "figure_refs": [],
                                                    "plate_refs": [],
                                                    "aliases": [],
                                                },
                                                "fields": {
                                                    "artifact_id": {
                                                        "value": "H125:1",
                                                        "status": "valid",
                                                        "evidence": [
                                                            {
                                                                "page": 1,
                                                                "quote": "H125:1",
                                                                "region_id": "reg-1",
                                                            }
                                                        ],
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                )
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        extraction_engine="llm",
        llm_provider="deepseek",
        llm_api_base="https://api.deepseek.com",
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
    )
    engine = OpenAICompatibleExtractionEngine(settings)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(
                chunk_id="job:page:1",
                page_no=1,
                text="H125:1 gray pottery",
                blocks=[
                    {
                        "region_id": "reg-1",
                        "text": "H125:1 gray pottery",
                        "bbox": [0.1, 0.1, 0.4, 0.2],
                    }
                ],
            ),
            config,
        )
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    request_json = captured["json"]
    assert isinstance(request_json, dict)
    assert request_json["response_format"] == {"type": "json_object"}
    assert request_json["thinking"] == {"type": "disabled"}
    user_input = json.loads(request_json["messages"][1]["content"])  # type: ignore[index]
    assert user_input["ocr_blocks"][0]["region_id"] == "reg-1"
    assert records[0]["fields"]["artifact_id"]["value"] == "H125:1"
    assert records[0]["link_hints"]["artifact_ids"] == ["H125:1"]


def test_openai_compatible_engine_uses_groq_payload_without_deepseek_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> httpx.Response:
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json_module.dumps(
                                    {
                                        "schema_version": "1.0",
                                        "chunk_id": "job:page:1",
                                        "records": [
                                            {
                                                "artifact_id": {
                                                    "value": "H125:1",
                                                    "status": "extracted",
                                                    "evidence": [{"quote": "H125:1"}],
                                                }
                                            }
                                        ],
                                        "link_hints": {
                                            "artifact_ids": ["H125:1"],
                                            "figure_refs": [],
                                            "plate_refs": [],
                                            "aliases": [],
                                        },
                                    }
                                )
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        extraction_engine="llm",
        llm_provider="groq",
        llm_api_base="https://api.groq.com/openai/v1",
        llm_api_key="test-key",
        llm_model="qwen/qwen3.6-27b",
    )
    engine = OpenAICompatibleExtractionEngine(settings)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(chunk_id="job:page:1", page_no=1, text="H125:1 gray pottery", blocks=[]),
            config,
        )
    )

    assert records[0]["fields"]["artifact_id"]["value"] == "H125:1"
    assert records[0]["fields"]["artifact_id"]["status"] == "valid"
    assert records[0]["fields"]["artifact_id"]["evidence"][0]["page"] == 1
    assert records[0]["link_hints"]["artifact_ids"] == ["H125:1"]
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    request_json = captured["json"]
    assert isinstance(request_json, dict)
    assert "thinking" not in request_json
    assert request_json["reasoning_format"] == "hidden"
    assert request_json["reasoning_effort"] == "none"


def test_groq_long_ocr_is_split_and_does_not_duplicate_full_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            **kwargs: object,
        ) -> httpx.Response:
            del kwargs
            requests.append(json)
            user_input = json_module.loads(json["messages"][1]["content"])  # type: ignore[index]
            first_text = user_input["ocr_blocks"][0]["text"]
            artifact_id = first_text.split()[0]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json_module.dumps(
                                    {
                                        "schema_version": "1.0",
                                        "chunk_id": user_input["chunk_id"],
                                        "records": [
                                            {
                                                "record_type": "artifact",
                                                "fields": {
                                                    "artifact_id": {
                                                        "value": artifact_id,
                                                        "status": "valid",
                                                        "evidence": [{"quote": artifact_id}],
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                )
                            },
                        }
                    ]
                },
                headers={
                    "x-ratelimit-remaining-tokens": "8000",
                    "x-ratelimit-reset-tokens": "1s",
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        extraction_engine="llm",
        llm_provider="groq",
        llm_api_key="test-key",
        llm_max_tokens=512,
        llm_input_chunk_chars=500,
        llm_chunk_overlap_chars=0,
        llm_request_token_budget=3000,
    )
    engine = OpenAICompatibleExtractionEngine(settings)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    blocks = [{"text": f"H{index}:1 " + "考古器物描述" * 18} for index in range(1, 9)]

    records = asyncio.run(
        engine.extract(
            PageChunk(
                chunk_id="job:page:44",
                page_no=44,
                text="\n".join(block["text"] for block in blocks),
                blocks=blocks,
            ),
            config,
        )
    )

    assert len(requests) > 1
    assert len(records) == len(requests)
    for request in requests:
        user_input = json.loads(request["messages"][1]["content"])  # type: ignore[index]
        assert "ocr_text" not in user_input
        assert sum(len(block["text"]) + 1 for block in user_input["ocr_blocks"]) <= 500


def test_groq_truncated_output_is_bisected_and_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            **kwargs: object,
        ) -> httpx.Response:
            del kwargs
            requests.append(json)
            user_input = json_module.loads(json["messages"][1]["content"])  # type: ignore[index]
            chunk_id = user_input["chunk_id"]
            if ":retry:" not in chunk_id:
                return httpx.Response(
                    200,
                    json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]},
                    request=httpx.Request("POST", url),
                )

            source_text = "\n".join(block["text"] for block in user_input.get("ocr_blocks", []))
            artifact_id = "H1:1" if "H1:1" in source_text else "H2:1"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json_module.dumps(
                                    {
                                        "schema_version": "1.0",
                                        "chunk_id": chunk_id,
                                        "records": [
                                            {
                                                "record_type": "artifact",
                                                "fields": {
                                                    "artifact_id": {
                                                        "value": artifact_id,
                                                        "status": "valid",
                                                        "evidence": [{"quote": artifact_id}],
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                )
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_provider="groq",
            llm_api_key="test-key",
            llm_input_chunk_chars=2000,
            llm_chunk_overlap_chars=0,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(
                chunk_id="job:page:70",
                page_no=70,
                text="H1:1 " + "器物描述" * 80 + "\nH2:1 " + "器物描述" * 80,
                blocks=[
                    {"text": "H1:1 " + "器物描述" * 80},
                    {"text": "H2:1 " + "器物描述" * 80},
                ],
            ),
            config,
        )
    )

    assert len(requests) == 3
    assert {record["fields"]["artifact_id"]["value"] for record in records} == {
        "H1:1",
        "H2:1",
    }


def test_dense_terminal_chunk_retries_with_larger_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_output_tokens: list[int] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            **kwargs: object,
        ) -> httpx.Response:
            del kwargs
            output_tokens = int(json["max_tokens"])
            requested_output_tokens.append(output_tokens)
            user_input = json_module.loads(json["messages"][1]["content"])  # type: ignore[index]
            if output_tokens == 512:
                return httpx.Response(
                    200,
                    json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json_module.dumps(
                                    {
                                        "schema_version": "1.0",
                                        "chunk_id": user_input["chunk_id"],
                                        "records": [
                                            {
                                                "record_type": "artifact",
                                                "fields": {
                                                    "artifact_id": {
                                                        "value": "H20:1",
                                                        "status": "valid",
                                                        "evidence": [{"quote": "H20:1"}],
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                )
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_provider="deepseek",
            llm_api_key="test-key",
            llm_max_tokens=512,
            llm_request_token_budget=5000,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="texture", label="Texture", type="string"),
        ],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(
                chunk_id="job:page:20",
                page_no=20,
                text="H20:1 泥质灰陶",
                blocks=[{"text": "H20:1 泥质灰陶"}],
            ),
            config,
        )
    )

    assert requested_output_tokens == [512, 1024]
    assert records[0]["fields"]["artifact_id"]["value"] == "H20:1"
    assert records[0]["fields"]["texture"]["status"] == "missing"


def test_groq_retries_json_validation_without_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            del timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            **kwargs: object,
        ) -> httpx.Response:
            del kwargs
            requests.append(json)
            if len(requests) == 1:
                return httpx.Response(
                    400,
                    json={"error": {"code": "json_validate_failed"}},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json_module.dumps(
                                    {
                                        "schema_version": "1.0",
                                        "chunk_id": "job:page:46",
                                        "records": [],
                                    }
                                )
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        _env_file=None,
        extraction_engine="llm",
        llm_provider="groq",
        llm_api_key="test-key",
        llm_max_retries=1,
        semantic_candidate_filter_enabled=False,
    )
    engine = OpenAICompatibleExtractionEngine(settings)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    records = asyncio.run(
        engine.extract(
            PageChunk(chunk_id="job:page:46", page_no=46, text="无器物", blocks=[]),
            config,
        )
    )

    assert records == []
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]


def test_llm_chunks_reuse_client_and_respect_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_count = 0
    active_requests = 0
    max_active_requests = 0
    client_closed = False

    class FakeAsyncClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            nonlocal client_count
            del timeout
            client_count += 1

        async def aclose(self) -> None:
            nonlocal client_closed
            client_closed = True

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            **kwargs: object,
        ) -> httpx.Response:
            nonlocal active_requests, max_active_requests
            del kwargs
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            try:
                await asyncio.sleep(0.02)
                user_input = json_module.loads(json["messages"][1]["content"])  # type: ignore[index]
                artifact_id = user_input["ocr_blocks"][0]["text"].split()[0]
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": json_module.dumps(
                                        {
                                            "schema_version": "1.0",
                                            "chunk_id": user_input["chunk_id"],
                                            "records": [
                                                {
                                                    "record_type": "artifact",
                                                    "fields": {
                                                        "artifact_id": {
                                                            "value": artifact_id,
                                                            "status": "valid",
                                                            "evidence": [{"quote": artifact_id}],
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    )
                                },
                            }
                        ]
                    },
                    request=httpx.Request("POST", url),
                )
            finally:
                active_requests -= 1

    json_module = json
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_provider="deepseek",
            llm_api_key="test-key",
            llm_max_concurrency=2,
            llm_max_tokens=512,
            llm_input_chunk_chars=500,
            llm_chunk_overlap_chars=0,
            llm_request_token_budget=3000,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    blocks = [{"text": f"H{index}:1 " + "考古器物形态描述" * 24} for index in range(1, 9)]

    async def run() -> list[dict[str, object]]:
        records = await engine.extract(
            PageChunk(
                chunk_id="job:page:1",
                page_no=1,
                text="\n".join(block["text"] for block in blocks),
                blocks=blocks,
            ),
            config,
        )
        await engine.aclose()
        return records

    records = asyncio.run(run())

    assert len(records) > 1
    assert client_count == 1
    assert max_active_requests == 2
    assert client_closed is True


def test_semantic_chunking_keeps_each_artifact_with_its_following_description() -> None:
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_provider="deepseek",
            llm_api_key="test-key",
            llm_max_tokens=512,
            llm_input_chunk_chars=500,
            llm_chunk_overlap_chars=0,
            llm_request_token_budget=3000,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    blocks = [
        {"text": "本页器物说明" + "前言" * 55},
        {"text": "H125:1 泥质灰陶罐，见图6-3。" + "形态" * 55},
        {"text": "口径 12.5 cm，残高 8.2 cm。" + "尺寸" * 28},
        {"text": "H126:2 夹砂红陶鬲，见彩版8-2。" + "形态" * 55},
        {"text": "口径 18.1 cm，残高 11.6 cm。" + "尺寸" * 28},
    ]

    chunks = engine._split_chunk(
        PageChunk(
            chunk_id="job:page:2",
            page_no=2,
            text="\n".join(block["text"] for block in blocks),
            blocks=blocks,
        ),
        config,
    )

    h125_chunk = next(chunk for chunk in chunks if "H125:1" in chunk.text)
    h126_chunk = next(chunk for chunk in chunks if "H126:2" in chunk.text)
    assert "口径 12.5 cm" in h125_chunk.text
    assert "口径 18.1 cm" in h126_chunk.text
    assert h125_chunk.chunk_id != h126_chunk.chunk_id


def test_chunking_fits_serialized_ocr_block_metadata_into_request_budget() -> None:
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_provider="deepseek",
            llm_api_key="test-key",
            llm_max_tokens=512,
            llm_input_chunk_chars=10000,
            llm_chunk_overlap_chars=0,
            llm_request_token_budget=5000,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    blocks = [
        {
            "region_id": f"job:page:172:paddleocr:{index}:" + "region-metadata-" * 8,
            "text": f"M13:{index} 玉器",
        }
        for index in range(1, 166)
    ]

    chunks = engine._split_chunk(
        PageChunk(
            chunk_id="job:page:172",
            page_no=172,
            text="\n".join(str(block["text"]) for block in blocks),
            blocks=blocks,
        ),
        config,
    )

    assert len(chunks) > 1
    assert all(engine._requested_tokens(chunk, config) <= 5000 for chunk in chunks)
    assert [block["text"] for chunk in chunks for block in chunk.blocks] == [
        block["text"] for block in blocks
    ]


def test_compact_llm_fields_expand_to_existing_record_contract() -> None:
    engine = object.__new__(OpenAICompatibleExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[
            ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string"),
            ExtractionFieldSpec(key="measurements", label="Measurements", type="string"),
        ],
    )
    chunk = PageChunk(
        chunk_id="job:page:20",
        page_no=20,
        text="M3:4 石铲，残长 12.3 厘米。",
        blocks=[
            {
                "region_id": "text-20-1",
                "text": "M3:4 石铲，残长 12.3 厘米。",
                "bbox": [0.1, 0.2, 0.8, 0.3],
            }
        ],
    )

    record = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "artifact_id": {"v": "M3:4", "q": "M3:4", "rid": "text-20-1"},
                "measurements": {
                    "v": "残长 12.3 cm",
                    "raw": "残长 12.3 厘米",
                    "q": "残长 12.3 厘米",
                },
            },
        },
        chunk,
        config,
    )

    assert record["fields"]["artifact_id"]["value"] == "M3:4"
    assert record["fields"]["artifact_id"]["evidence"][0]["region_id"] == "text-20-1"
    assert record["fields"]["measurements"]["raw_value"] == "残长 12.3 厘米"
    assert record["fields"]["measurements"]["evidence"][0]["page"] == 20


def test_llm_evidence_region_id_accepts_single_item_arrays() -> None:
    engine = object.__new__(OpenAICompatibleExtractionEngine)
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    chunk = PageChunk(
        chunk_id="job:page:146",
        page_no=146,
        text="M3:4 石铲",
        blocks=[
            {
                "region_id": "reg-current-146-4",
                "text": "M3:4 石铲",
                "bbox": [0.1, 0.2, 0.4, 0.3],
            }
        ],
    )

    record = engine._normalize_record(
        {
            "record_type": "artifact",
            "fields": {
                "artifact_id": {
                    "value": "M3:4",
                    "raw_value": "M3:4",
                    "status": "valid",
                    "evidence": [
                        {
                            "quote": "M3:4",
                            "region_id": ["reg-current-146-4"],
                        }
                    ],
                }
            },
        },
        chunk,
        config,
    )

    evidence = record["fields"]["artifact_id"]["evidence"][0]
    assert evidence["region_id"] == "reg-current-146-4"
    assert evidence["bbox"] == [0.1, 0.2, 0.4, 0.3]


def test_candidate_router_skips_prose_but_keeps_numbered_artifacts() -> None:
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_api_key="test-key",
            semantic_candidate_filter_enabled=True,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )

    prose_chunks = engine._split_chunk(
        PageChunk(
            chunk_id="job:page:1",
            page_no=1,
            text="本章介绍遗址地层和发掘经过，不含器物登记信息。",
            blocks=[{"text": "本章介绍遗址地层和发掘经过，不含器物登记信息。"}],
        ),
        config,
    )
    artifact_chunks = engine._split_chunk(
        PageChunk(
            chunk_id="job:page:2",
            page_no=2,
            text="M3:4 石铲，残长 12.3 厘米。",
            blocks=[{"text": "M3:4 石铲，残长 12.3 厘米。"}],
        ),
        config,
    )

    assert prose_chunks == []
    assert len(artifact_chunks) == 1


def test_output_aware_chunking_caps_artifacts_per_request() -> None:
    engine = OpenAICompatibleExtractionEngine(
        Settings(
            _env_file=None,
            extraction_engine="llm",
            llm_api_key="test-key",
            llm_max_records_per_chunk=3,
            llm_max_tokens=4096,
            llm_input_chunk_chars=10000,
            llm_request_token_budget=10000,
        )
    )
    config = ExtractionConfig(
        template_id="basic",
        template_name="Basic",
        fields=[ExtractionFieldSpec(key="artifact_id", label="Artifact ID", type="string")],
    )
    blocks = [{"text": f"M3:{index} 陶器"} for index in range(1, 11)]

    chunks = engine._split_chunk(
        PageChunk(
            chunk_id="job:page:3",
            page_no=3,
            text="\n".join(block["text"] for block in blocks),
            blocks=blocks,
        ),
        config,
    )

    assert len(chunks) == 4
    assert all(engine._segment_candidate_count(chunk.blocks) <= 3 for chunk in chunks)
