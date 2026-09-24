import asyncio
from types import SimpleNamespace
from typing import Any

from app.api.v1.configuration import preview_extraction_prompt
from app.models.schemas import ExtractionPromptPreviewRequest, ExtractionTemplateDefinition
from app.services.configuration_service import ConfigurationService


class FakeConfigurationRepository:
    def __init__(self) -> None:
        self.templates: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self.system_prompt: dict[str, Any] | None = None

    async def get_extraction_system_prompt(self) -> dict[str, Any] | None:
        return self.system_prompt

    async def replace_extraction_system_prompt(self, content: str) -> None:
        self.system_prompt = {"_id": "default", "content": content}

    async def count_extraction_templates(self) -> int:
        return len(self.templates)

    async def replace_extraction_templates(self, templates: list[dict[str, Any]]) -> None:
        self.templates = [{"_id": item["id"], **item} for item in templates]

    async def list_extraction_templates(self) -> list[dict[str, Any]]:
        return self.templates

    async def count_post_processing_rules(self) -> int:
        return len(self.rules)

    async def replace_post_processing_rules(self, rules: list[dict[str, Any]]) -> None:
        self.rules = [{"_id": item["id"], **item} for item in rules]

    async def list_post_processing_rules(self) -> list[dict[str, Any]]:
        return self.rules


def test_seeded_configuration_uses_stable_field_and_rule_keys() -> None:
    repository = FakeConfigurationRepository()
    service = ConfigurationService(repository)  # type: ignore[arg-type]

    asyncio.run(service.seed_defaults())

    basic_template = next(item for item in repository.templates if item["id"] == "basic-research")
    assert basic_template["fields"][0]["key"] == "site_id"
    assert all(field["key"] for field in basic_template["fields"])
    assert all(field.get("instruction") for field in basic_template["fields"])
    assert all(field.get("evidence_kind") for field in basic_template["fields"])
    assert any(item["id"] == "latest-artifact-card" for item in repository.templates)
    assert all(
        field["type"] == "string"
        for template in repository.templates
        if template["builtin"]
        for field in template["fields"]
    )
    assert {rule["key"] for rule in repository.rules} >= {
        "chinese_number_to_arabic",
        "date_formatting",
        "unit_standardization",
    }


def test_seed_defaults_refreshes_builtins_and_preserves_custom_templates() -> None:
    repository = FakeConfigurationRepository()
    repository.templates = [
        {
            "_id": "custom-pottery",
            "name": "Custom Pottery",
            "fields": [
                {
                    "key": "ware_type",
                    "label": "Ware Type",
                    "type": "string",
                    "required": False,
                }
            ],
            "builtin": False,
        }
    ]
    service = ConfigurationService(repository)  # type: ignore[arg-type]

    asyncio.run(service.seed_defaults())

    assert any(item["id"] == "custom-pottery" for item in repository.templates)
    basic_template = next(item for item in repository.templates if item["id"] == "basic-research")
    assert basic_template["fields"][0]["instruction"]


def test_seed_defaults_preserves_a_saved_builtin_field_instruction() -> None:
    repository = FakeConfigurationRepository()
    service = ConfigurationService(repository)  # type: ignore[arg-type]

    asyncio.run(service.seed_defaults())
    basic_template = next(item for item in repository.templates if item["id"] == "basic-research")
    basic_template["fields"][0]["instruction"] = "仅提取人工校订后的器物编号。"

    asyncio.run(service.seed_defaults())

    refreshed = next(item for item in repository.templates if item["id"] == "basic-research")
    field = refreshed["fields"][0]
    assert field["instruction"] == "仅提取人工校订后的器物编号。"
    assert field["default_instruction"]


def test_seed_defaults_promotes_existing_basic_template_before_resetting_baseline() -> None:
    repository = FakeConfigurationRepository()
    repository.templates = [
        {
            "_id": "basic-research",
            "name": "Basic Research Template",
            "builtin": True,
            "fields": [
                {
                    "key": "artifact_id",
                    "label": "Artifact ID",
                    "type": "string",
                    "required": False,
                    "instruction": "保留现有生产提示词。",
                    "default_instruction": "旧默认提示词。",
                    "evidence_kind": "number",
                }
            ],
        }
    ]
    service = ConfigurationService(repository)  # type: ignore[arg-type]

    asyncio.run(service.seed_defaults())

    latest = next(item for item in repository.templates if item["id"] == "latest-artifact-card")
    baseline = next(item for item in repository.templates if item["id"] == "basic-research")
    assert latest["fields"][0]["instruction"] == "保留现有生产提示词。"
    assert baseline["fields"][0]["key"] == "site_id"


def test_system_prompt_is_seeded_and_can_be_customized() -> None:
    repository = FakeConfigurationRepository()
    service = ConfigurationService(repository)  # type: ignore[arg-type]

    asyncio.run(service.seed_defaults())
    seeded = asyncio.run(service.get_system_prompt())
    saved = asyncio.run(service.replace_system_prompt("只输出严格 JSON。"))

    assert seeded["content"] == seeded["default_content"]
    assert saved["content"] == "只输出严格 JSON。"
    assert saved["default_content"] == seeded["default_content"]


def test_prompt_preview_reuses_the_runtime_template_contract() -> None:
    repository = FakeConfigurationRepository()
    service = ConfigurationService(repository)  # type: ignore[arg-type]
    template = ExtractionTemplateDefinition.model_validate(
        {
            "id": "preview-template",
            "name": "预览模板",
            "builtin": False,
            "fields": [
                {
                    "key": "artifact_id",
                    "label": "器物编号",
                    "type": "string",
                    "instruction": "逐字提取当前器物编号。",
                }
            ],
        }
    )

    response = asyncio.run(
        preview_extraction_prompt(
            ExtractionPromptPreviewRequest(template=template),
            container=SimpleNamespace(configuration_service=service),
        )
    )

    assert "逐字提取当前器物编号" in response.data.composed_prompt
    assert "archaeological_card_contract" not in response.data.complete_prompt
    assert "当前页 OCR 文本" in response.data.dynamic_content_note
    assert response.data.estimated_tokens > 0
