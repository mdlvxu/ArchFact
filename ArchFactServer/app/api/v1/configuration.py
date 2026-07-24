from typing import Annotated

from fastapi import APIRouter, Body, Depends

from app.api.dependencies import get_container
from app.container import Container
from app.models.schemas import (
    ApiResponse,
    ExtractionTemplateDefinition,
    PostProcessingRuleDefinition,
)

router = APIRouter(tags=["extraction-configuration"])


def build_template(document: dict) -> ExtractionTemplateDefinition:
    return ExtractionTemplateDefinition(
        id=document["_id"],
        name=document["name"],
        fields=document["fields"],
        builtin=document.get("builtin", False),
    )


def build_rule(document: dict) -> PostProcessingRuleDefinition:
    return PostProcessingRuleDefinition(
        id=document["_id"],
        key=document["key"],
        name=document["name"],
        description=document.get("description", ""),
        example=document.get("example", ""),
        handler=document.get("handler", "builtin"),
        enabled=document.get("enabled", True),
        builtin=document.get("builtin", False),
    )


@router.get(
    "/extraction-templates",
    response_model=ApiResponse[list[ExtractionTemplateDefinition]],
)
async def list_extraction_templates(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[ExtractionTemplateDefinition]]:
    templates = await container.configuration_service.list_templates()
    return ApiResponse(data=[build_template(template) for template in templates])


@router.put(
    "/extraction-templates",
    response_model=ApiResponse[list[ExtractionTemplateDefinition]],
)
async def replace_extraction_templates(
    templates: Annotated[list[ExtractionTemplateDefinition], Body(min_length=1, max_length=100)],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[ExtractionTemplateDefinition]]:
    saved = await container.configuration_service.replace_templates(templates)
    return ApiResponse(message="抽取模板已保存", data=[build_template(item) for item in saved])


@router.get(
    "/post-processing-rules",
    response_model=ApiResponse[list[PostProcessingRuleDefinition]],
)
async def list_post_processing_rules(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[PostProcessingRuleDefinition]]:
    rules = await container.configuration_service.list_rules()
    return ApiResponse(data=[build_rule(rule) for rule in rules])


@router.put(
    "/post-processing-rules",
    response_model=ApiResponse[list[PostProcessingRuleDefinition]],
)
async def replace_post_processing_rules(
    rules: Annotated[list[PostProcessingRuleDefinition], Body(max_length=100)],
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[list[PostProcessingRuleDefinition]]:
    saved = await container.configuration_service.replace_rules(rules)
    return ApiResponse(message="后处理规则已保存", data=[build_rule(rule) for rule in saved])
