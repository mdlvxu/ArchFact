from typing import Any

from app.core.errors import DomainError
from app.models.schemas import ExtractionTemplateDefinition, PostProcessingRuleDefinition
from app.repositories.mongo_repository import MongoRepository

DEFAULT_TEMPLATES = [
    {
        "id": "basic-research",
        "name": "Basic Research Template",
        "builtin": True,
        "fields": [
            {
                "key": "artifact_id",
                "label": "Artifact ID",
                "type": "string",
                "instruction": (
                    "只提取原文明确出现的器物或遗迹编号（如 H125:1、M3:18、T3:3）；"
                    "保留冒号等标点，不得把图号或图中序号当作器物编号。"
                ),
                "evidence_kind": "number",
            },
            {
                "key": "surface_color",
                "label": "Surface Color",
                "type": "string",
                "instruction": "只提取原文明示的表面颜色，不得根据黑白线图推断颜色。",
                "evidence_kind": "text",
            },
            {
                "key": "texture",
                "label": "Texture",
                "type": "string",
                "instruction": "提取原文明示的质地、胎质或材质，不得从器形或颜色推断。",
                "evidence_kind": "text",
            },
            {
                "key": "measurements",
                "label": "Measurements",
                "type": "string",
                "instruction": (
                    "提取与当前器物明确对应的高、口径、底径、宽、厚等尺寸；"
                    "raw_value 逐字保留尺寸相关原文；value 按‘部位 数值 单位’整理，"
                    "多个尺寸使用中文分号分隔，可统一空格、标点和单位，但不得推算缺失尺寸，"
                    "也不得混入遗迹范围、探方或沟槽尺寸；存在歧义时标记 needs_review。"
                ),
                "evidence_kind": "text",
            },
            {
                "key": "morphological_description",
                "label": "Morphological Description",
                "type": "string",
                "instruction": (
                    "提取当前器物的口、颈、腹、底、足、纹饰等形态描述；"
                    "raw_value 保留原始 OCR 片段；value 可去除无关内容、修复断行并补充标点，"
                    "按器物部位的逻辑顺序重组为通顺中文；只能纠正上下文能够唯一确定的 OCR 错字，"
                    "不得合并同页其他器物的描述或增加原文没有的器形特征。"
                ),
                "evidence_kind": "text",
            },
            {
                "key": "category",
                "label": "Category",
                "type": "string",
                "instruction": (
                    "提取原文明示的器物类别；疑似 OCR 错字时 raw_value 保留原文，"
                    "value 可给出有依据的规范名称，并标记 needs_review。"
                ),
                "evidence_kind": "text",
            },
            {
                "key": "figure_caption",
                "label": "Figure Caption",
                "type": "string",
                "instruction": (
                    "逐字提取包含图号、图中序号或器物编号的原始图注；"
                    "不得用概括后的器物描述替代图注。"
                ),
                "evidence_kind": "caption",
            },
            {
                "key": "completeness",
                "label": "Completeness",
                "type": "string",
                "instruction": "只提取原文明示的完整、残、残片、复原或比例信息，不得根据线图推断。",
                "evidence_kind": "text",
            },
        ],
    },
    {
        "id": "typology-research",
        "name": "Typology Research Template",
        "builtin": True,
        "fields": [
            {
                "key": "artifact_id",
                "label": "Artifact ID",
                "type": "string",
                "evidence_kind": "number",
            },
            {"key": "category", "label": "Category", "type": "string"},
            {"key": "type", "label": "Type", "type": "string"},
            {"key": "subtype", "label": "Subtype", "type": "string"},
            {"key": "shape", "label": "Shape", "type": "string"},
            {"key": "texture", "label": "Texture", "type": "string"},
            {"key": "measurements", "label": "Measurements", "type": "string"},
        ],
    },
    {
        "id": "stratigraphic-research",
        "name": "Stratigraphic Research Template",
        "builtin": True,
        "fields": [
            {"key": "context_id", "label": "Context ID", "type": "string"},
            {"key": "layer", "label": "Layer", "type": "string"},
            {"key": "unit", "label": "Unit", "type": "string"},
            {"key": "depth", "label": "Depth", "type": "number"},
            {"key": "period", "label": "Period", "type": "string"},
            {"key": "relationship", "label": "Relationship", "type": "string"},
            {"key": "finds_summary", "label": "Finds Summary", "type": "string"},
        ],
    },
    {
        "id": "artifact-restoration",
        "name": "Artifact Restoration Template",
        "builtin": True,
        "fields": [
            {
                "key": "artifact_id",
                "label": "Artifact ID",
                "type": "string",
                "evidence_kind": "number",
            },
            {"key": "material", "label": "Material", "type": "string"},
            {"key": "damage", "label": "Damage", "type": "string"},
            {"key": "completeness", "label": "Completeness", "type": "string"},
            {"key": "repair_history", "label": "Repair History", "type": "string"},
            {"key": "restoration_plan", "label": "Restoration Plan", "type": "string"},
        ],
    },
]

