import asyncio
from typing import Any

from app.services.configuration_service import ConfigurationService


class FakeConfigurationRepository:
    def __init__(self) -> None:
        self.templates: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []

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
    assert basic_template["fields"][0]["key"] == "artifact_id"
    assert all(field["key"] for field in basic_template["fields"])
    assert all(field.get("instruction") for field in basic_template["fields"])
    assert all(field.get("evidence_kind") for field in basic_template["fields"])
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
