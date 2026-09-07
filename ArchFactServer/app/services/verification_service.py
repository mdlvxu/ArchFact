from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ConflictError, DomainError
from app.domain.identifiers import normalize_identifier
from app.domain.time import utc_now
from app.domain.verification_sampling import (
    enabled_rule_payload,
    evaluate_record_rules,
    rule_scopes_from_rules,
)
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.repositories.mongo_repository import MongoRepository


def field_value(record: dict[str, Any], key: str) -> Any:
    field = record.get("fields", {}).get(key, {})
    if isinstance(field, dict):
        return field.get("value", field.get("raw_value"))
    return field


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = text.replace("厘米", "cm").replace("毫米", "mm").replace("米", "m")
    text = re.sub(r"[\s,，。.;；:：、()（）\[\]【】]+", "", text)
    return text


def parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object")
    return parsed


class VerificationService:
    """Human-first verification: 9/9 sample, then DeepSeek comparison only."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: MongoRepository,
        dispatcher: LocalJobDispatcher,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._dispatcher = dispatcher
        self._semaphore = asyncio.Semaphore(settings.verification_llm_max_concurrency)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.verification_llm_timeout_seconds,
                connect=min(10.0, settings.verification_llm_timeout_seconds),
            )
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def complete_or_start(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        session = await self._repository.get_verification_session(
            job_id=job_id,
            session_id=session_id,
        )
        if session.get("status") == "completed" and session.get("version_id"):
            version = await self._repository.get_verification_version(
                job_id=job_id,
                version_id=session["version_id"],
            )
            run = None
            if session.get("ai_run_id"):
                run = await self._repository.get_ai_verification_run(
                    job_id=job_id,
                    run_id=session["ai_run_id"],
                )
            return session, version, run

        if session.get("status") == "conflict_review":
            completed, version = await self._repository.finalize_verification_session(
                job_id=job_id,
                session_id=session_id,
            )
            run = None
            if session.get("ai_run_id"):
                run = await self._repository.update_ai_verification_run(
                    session["ai_run_id"],
                    version_id=version["_id"],
                )
            return completed, version, run

        if session.get("status") == "ai_review":
            run = await self._repository.get_ai_verification_run(
                job_id=job_id,
                run_id=session["ai_run_id"],
            )
            return session, None, run

        unreviewed = [
            item for item in session.get("items", []) if item.get("verdict") == "unreviewed"
        ]
        if unreviewed:
            raise ConflictError(f"还有 {len(unreviewed)} 条样本尚未完成人工核验")

        if not self._settings.llm_api_key:
            raise DomainError("尚未配置 LLM_API_KEY，无法启动 DeepSeek 人机对照")

        updated, run = await self._repository.create_ai_verification_run(
            job_id=job_id,
            session_id=session_id,
            gold_dataset_id=None,
            total=len(session.get("items", [])),
        )
        await self._dispatcher.dispatch(run["_id"])
        return updated, None, run

    async def run(self, run_id: str) -> None:
        run: dict[str, Any] | None = None
        try:
            run = await self._repository.update_ai_verification_run(
                run_id,
                status="running",
                error=None,
            )
            session = await self._repository.get_verification_session(
                job_id=run["job_id"],
                session_id=run["session_id"],
            )
            records = await self._repository.list_verification_session_records(
                job_id=run["job_id"],
                session_id=run["session_id"],
            )
            record_by_id = {str(record["_id"]): record for record in records}
            regions = await self._repository.list_job_regions(run["job_id"])
            region_kind_by_id = {
                str(region["_id"]): region.get("kind", "other") for region in regions
            }
            relations = await self._repository.list_job_relations(run["job_id"])
            relation_by_id = {str(relation["_id"]): relation for relation in relations}
            rules = [rule for rule in session.get("rules", []) if rule.get("enabled", True)]
            artifact_id_counts = self._artifact_id_counts(records)

            async def judge_item(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
                record_id = str(item["record_id"])
                record = record_by_id.get(record_id)
                if record is None:
                    return record_id, self._unavailable_result("当前固定样本记录已失效")
                try:
                    return record_id, await self._judge_record(
                        record=record,
                        rules=rules,
                        expected_label=item.get("expected_label"),
                        artifact_id_counts=artifact_id_counts,
                        visual_context=self._visual_context(
                            record,
                            region_kind_by_id=region_kind_by_id,
                            relation_by_id=relation_by_id,
                        ),
                    )
                except Exception as exc:
                    return record_id, {
                        "ai_verdict": "uncertain",
                        "ai_confidence": 0.0,
                        "ai_reason": f"单条机器校验失败：{str(exc)[:300]}",
                        "ai_field_results": [],
                        "gold_record_id": None,
                        "gold_match_status": None,
                        "conflict_resolved": False,
                    }

            tasks = [asyncio.create_task(judge_item(item)) for item in session.get("items", [])]
            results: dict[str, dict[str, Any]] = {}
            total = len(tasks)
            item_by_id = {
                str(item.get("record_id")): item for item in session.get("items", [])
            }
            for current, task in enumerate(asyncio.as_completed(tasks), start=1):
                record_id, result = await task
                human_verdict = item_by_id.get(record_id, {}).get("verdict")
                result["consensus_status"] = self._consensus(
                    human_verdict=human_verdict,
                    ai_verdict=result.get("ai_verdict"),
                )
                results[record_id] = result
                await self._repository.update_ai_verification_run(
                    run_id,
                    progress={
                        "current": current,
                        "total": total,
                        "percent": round(current / total * 100) if total else 100,
                    },
                )

            await self._repository.apply_ai_verification_results(
                job_id=run["job_id"],
                session_id=run["session_id"],
                run_id=run_id,
                results=results,
            )
            conflict_count = sum(
                result.get("consensus_status") == "conflict" for result in results.values()
            )
            uncertain_count = sum(
                result.get("ai_verdict") == "uncertain" for result in results.values()
            )
            _updated_session, version = await self._repository.finalize_verification_session(
                job_id=run["job_id"],
                session_id=run["session_id"],
            )
            await self._repository.update_ai_verification_run(
                run_id,
                status="completed",
                conflict_count=conflict_count,
                uncertain_count=uncertain_count,
                version_id=version["_id"],
                completed_at=utc_now(),
                progress={"current": total, "total": total, "percent": 100},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if run is not None:
                await self._repository.update_ai_verification_run(
                    run_id,
                    status="failed",
                    error=str(exc)[:1000],
                    completed_at=utc_now(),
                )
                await self._repository.reset_failed_ai_verification(
                    job_id=run["job_id"],
                    session_id=run["session_id"],
                    run_id=run_id,
                )

    async def _judge_record(
        self,
        *,
        record: dict[str, Any],
        rules: list[dict[str, Any]],
        expected_label: str | None,
        artifact_id_counts: dict[str, int],
        visual_context: dict[str, Any],
    ) -> dict[str, Any]:
        extracted = {
            key: field_value(record, key)
            for key in (
                "artifact_id",
                "surface_color",
                "texture",
                "measurements",
                "morphological_description",
                "category",
                "figure_caption",
                "completeness",
            )
        }
        artifact_id = self._record_artifact_id(record)
        extracted["artifact_id"] = extracted["artifact_id"] or artifact_id
        heuristic = [
            {
                "field": item.scope,
                "verdict": item.verdict,
                "reason": item.reason,
                "method": "rule_heuristic",
            }
            for item in evaluate_record_rules(
                record=record,
                rule_scopes=rule_scopes_from_rules(rules),
                artifact_id_counts=artifact_id_counts,
            )
        ]
        heuristic.append(
            {
                "field": "artifact_crop",
                "verdict": "passed" if visual_context["artifact_crop_present"] else "failed",
                "reason": "检查器物卡片是否关联线图或器物裁剪图",
                "method": "deterministic_presence",
            }
        )
        heuristic.append(
            {
                "field": "evidence_relation_chain",
                "verdict": "passed" if visual_context["relation_count"] > 0 else "uncertain",
                "reason": f"当前记录保存了 {visual_context['relation_count']} 条区域关系",
                "method": "deterministic_relation_presence",
            }
        )
        llm = await self._call_llm(
            extracted=extracted,
            rules=enabled_rule_payload(rules),
            evidence=self._evidence_quotes(record),
            heuristic=heuristic,
            visual_context=visual_context,
        )
        verdict = str(llm.get("overall_verdict", "uncertain")).lower()
        if verdict not in {"passed", "failed", "uncertain"}:
            verdict = "uncertain"
        confidence = llm.get("confidence", 0.5)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.5
        semantic_results = llm.get("field_results", [])
        if not isinstance(semantic_results, list):
            semantic_results = []
        semantic_results = [item for item in semantic_results if isinstance(item, dict)]
        return {
            "ai_verdict": verdict,
            "ai_confidence": confidence,
            "ai_reason": str(llm.get("reason", ""))[:1000],
            "ai_field_results": [*heuristic, *semantic_results][:50],
            "gold_record_id": None,
            "gold_match_status": None,
            "expected_label": expected_label,
            "conflict_resolved": False,
        }

    async def _call_llm(
        self,
        *,
        extracted: dict[str, Any],
        rules: list[dict[str, Any]],
        evidence: dict[str, list[str]],
        heuristic: list[dict[str, Any]],
        visual_context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": self._settings.verification_llm_model or self._settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是考古器物卡片机器校验员。只根据操作员在第三页配置的校验规则、"
                        "自动生成的器物卡片、OCR 原文证据和关系/裁剪图是否存在来判断。"
                        "不要使用任何人工金标准标注，也不要猜测人工 PASS/FAIL。"
                        "不得改写生产数据或人工核验结论。"
                        "规则全部满足则 passed；关键字段与规则矛盾、缺失或无证据推断则 failed；"
                        "无法确定则 uncertain。算法启发式检查仅供参考，最终以规则为准。只输出 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "verification_rules": rules,
                            "extracted_card": extracted,
                            "ocr_evidence": evidence,
                            "visual_context": visual_context,
                            "algorithm_heuristic_checks": heuristic,
                            "required_output": {
                                "overall_verdict": "passed|failed|uncertain",
                                "confidence": "0..1",
                                "reason": "concise Chinese explanation",
                                "field_results": [
                                    {
                                        "field": "field or rule name",
                                        "verdict": "passed|failed|uncertain",
                                        "reason": "why",
                                        "method": "semantic_llm",
                                    }
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0,
            "max_tokens": self._settings.verification_llm_max_tokens,
        }
        if self._settings.llm_provider.lower() == "deepseek":
            payload["thinking"] = {
                "type": "enabled" if self._settings.llm_thinking else "disabled"
            }
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._settings.llm_api_base.rstrip('/')}/chat/completions"
        async with self._semaphore:
            response = await self._client.post(url, headers=headers, json=payload)
        if not response.is_success:
            raise DomainError(f"机器校验服务返回 HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DomainError("机器校验服务未返回 choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise DomainError("机器校验服务返回的 message 无效")
        content = self._message_text(message.get("content"))
        if not content:
            content = self._message_text(message.get("reasoning_content"))
        if not content:
            raise DomainError("机器校验服务返回空内容，请检查模型与 thinking 配置")
        return parse_json_object(content)

    @staticmethod
    def _message_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
            return "".join(parts).strip()
        return str(value).strip()

    @staticmethod
    def _record_artifact_id(record: dict[str, Any]) -> str:
        identity = record.get("linkage", {}).get("identity", {})
        candidate = (
            identity.get("artifact_id_normalized")
            or identity.get("artifact_id_raw")
            or field_value(record, "artifact_id")
        )
        return normalize_identifier(candidate)

    @staticmethod
    def _artifact_id_counts(records: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            artifact_id = VerificationService._record_artifact_id(record)
            if not artifact_id:
                continue
            counts[artifact_id] = counts.get(artifact_id, 0) + 1
        return counts

    @staticmethod
    def _evidence_quotes(record: dict[str, Any]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for key, field in record.get("fields", {}).items():
            if not isinstance(field, dict):
                continue
            quotes = [
                str(item.get("quote", "")).strip()
                for item in field.get("evidence", [])
                if isinstance(item, dict) and str(item.get("quote", "")).strip()
            ]
            if quotes:
                result[key] = quotes[:5]
        return result

    @staticmethod
    def _visual_context(
        record: dict[str, Any],
        *,
        region_kind_by_id: dict[str, str],
        relation_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        region_ids = {str(region_id) for region_id in record.get("region_ids", [])}
        kinds = {region_kind_by_id.get(region_id, "other") for region_id in region_ids}
        relation_ids = [
            str(relation_id)
            for relation_id in record.get("relation_ids", [])
            if str(relation_id) in relation_by_id
        ]
        return {
            "artifact_crop_present": bool(kinds & {"artifact", "line_drawing"}),
            "color_plate_present": "color_plate" in kinds,
            "caption_present": "caption" in kinds,
            "number_present": "number" in kinds,
            "relation_count": len(relation_ids),
            "region_kinds": sorted(kinds),
        }

    @staticmethod
    def _consensus(
        *,
        human_verdict: str | None,
        ai_verdict: str | None,
        expected_label: str | None = None,
        gold_match_status: str | None = None,
    ) -> str:
        if gold_match_status == "unavailable":
            return "benchmark_unavailable"
        if ai_verdict in {"passed", "failed"} and human_verdict == ai_verdict:
            return "agreed"
        return "conflict"

    @staticmethod
    def _unavailable_result(reason: str) -> dict[str, Any]:
        return {
            "ai_verdict": "uncertain",
            "ai_confidence": 0.0,
            "ai_reason": reason,
            "ai_field_results": [],
            "gold_record_id": None,
            "gold_match_status": None,
            "conflict_resolved": False,
        }
