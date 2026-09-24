import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends

from app.api.dependencies import get_container
from app.container import Container
from app.models.schemas import (
    ApiResponse,
    ExtractionConfig,
    ExtractionPromptPreviewRequest,
    ExtractionPromptPreviewView,
    ExtractionSystemPromptUpdate,
    ExtractionSystemPromptView,
    ExtractionTemplateDefinition,
    PostProcessingRuleDefinition,
)
from app.services.extraction_engine import OpenAICompatibleExtractionEngine, PageChunk

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
    "/extraction-system-prompt",
    response_model=ApiResponse[ExtractionSystemPromptView],
)
async def get_extraction_system_prompt(
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionSystemPromptView]:
    return ApiResponse(
        data=ExtractionSystemPromptView.model_validate(
            await container.configuration_service.get_system_prompt()
        )
    )


@router.put(
    "/extraction-system-prompt",
    response_model=ApiResponse[ExtractionSystemPromptView],
)
async def replace_extraction_system_prompt(
    payload: ExtractionSystemPromptUpdate,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionSystemPromptView]:
    saved = await container.configuration_service.replace_system_prompt(payload.content)
    return ApiResponse(
        message="系统公共提示词已保存",
        data=ExtractionSystemPromptView.model_validate(saved),
    )


@router.post(
    "/extraction-prompt-preview",
    response_model=ApiResponse[ExtractionPromptPreviewView],
)
async def preview_extraction_prompt(
    payload: ExtractionPromptPreviewRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ApiResponse[ExtractionPromptPreviewView]:
    """Build the same static prompt contract used for an extraction request."""

    template = payload.template
    system_prompt = await container.configuration_service.get_system_prompt()
    config = ExtractionConfig(
        template_id=template.id,
        template_name=template.name,
        fields=template.fields,
        system_prompt=system_prompt["content"],
    )
    preview_chunk = PageChunk(
        chunk_id="template-preview",
        page_no=0,
        text="[执行时注入当前页 OCR 文本与坐标块]",
        blocks=[],
    )
    public_prompt = OpenAICompatibleExtractionEngine._system_prompt(config)
    user_prompt = OpenAICompatibleExtractionEngine._user_prompt(preview_chunk, config)
    field_lines = []
    for index, field in enumerate(template.fields, start=1):
        instruction = (field.instruction or "未配置字段专用提示词，将仅使用公共抽取规则。").strip()
        field_lines.append(f"{index}. {field.label}\n{instruction}")
    composed_prompt = "\n\n".join(
        [
            f"模板：{template.name}",
            "字段组合提示词：",
            "\n\n".join(field_lines),
            "公共关联规则：图号、图内序号、彩版号与彩版序号分别提取；"
            "无 OCR 证据时不得猜测图文关系。",
        ]
    )
    complete_prompt = (
        f"[系统公共提示词]\n{public_prompt}\n\n"
        f"[模板运行提示词，OCR 内容以占位符展示]\n"
        f"{json.dumps(json.loads(user_prompt), ensure_ascii=False, indent=2)}"
    )
    estimated_tokens = OpenAICompatibleExtractionEngine._estimate_tokens(complete_prompt)
    return ApiResponse(
        data=ExtractionPromptPreviewView(
            template_id=template.id,
            template_name=template.name,
            composed_prompt=composed_prompt,
            complete_prompt=complete_prompt,
            estimated_tokens=estimated_tokens,
            dynamic_content_note="实际执行时会在模板运行提示词中注入当前页 OCR 文本与坐标块。",
        )
    )


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
