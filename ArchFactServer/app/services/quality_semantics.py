"""LLM-backed semantic comparison of free-text artifact descriptions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import DomainError

SEMANTIC_FIELDS = {"morphological_description", "notes"}


async def score_semantic_pairs(
    settings: Settings, pairs: list[dict[str, Any]],
    checkpoint: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return an explicit semantic judgment for every non-empty text pair.

    Missing or malformed model output fails the run; it must not silently turn
    into a character-similarity score presented as semantic evaluation.
    """
    if not pairs:
        return {}
    if not settings.llm_api_key:
        raise DomainError("语义评测需要配置 LLM_API_KEY；未执行字符相似度替代")
    chunks = [pairs[index : index + 8] for index in range(0, len(pairs), 8)]
    semaphore = asyncio.Semaphore(min(settings.llm_max_concurrency, 3))
    timeout = httpx.Timeout(settings.llm_timeout_seconds)
    url = f"{settings.llm_api_base.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=timeout) as client:

        async def score_chunk(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
            payload = {
                "model": settings.verification_llm_model or settings.llm_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是考古器物提取质量评测员。比较人工标注与机器提取的自由文本，"
                            "只判断事实语义是否一致；允许措辞、语序、标点不同，不能忽略"
                            "材质、形制、部位、数量等实质矛盾。只返回 JSON 对象，格式："
                            '{"results":[{"id":"原id","score":0到1,"matched":true或false,"reason":"简短理由"}]}。'
                            "每个输入必须恰好返回一个结果，不得编造缺失内容。"
                        ),
                    },
                    {"role": "user", "content": json.dumps({"pairs": chunk}, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "stream": False,
                "temperature": 0,
            }
            if settings.llm_provider.lower() == "deepseek":
                payload["thinking"] = {"type": "disabled"}
            async with semaphore:
                if checkpoint is not None:
                    await checkpoint()
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                )
            if not response.is_success:
                raise DomainError(f"语义评测服务返回 HTTP {response.status_code}")
            try:
                content = response.json()["choices"][0]["message"]["content"]
                result = json.loads(content)
                returned = result["results"]
                expected_ids = {str(pair["id"]) for pair in chunk}
                if (
                    not isinstance(returned, list)
                    or {str(item["id"]) for item in returned} != expected_ids
                    or len(returned) != len(chunk)
                ):
                    raise ValueError("missing or duplicate pair IDs")
                for item in returned:
                    score = item["score"]
                    if (
                        not isinstance(score, (int, float))
                        or not 0 <= score <= 1
                        or not isinstance(item["matched"], bool)
                    ):
                        raise ValueError("invalid semantic score")
                return returned
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DomainError("语义评测返回格式无效，未保存不完整评测结果") from exc

        results = await asyncio.gather(*(score_chunk(chunk) for chunk in chunks))
    return {str(item["id"]): item for chunk in results for item in chunk}