DEFAULT_RULES = [
    {
        "id": "chinese-number-to-arabic",
        "key": "chinese_number_to_arabic",
        "name": "Chinese to Arabic Number Conversion",
        "description": "Automatically convert Chinese numbers to Arabic numerals.",
        "example": "“一百” to “100”",
        "handler": "builtin",
        "enabled": True,
        "builtin": True,
    },
    {
        "id": "unit-standardization",
        "key": "unit_standardization",
        "name": "Unit Standardization",
        "description": "Format units to a consistent standard.",
        "example": "“厘米” or “公分” both become “cm”",
        "handler": "builtin",
        "enabled": True,
        "builtin": True,
    },
    {
        "id": "punctuation-normalization",
        "key": "punctuation_normalization",
        "name": "Punctuation Normalization",
        "description": "Standardize punctuation from full-width to half-width.",
        "example": "“Hello！” to “Hello!”",
        "handler": "builtin",
        "enabled": True,
        "builtin": True,
    },
    {
        "id": "space-removal",
        "key": "space_removal",
        "name": "Space Removal",
        "description": "Eliminate extra spaces to ensure cleaner extracted text.",
        "example": "“Artifact   A” to “Artifact A”",
        "handler": "builtin",
        "enabled": False,
        "builtin": True,
    },
    {
        "id": "date-formatting",
        "key": "date_formatting",
        "name": "Date and Time Formatting",
        "description": "Standardize common Chinese date formats as ISO dates.",
        "example": "“2026年7月15日” to “2026-07-15”",
        "handler": "builtin",
        "enabled": False,
        "builtin": True,
    },
]


class ConfigurationService:
    def __init__(self, repository: MongoRepository) -> None:
        self._repository = repository

    async def seed_defaults(self) -> None:
        default_templates = [
            ExtractionTemplateDefinition.model_validate(item) for item in DEFAULT_TEMPLATES
        ]
        existing_templates = await self._repository.list_extraction_templates()
        default_ids = {template.id for template in default_templates}
        custom_templates = [
            ExtractionTemplateDefinition.model_validate(
                {
                    "id": item["_id"],
                    "name": item["name"],
                    "fields": item["fields"],
                    "builtin": item.get("builtin", False),
                }
            )
            for item in existing_templates
            if item.get("_id") not in default_ids
        ]
        await self.replace_templates([*default_templates, *custom_templates])
        if await self._repository.count_post_processing_rules() == 0:
            await self.replace_rules(
                [PostProcessingRuleDefinition.model_validate(item) for item in DEFAULT_RULES]
            )

    async def list_templates(self) -> list[dict[str, Any]]:
        return await self._repository.list_extraction_templates()

    async def replace_templates(
        self, templates: list[ExtractionTemplateDefinition]
    ) -> list[dict[str, Any]]:
        self._ensure_unique([template.id for template in templates], "模板 id")
        await self._repository.replace_extraction_templates(
            [template.model_dump(mode="json") for template in templates]
        )
        return await self.list_templates()

    async def list_rules(self) -> list[dict[str, Any]]:
        return await self._repository.list_post_processing_rules()

    async def replace_rules(
        self, rules: list[PostProcessingRuleDefinition]
    ) -> list[dict[str, Any]]:
        self._ensure_unique([rule.id for rule in rules], "规则 id")
        self._ensure_unique([rule.key for rule in rules], "规则 key")
        await self._repository.replace_post_processing_rules(
            [rule.model_dump(mode="json") for rule in rules]
        )
        return await self.list_rules()

    @staticmethod
    def _ensure_unique(values: list[str], label: str) -> None:
        if len(values) != len(set(values)):
            raise DomainError(f"{label} 不能重复", code=4091, status_code=409)
