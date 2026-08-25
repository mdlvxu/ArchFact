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
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.repositories.mongo_repository import MongoRepository
from app.services.gold_dataset_service import normalize_identifier


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    """Coordinates human-first, benchmark-isolated, asynchronous AI verification."""

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
            # Legacy sessions may still be stuck in conflict_review. Freeze using the
            # existing human verdicts instead of requiring another pass on page 2.
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

        job = await self._repository.get_job(job_id)
        dataset = await self._repository.get_gold_dataset_for_document(
            document_id=job["document_id"]
        )
        if dataset is not None and not self._settings.llm_api_key:
            raise DomainError("已绑定人工标注数据，但尚未配置 LLM_API_KEY，无法启动 AI 复核")
        updated, run = await self._repository.create_ai_verification_run(
            job_id=job_id,
            session_id=session_id,
            gold_dataset_id=dataset["_id"] if dataset else None,
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
            dataset = None
            if run.get("gold_dataset_id"):
                dataset = await self._repository.get_gold_dataset(run["gold_dataset_id"])

            async def judge_item(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
                record_id = str(item["record_id"])
                record = record_by_id.get(record_id)
                if record is None:
                    return record_id, self._unavailable_result("当前固定样本记录已失效")
                try:
                    return record_id, await self._judge_record(
                        record=record,
                        dataset=dataset,
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
                        "ai_reason": f"单条 AI 复核失败：{str(exc)[:300]}",
                        "ai_field_results": [],
                        "gold_record_id": None,
                        "gold_match_status": "matched" if dataset else "unavailable",
                        "conflict_resolved": False,
                    }

            tasks = [asyncio.create_task(judge_item(item)) for item in session.get("items", [])]
            results: dict[str, dict[str, Any]] = {}
            total = len(tasks)
            for current, task in enumerate(asyncio.as_completed(tasks), start=1):
                record_id, result = await task
                human_verdict = next(
                    item.get("verdict")
                    for item in session.get("items", [])
                    if str(item.get("record_id")) == record_id
                )
                result["consensus_status"] = self._consensus(
                    human_verdict=human_verdict,
                    ai_verdict=result.get("ai_verdict"),
                    gold_match_status=result.get("gold_match_status"),
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

            updated_session = await self._repository.apply_ai_verification_results(
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
            # Always freeze a version after AI review. Human PASS/FAIL remains authoritative;
            # conflicts are retained in the version report instead of blocking navigation.
            updated_session, version = await self._repository.finalize_verification_session(
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
        dataset: dict[str, Any] | None,
        visual_context: dict[str, Any],
    ) -> dict[str, Any]:
        if dataset is None:
            return self._unavailable_result("当前 PDF 未绑定专属人工标注数据，保留人工结论")
        artifact_id = self._record_artifact_id(record)
        if not artifact_id:
            return self._ambiguous_result("自动结果缺少可匹配的器物编号")
        matches = await self._repository.find_gold_records_by_artifact_id(
            dataset_id=dataset["_id"],
            canonical_artifact_id=artifact_id,
        )
        if not matches:
            return self._ambiguous_result(f"人工标注数据中未找到器物编号 {artifact_id}")
        if len(matches) > 1:
            return self._ambiguous_result(f"人工标注数据中器物编号 {artifact_id} 存在多条记录")

        gold = matches[0]
        assets = await self._repository.get_gold_record_assets(
            dataset_id=dataset["_id"],
            record_id=gold["_id"],
        )
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
        extracted["artifact_id"] = extracted["artifact_id"] or artifact_id
        gold_fields = gold.get("fields", {})
        deterministic = self._deterministic_compare(extracted, gold_fields)
        deterministic.append(
            {
                "field": "artifact_crop",
                "verdict": (
                    "passed"
                    if any(asset.get("asset_type") == "artifact_crop" for asset in assets)
                    and visual_context["artifact_crop_present"]
                    else "uncertain"
                ),
                "reason": "仅校验双方是否具备器物裁剪图；文本模型不判断图像像素相似度",
                "method": "deterministic_presence",
            }
        )
        if any(asset.get("asset_type") == "color_plate" for asset in assets):
            deterministic.append(
                {
                    "field": "color_plate",
                    "verdict": "passed" if visual_context["color_plate_present"] else "failed",
                    "reason": "人工标注数据含彩图引用，检查当前关系链是否关联彩图区域",
                    "method": "deterministic_relation_presence",
                }
            )
        deterministic.append(
            {
                "field": "evidence_relation_chain",
                "verdict": "passed" if visual_context["relation_count"] > 0 else "uncertain",
                "reason": f"当前记录保存了 {visual_context['relation_count']} 条区域关系",
                "method": "deterministic_relation_presence",
            }
        )
        llm = await self._call_llm(
            extracted=extracted,
            gold_fields=gold_fields,
            evidence=self._evidence_quotes(record),
            deterministic=deterministic,
        )
        verdict = str(llm.get("overall_verdict", "uncertain")).lower()
        if verdict not in {"passed", "failed", "uncertain"}:
            verdict = "uncertain"
        hard_failure = any(
            item.get("verdict") == "failed" and item.get("field") == "artifact_id"
            for item in deterministic
        )
        if hard_failure:
            verdict = "failed"
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
            "ai_field_results": [*deterministic, *semantic_results][:50],
            "gold_record_id": gold["_id"],
            "gold_match_status": "matched",
            "conflict_resolved": False,
        }

    async def _call_llm(
        self,
        *,
        extracted: dict[str, Any],
        gold_fields: dict[str, Any],
        evidence: dict[str, list[str]],
        deterministic: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self._settings.verification_llm_model or self._settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是考古器物数据质量复核员。只比较自动抽取结果、OCR原文证据与人工标注数据。"
                        "人工审核结论不会提供给你。不得改写生产数据。语义等价、单位等价和合理OCR纠错可判通过；"
                        "关键信息矛盾、遗漏或无证据推断判不通过；无法确定则判uncertain。只输出JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "extracted": extracted,
                            "gold_standard": gold_fields,
                            "ocr_evidence": evidence,
                            "deterministic_checks": deterministic,
                            "required_output": {
                                "overall_verdict": "passed|failed|uncertain",
                                "confidence": "0..1",
                                "reason": "concise Chinese explanation",
                                "field_results": [
                                    {
                                        "field": "field name",
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
        # Match extraction: DeepSeek thinking models otherwise burn tokens on
        # reasoning and leave message.content empty, which blocks V-version creation.
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
            raise DomainError(f"AI 复核服务返回 HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DomainError("AI 复核服务未返回 choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise DomainError("AI 复核服务返回的 message 无效")
        content = self._message_text(message.get("content"))
        if not content:
            content = self._message_text(message.get("reasoning_content"))
        if not content:
            raise DomainError("AI 复核服务返回空内容，请检查模型与 thinking 配置")
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
    def _deterministic_compare(
        extracted: dict[str, Any],
        gold: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for key in ("artifact_id", "measurements", "figure_caption"):
            actual, expected = extracted.get(key), gold.get(key)
            if actual in (None, "") and expected in (None, ""):
                verdict = "passed"
            elif actual in (None, "") or expected in (None, ""):
                verdict = "failed"
            else:
                verdict = (
                    "passed" if compact_text(actual) == compact_text(expected) else "uncertain"
                )
            results.append(
                {
                    "field": key,
                    "verdict": verdict,
                    "reason": "规范化后直接比较",
                    "method": "deterministic_normalized",
                }
            )
        return results

    @staticmethod
    def _consensus(
        *,
        human_verdict: str,
        ai_verdict: str | None,
        gold_match_status: str | None,
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
            "gold_match_status": "unavailable",
            "conflict_resolved": False,
        }

    @staticmethod
    def _ambiguous_result(reason: str) -> dict[str, Any]:
        return {
            "ai_verdict": "uncertain",
            "ai_confidence": 0.0,
            "ai_reason": reason,
            "ai_field_results": [],
            "gold_record_id": None,
            "gold_match_status": "not_found",
            "conflict_resolved": False,
        }
