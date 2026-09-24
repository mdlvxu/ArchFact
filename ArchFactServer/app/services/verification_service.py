from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
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


_CATALOG_FIELD_WEIGHTS: dict[str, int] = {
    "artifact_id": 3,
    "category": 4,
    "material": 6,
    "surface_color": 6,
    "texture": 6,
    "surface_treatment": 6,
    "measurements": 10,
    "morphological_description": 12,
    "figure_caption": 1,
}


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


_ASSERTION_BASELINES: dict[str, dict[str, Any]] = {
    "v1": {
        "id": "v1",
        "name": "LLM 断言 V1",
        "rules": [
            {
                "id": "baseline-v1-source",
                "title": "V1 原文一致性",
                "description": (
                    "逐条核对尺寸、材质、器型、残损描述与颜色是否能由对应 OCR 原文明确支持；"
                    "任一维度不满足即 FAIL。"
                ),
                "enabled": True,
                "source": "baseline",
            },
            {
                "id": "baseline-v1-id",
                "title": "V1 器物 ID 唯一性",
                "description": "同一器物唯一 ID 重复即 FAIL；无重复即 PASS。",
                "enabled": True,
                "source": "baseline",
            },
        ],
    },
    "v2": {
        "id": "v2",
        "name": "LLM 断言 V2",
        "rules": [
            {
                "id": "baseline-v2-source",
                "title": "V2 原文一致性与空值边界",
                "description": (
                    "尺寸有明确数值或量化范围时必须一致；原文无尺寸时，‘无/未见/无明确记载’可通过。"
                    "材质须有明确原文依据；器型须有明确器型名称，只有大类不足。"
                    "残损不得遗漏核心残损信息。颜色原文无记载时，‘无/未见/未提及’可通过；"
                    "原文有明确颜色而提取缺失、错误或明显不符即 FAIL。"
                ),
                "enabled": True,
                "source": "baseline",
            },
            {
                "id": "baseline-v2-id",
                "title": "V2 器物 ID 唯一性",
                "description": "同一器物唯一 ID 重复即 FAIL；无重复即 PASS。",
                "enabled": True,
                "source": "baseline",
            },
            {
                "id": "baseline-v2-reference",
                "title": "V2 图注关联验证",
                "description": (
                    "仅对原文或卡片要求图注的器物适用。图注编号、图序或器物号与已定位的 PDF "
                    "关系明确冲突即 FAIL；"
                    "没有图注要求时为不适用，不得判 FAIL。"
                ),
                "enabled": True,
                "source": "baseline",
            },
        ],
    },
}

# 版本编号是实验设计的一部分：V1 必须使用断言 V1，V2 必须使用断言 V2。
# 新增 V3 时，只需在这里和 _ASSERTION_BASELINES 同时登记即可开放。
_BASELINE_BY_VERIFICATION_VERSION = {1: "v1", 2: "v2"}


class VerificationService:
    """Full machine validation calibrated by one fixed human-reviewed sample."""

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

    async def start_machine_verification(
        self,
        *,
        job_id: str,
        rules: list[dict[str, Any]],
        sample_size: int,
        assertion_baseline_id: str = "v1",
    ) -> dict[str, Any]:
        """Start a full run; later assertion changes reuse the completed human sample."""

        experiment = await self._repository.get_active_verification_experiment(job_id)
        active_session = await self._repository.get_active_verification_session(
            job_id, experiment_id=experiment["_id"]
        )
        if active_session is not None:
            raise ConflictError("当前仍有一组人工核验样本未完成，请先完成或关闭该核验")
        if not self._settings.llm_api_key:
            raise DomainError("尚未配置 LLM_API_KEY，无法启动全量机器校验")

        versions = await self._repository.list_verification_versions(
            job_id, experiment_id=experiment["_id"]
        )
        target_version = max((int(item.get("version", 0)) for item in versions), default=0) + 1
        latest = max(versions, key=lambda item: int(item.get("version", 0)), default=None)
        # Older verification versions were sampled before a full machine run and
        # have no baseline to calibrate. They must create a new fixed sample once.
        has_machine_sample = bool(
            latest
            and any(item.get("machine_verdict") for item in latest.get("items", []))
        )
        expected_baseline_id = _BASELINE_BY_VERIFICATION_VERSION.get(target_version)
        if expected_baseline_id is None:
            raise DomainError(
                f"V{target_version} 尚未配置对应的 LLM 断言基准，当前仅支持 V1 与 V2"
            )
        if assertion_baseline_id != expected_baseline_id:
            raise DomainError(
                f"V{target_version} 必须使用 LLM 断言 "
                f"{expected_baseline_id.upper()}，不能手动替换断言基准"
            )
        baseline = self._assertion_baseline(expected_baseline_id)
        effective_rules = self._effective_rules(baseline=baseline, selected_rules=rules)
        run = await self._repository.create_machine_verification_run(
            job_id=job_id,
            rules=effective_rules,
            sample_size=sample_size,
            mode="recheck" if has_machine_sample else "initial",
            target_version=target_version,
            assertion_baseline=baseline,
            selected_rules=rules,
            experiment=experiment,
        )
        if run.get("status") == "queued":
            await self._dispatcher.dispatch(run["_id"])
        return run

    @staticmethod
    def _assertion_baseline(baseline_id: str) -> dict[str, Any]:
        baseline = _ASSERTION_BASELINES.get(baseline_id)
        if baseline is None:
            raise DomainError("未知的 LLM 断言基准，请选择 V1 或 V2")
        return {
            **baseline,
            "rules": [dict(rule) for rule in baseline["rules"]],
        }

    @staticmethod
    def _effective_rules(
        *,
        baseline: dict[str, Any],
        selected_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist the exact baseline plus user-selected rule snapshot."""
        seen: set[tuple[str, str]] = set()
        effective: list[dict[str, Any]] = []
        for source, rules in (("baseline", baseline["rules"]), ("selected", selected_rules)):
            for rule in rules:
                title = str(rule.get("title", "")).strip()
                description = str(rule.get("description", "")).strip()
                marker = (title.casefold(), description.casefold())
                if not title or marker in seen:
                    continue
                seen.add(marker)
                effective.append({**rule, "source": source})
        return effective

    async def pause_machine_verification(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        await self._repository.pause_machine_verification_run(
            job_id=job_id,
            run_id=run_id,
        )
        await self._dispatcher.cancel(run_id)
        return await self._repository.get_machine_verification_run(
            job_id=job_id,
            run_id=run_id,
        )

    async def resume_machine_verification(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self._repository.resume_machine_verification_run(
            job_id=job_id,
            run_id=run_id,
        )
        await self._dispatcher.dispatch(run_id)
        return run

    async def terminate_machine_verification(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Stop one unfinished execution without touching completed versions or cohorts."""

        await self._repository.terminate_machine_verification_run(
            job_id=job_id,
            run_id=run_id,
        )
        await self._dispatcher.cancel(run_id)
        # The dispatcher waits for cancellation cleanup. Delete only afterwards so
        # no in-flight old-rule result can be reused by the next execution.
        await self._repository.clear_machine_verification_items(job_id=job_id, run_id=run_id)
        return await self._repository.get_machine_verification_run(
            job_id=job_id,
            run_id=run_id,
        )

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

        updated, run = await self._repository.create_ai_verification_run(
            job_id=job_id,
            session_id=session_id,
            gold_dataset_id=None,
            total=len(session.get("items", [])),
        )
        await self._dispatcher.dispatch(run["_id"])
        return updated, None, run

    async def run(self, run_id: str) -> None:
        if run_id.startswith("machine_verify_"):
            await self._run_machine_verification(run_id)
            return

        # Human labels are evaluation truth only.  The initial full blind run is
        # already complete when this job starts, so no model call or calibrated
        # rerun is allowed here.
        await self._run_sample_metric_finalization(run_id)
        return

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
                        human_review={
                            "verdict": item.get("verdict"),
                            "failure_code": item.get("failure_code"),
                            "failure_reason": item.get("failure_reason"),
                        },
                        initial_machine_verdict=item.get("machine_verdict"),
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
                sample_item = item_by_id.get(record_id, {})
                human_verdict = sample_item.get("verdict")
                result["consensus_status"] = self._consensus(
                    human_verdict=human_verdict,
                    # The calibration result must compare the original full-run
                    # verdict with the human decision. The follow-up LLM is only
                    # used to explain that discrepancy field by field.
                    ai_verdict=sample_item.get("machine_verdict")
                    or result.get("ai_verdict"),
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
            calibration_profile = await self._build_calibration_profile(
                session=session,
                records=record_by_id,
                ai_results=results,
            )
            full_run = await self._repository.create_machine_verification_run(
                job_id=run["job_id"],
                rules=session.get("rules", []),
                sample_size=session.get("sample_count", 18),
                mode="calibrated",
                target_version=session["target_version"],
                calibration_profile=calibration_profile,
            )

            async def update_full_progress(current: int, full_total: int) -> None:
                overall_total = total + full_total + 1
                await self._repository.update_ai_verification_run(
                    run_id,
                    progress={
                        "current": total + 1 + current,
                        "total": overall_total,
                        "percent": round((total + 1 + current) / overall_total * 100),
                    },
                )

            await self._repository.update_ai_verification_run(
                run_id,
                progress={
                    "current": total + 1,
                    "total": total + 1,
                    "percent": 100,
                },
            )
            await self._run_machine_verification(
                full_run["_id"],
                progress_callback=update_full_progress,
            )
            full_run = await self._repository.get_machine_verification_run(
                job_id=run["job_id"],
                run_id=full_run["_id"],
            )
            if full_run.get("status") != "completed":
                raise DomainError(full_run.get("error") or "人工校准后的全量复核失败")
            _updated_session, version = (
                await self._repository.finalize_calibrated_verification_session(
                    job_id=run["job_id"],
                    session_id=run["session_id"],
                    run_id=full_run["_id"],
                    calibration_profile=calibration_profile,
                )
            )
            await self._repository.update_ai_verification_run(
                run_id,
                status="completed",
                conflict_count=conflict_count,
                uncertain_count=uncertain_count,
                version_id=version["_id"],
                completed_at=utc_now(),
                progress={
                    "current": total + full_run.get("total_artifacts", 0) + 1,
                    "total": total + full_run.get("total_artifacts", 0) + 1,
                    "percent": 100,
                },
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

    async def _run_sample_metric_finalization(self, run_id: str) -> None:
        """Freeze V1 using the fixed human sample and the stored blind results."""
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
            machine_run_id = session.get("machine_run_id")
            if not machine_run_id:
                raise DomainError("缺少首次全量盲测结果，无法计算样本一致性")
            machine_items = await self._repository.list_machine_verification_items(machine_run_id)
            machine_by_record = {str(item["record_id"]): item for item in machine_items}

            results: dict[str, dict[str, Any]] = {}
            for item in session.get("items", []):
                record_id = str(item.get("record_id"))
                machine = machine_by_record.get(record_id)
                if machine is None:
                    results[record_id] = self._unavailable_result("固定样本不在首次全量盲测结果中")
                    continue
                machine_verdict = str(machine.get("verdict", "uncertain"))
                results[record_id] = {
                    "ai_verdict": machine_verdict,
                    "ai_confidence": machine.get("confidence", 0.0),
                    "ai_reason": "使用首次全量盲测结论与人工审核结果计算样本一致性",
                    "ai_field_results": machine.get("field_results", []),
                    "gold_record_id": None,
                    "gold_match_status": None,
                    "consensus_status": self._consensus(
                        human_verdict=item.get("verdict"),
                        ai_verdict=machine_verdict,
                    ),
                    "conflict_resolved": False,
                }

            await self._repository.apply_ai_verification_results(
                job_id=run["job_id"],
                session_id=run["session_id"],
                run_id=run_id,
                results=results,
            )
            completed, version = await self._repository.finalize_verification_session(
                job_id=run["job_id"],
                session_id=run["session_id"],
            )
            conflict_count = sum(
                result.get("consensus_status") == "conflict" for result in results.values()
            )
            uncertain_count = sum(
                result.get("ai_verdict") == "uncertain" for result in results.values()
            )
            await self._repository.update_ai_verification_run(
                run_id,
                status="completed",
                conflict_count=conflict_count,
                uncertain_count=uncertain_count,
                version_id=version["_id"],
                completed_at=utc_now(),
                progress={"current": len(results), "total": len(results), "percent": 100},
            )
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
            raise

    async def _run_machine_verification(
        self,
        run_id: str,
        *,
        progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> None:
        """Run every assertion, then open the fixed sample or recheck it."""

        run: dict[str, Any] | None = None
        tasks: list[asyncio.Task[tuple[str, dict[str, Any]]]] = []
        pending_results: list[dict[str, Any]] = []
        try:
            run = await self._repository.update_machine_verification_run(
                run_id,
                status="running",
                error=None,
            )
            job_id = run["job_id"]
            stored_records = await self._repository.list_job_records(job_id)
            all_records = self._records_for_machine_verification(stored_records)
            if not all_records:
                raise DomainError("当前任务没有关联器物裁剪图的器物卡片，无法执行机器校验")
            regions = await self._repository.list_job_regions(job_id)
            relations = await self._repository.list_job_relations(job_id)
            region_kind_by_id = {
                str(region["_id"]): region.get("kind", "other") for region in regions
            }
            relation_by_id = {str(relation["_id"]): relation for relation in relations}
            rules = [rule for rule in run.get("rules", []) if rule.get("enabled", True)]
            artifact_id_counts = self._artifact_id_counts(all_records)
            existing_items = await self._repository.list_machine_verification_items(run_id)
            processed_record_ids = {str(item.get("record_id")) for item in existing_items}
            records = [
                record for record in all_records if str(record["_id"]) not in processed_record_ids
            ]

            async def judge(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
                record_id = str(record["_id"])
                try:
                    result = await self._judge_record(
                        record=record,
                        rules=rules,
                        expected_label=None,
                        artifact_id_counts=artifact_id_counts,
                        visual_context=self._visual_context(
                            record,
                            region_kind_by_id=region_kind_by_id,
                            relation_by_id=relation_by_id,
                        ),
                        calibration_profile=run.get("calibration_profile"),
                    )
                except Exception as exc:
                    result = self._unavailable_result(f"单条机器校验失败：{str(exc)[:300]}")
                verdict = str(result.get("ai_verdict", "uncertain"))
                # A transport/provider failure is not a judgement about an artifact.
                # In particular, never turn e.g. a 402 balance error into a false FAIL.
                if (
                    run.get("evaluation_mode") == "binary_fail_closed"
                    and verdict == "uncertain"
                    and not result.get("model_unavailable", False)
                ):
                    verdict = "failed"
                    result["ai_verdict"] = verdict
                    result["ai_reason"] = (
                        f"{result.get('ai_reason', '')}；实验模式下不确定结果按 FAIL 计入复核"
                    )[:1000]
                return record_id, {
                    "record_id": record_id,
                    "verdict": verdict,
                    "confidence": result.get("ai_confidence", 0.0),
                    "reason": result.get("ai_reason", ""),
                    "field_results": result.get("ai_field_results", []),
                    "model_unavailable": bool(result.get("model_unavailable", False)),
                }

            total = len(all_records)
            completed_before_resume = len(processed_record_ids)
            await self._repository.update_machine_verification_run(
                run_id,
                total_artifacts=total,
                progress={
                    "current": completed_before_resume,
                    "total": total,
                    "percent": round(completed_before_resume / total * 100),
                },
            )
            tasks = [asyncio.create_task(judge(record)) for record in records]
            for current, task in enumerate(asyncio.as_completed(tasks), start=1):
                _record_id, result = await task
                pending_results.append(result)
                completed = completed_before_resume + current
                if current == len(tasks) or len(pending_results) >= 5:
                    await self._repository.replace_machine_verification_items(
                        job_id=job_id,
                        run_id=run_id,
                        items=pending_results,
                    )
                    pending_results = []
                    await self._repository.update_machine_verification_run(
                        run_id,
                        progress={
                            "current": completed,
                            "total": total,
                            "percent": round(completed / total * 100),
                        },
                    )
                    if progress_callback is not None:
                        await progress_callback(completed, total)

            results = await self._repository.list_machine_verification_items(run_id)
            pass_count = sum(item["verdict"] == "passed" for item in results)
            fail_count = sum(item["verdict"] == "failed" for item in results)
            uncertain_count = sum(item["verdict"] == "uncertain" for item in results)
            unavailable_items = [item for item in results if item.get("model_unavailable")]
            unavailable_count = len(unavailable_items)
            unavailable_reason = str(unavailable_items[0].get("reason", ""))[:1000] if unavailable_items else None
            run = await self._repository.update_machine_verification_run(
                run_id,
                pass_count=pass_count,
                fail_count=fail_count,
                uncertain_count=uncertain_count,
                model_unavailable_count=unavailable_count,
                model_unavailable_reason=unavailable_reason,
                progress={"current": total, "total": total, "percent": 100},
            )
            # A systemic LLM/provider outage means this run is not an experiment result.
            # Keep the diagnostic items, but do not create a cohort, session, or version.
            # At least 20% (and therefore every item for tiny runs) is treated as systemic.
            if unavailable_count and unavailable_count * 5 >= total:
                raise DomainError(
                    "模型校验服务不可用："
                    f"{unavailable_count}/{total} 条器物未获得模型判断。"
                    "本次结果未生成 18 条人工样本或校验版本；请检查模型账号、余额或服务连接后重新执行。"
                )
            if run.get("mode") == "initial":
                session = await self._repository.create_initial_session_from_machine_run(
                    job_id=job_id,
                    run_id=run_id,
                )
                await self._repository.update_machine_verification_run(
                    run_id,
                    status="completed",
                    session_id=session["_id"],
                    completed_at=utc_now(),
                )
            elif run.get("mode") == "calibrated":
                await self._repository.update_machine_verification_run(
                    run_id,
                    status="completed",
                    completed_at=utc_now(),
                )
            else:
                await self._repository.finalize_recheck_from_machine_run(
                    job_id=job_id,
                    run_id=run_id,
                )
        except asyncio.CancelledError:
            if run is not None:
                if pending_results:
                    await self._repository.replace_machine_verification_items(
                        job_id=run["job_id"],
                        run_id=run_id,
                        items=pending_results,
                    )
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                current = await self._repository.get_machine_verification_run(
                    job_id=run["job_id"],
                    run_id=run_id,
                )
                if current.get("status") != "terminated":
                    await self._repository.update_machine_verification_run(
                        run_id,
                        status="paused",
                        pause_requested=False,
                        error=None,
                    )
            raise
        except Exception as exc:
            if run is not None:
                await self._repository.update_machine_verification_run(
                    run_id,
                    status="failed",
                    error=str(exc)[:1000],
                    completed_at=utc_now(),
                )

    async def _build_calibration_profile(
        self,
        *,
        session: dict[str, Any],
        records: dict[str, dict[str, Any]],
        ai_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Turn the fixed human sample into a compact, reusable model instruction."""

        examples = []
        for item in session.get("items", []):
            record_id = str(item.get("record_id"))
            record = records.get(record_id, {})
            examples.append(
                {
                    "human_verdict": item.get("verdict"),
                    "human_failure_code": item.get("failure_code"),
                    "human_failure_reason": str(item.get("failure_reason", ""))[:300],
                    "initial_machine_verdict": item.get("machine_verdict"),
                    "machine_explanation": str(
                        ai_results.get(record_id, {}).get("ai_reason", "")
                    )[:300],
                    "card": {
                        key: str(field_value(record, key) or "")[:240]
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
                    },
                    "ocr_evidence": {
                        key: values[:2]
                        for key, values in self._evidence_quotes(record).items()
                    },
                }
            )
        fallback = {
            "source": "deterministic_human_sample",
            "sample_size": len(examples),
            "instructions": [
                "以人工审核通过/不通过结论为边界案例，保持与原始 OCR 证据一致。",
                "没有文本或图像关系证据时，不把推测当作通过。",
            ],
            "examples": examples,
        }
        try:
            profile = await self._call_calibration_llm(
                rules=enabled_rule_payload(session.get("rules", [])),
                examples=examples,
            )
            if not isinstance(profile, dict):
                return fallback
            return {
                "source": "deepseek_human_calibration",
                "sample_size": len(examples),
                "instructions": [
                    str(value)[:500]
                    for value in profile.get("instructions", [])
                    if str(value).strip()
                ][:12]
                or fallback["instructions"],
                "failure_taxonomy": profile.get("failure_taxonomy", {}),
                "examples": examples,
            }
        except Exception:
            # A transient profile call must not discard the already-completed human work.
            return fallback

    async def _call_calibration_llm(
        self,
        *,
        rules: list[dict[str, Any]],
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self._settings.verification_llm_model or self._settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是考古器物卡片校验策略专家。根据固定人工审核样本，"
                        "提炼可用于后续全量复核的边界规则。不得编造事实，不得改写人工结论。"
                        "只输出 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "verification_rules": rules,
                            "human_reviewed_examples": examples,
                            "required_output": {
                                "instructions": ["最多 12 条简洁中文校验准则"],
                                "failure_taxonomy": {
                                    "field_or_rule": "如何依据 OCR/关系证据判断失败"
                                },
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
            raise DomainError(f"人工校准服务返回 HTTP {response.status_code}")
        choices = response.json().get("choices")
        if not isinstance(choices, list) or not choices:
            raise DomainError("人工校准服务未返回 choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = self._message_text((message or {}).get("content"))
        if not content:
            raise DomainError("人工校准服务返回空内容")
        return parse_json_object(content)

    async def _judge_record(
        self,
        *,
        record: dict[str, Any],
        rules: list[dict[str, Any]],
        expected_label: str | None,
        artifact_id_counts: dict[str, int],
        visual_context: dict[str, Any],
        human_review: dict[str, Any] | None = None,
        initial_machine_verdict: str | None = None,
        calibration_profile: dict[str, Any] | None = None,
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
            self._rule_check_payload(item)
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
                "failure_code": "artifact_crop_missing",
                "causes_overall_failure": not visual_context["artifact_crop_present"],
            }
        )
        heuristic.append(
            {
                "field": "evidence_relation_chain",
                "verdict": "passed" if visual_context["relation_count"] > 0 else "uncertain",
                "reason": f"当前记录保存了 {visual_context['relation_count']} 条区域关系",
                "method": "deterministic_relation_presence",
                "causes_overall_failure": False,
            }
        )
        llm = await self._call_llm(
            extracted=extracted,
            rules=enabled_rule_payload(rules),
            evidence=self._evidence_quotes(record),
            heuristic=heuristic,
            visual_context=visual_context,
            human_review=human_review,
            initial_machine_verdict=initial_machine_verdict,
            calibration_profile=calibration_profile,
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
        semantic_results = [
            self._semantic_result_payload(item, overall_verdict=verdict)
            for item in semantic_results
            if isinstance(item, dict)
        ]
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
        human_review: dict[str, Any] | None = None,
        initial_machine_verdict: str | None = None,
        calibration_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": self._settings.verification_llm_model or self._settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是考古器物卡片机器校验员。只根据操作员在第三页配置的校验规则、"
                        "自动生成的器物卡片、OCR 原文证据和关系/裁剪图是否存在来判断。"
                        "不得改写生产数据或人工核验结论。"
                        "规则全部满足则 passed；关键字段与规则存在可证实的矛盾或明确缺失则 failed；"
                        "OCR 证据不足、跨页图注暂未定位或关系链不完整，"
                        "但未出现冲突时必须为 uncertain，不能直接判 failed。"
                        "不得仅因序号、图注或裁剪图存在而假定其匹配正确或错误；只有给出明确冲突证据时，才可判定关系冲突。"
                        "算法启发式检查仅供参考，最终以规则为准。"
                        "只输出 JSON。"
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
                            "human_review_reference": human_review,
                            "initial_machine_verdict": initial_machine_verdict,
                            "human_calibration_profile": (
                                {
                                    key: value
                                    for key, value in calibration_profile.items()
                                    if key != "examples"
                                }
                                if calibration_profile
                                else None
                            ),
                            "calibration_instruction": (
                                "若提供人工核验结论，它是不可改写的参考真值；"
                                "请解释机器首次判断与人工结论是否一致，不要推翻人工结论。"
                                if human_review
                                else (
                                    "若提供人工校准档案，请将其视为本次全量复核的判定准则；"
                                    "它来自固定人工样本，优先用于统一边界情况的判定。"
                                    if calibration_profile
                                    else "本次为首次机器校验，不包含人工结论。"
                                )
                            ),
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
                                        "failure_code": (
                                            "artifact_id_missing|artifact_id_duplicate|artifact_id_evidence_conflict|"
                                            "sequence_crop_conflict|figure_caption_missing|figure_caption_evidence_conflict|"
                                            "color_plate_relation_conflict|artifact_crop_missing|text_evidence_conflict|"
                                            "structured_measurements_error|structured_classification_error|"
                                            "structured_field_evidence_conflict"
                                        ),
                                        "causes_overall_failure": (
                                            "boolean; true only when this field directly causes "
                                            "overall failed"
                                        ),
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
            raise DomainError(
                f"机器校验服务返回 HTTP {response.status_code}: {response.text[:300]}"
            )
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
    def _has_field_value(field: Any) -> bool:
        if not isinstance(field, dict):
            return field is not None and field != ""
        value = field.get("value", field.get("raw_value"))
        if value is None or value == "":
            return False
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    @classmethod
    def _catalog_representative_score(cls, record: dict[str, Any]) -> int:
        """Keep the richest page-level record when an entity spans several pages."""

        score = 0
        for field_key, field in record.get("fields", {}).items():
            if not cls._has_field_value(field):
                continue
            score += _CATALOG_FIELD_WEIGHTS.get(field_key, 2)
            if isinstance(field, dict) and any(
                isinstance(evidence, dict) and evidence.get("kind", "text") == "text"
                for evidence in field.get("evidence", [])
            ):
                score += 1
        return score

    @classmethod
    def _records_for_machine_verification(
        cls,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Use the same crop-bound, entity-level scope as the preview catalog.

        Text-only records remain in storage for OCR provenance and rematching, but
        are not cards and therefore must not inflate full-validation totals or the
        18-record human-review cohort.
        """

        crop_bound_entity_ids = {
            str(record.get("entity_id"))
            for record in records
            if record.get("entity_id")
            and (record.get("primary_artifact_region_id") or record.get("thumbnail_region_id"))
        }
        representatives: dict[str, tuple[int, int, dict[str, Any]]] = {}
        for index, record in enumerate(records):
            entity_id = record.get("entity_id")
            has_own_crop = bool(
                record.get("primary_artifact_region_id") or record.get("thumbnail_region_id")
            )
            has_entity_crop = bool(entity_id and str(entity_id) in crop_bound_entity_ids)
            if not has_own_crop and not has_entity_crop:
                continue

            key = f"entity:{entity_id}" if entity_id else f"record:{record.get('_id', index)}"
            candidate = (cls._catalog_representative_score(record), index, record)
            current = representatives.get(key)
            # The stable page-order tie break makes repeated runs deterministic.
            if current is None or candidate[0] > current[0]:
                representatives[key] = candidate

        return [
            candidate[2]
            for candidate in sorted(representatives.values(), key=lambda item: item[1])
        ]

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
    def _rule_check_payload(item: Any) -> dict[str, Any]:
        """Give deterministic checks a stable code and causal attribution."""
        scope = str(item.scope)
        reason = str(item.reason)
        failure_codes = {
            "figure": "figure_caption_missing",
            "measurements": "structured_measurements_error",
            "classification": "structured_classification_error",
        }
        if scope == "identifier":
            failure_code = "artifact_id_duplicate" if "重复" in reason else "artifact_id_missing"
        else:
            failure_code = failure_codes.get(scope)
        return {
            "field": scope,
            "verdict": item.verdict,
            "reason": reason,
            "method": "rule_heuristic",
            "failure_code": failure_code,
            "causes_overall_failure": item.verdict == "failed",
        }

    @staticmethod
    def _semantic_result_payload(
        result: dict[str, Any], *, overall_verdict: str
    ) -> dict[str, Any]:
        """Constrain model diagnostics to auditable categories.

        The model remains responsible for semantic judgment, but it must not
        create a reporting category from arbitrary wording. A failed field on a
        passed card is retained as a review signal, not a failure cause.
        """
        normalized = dict(result)
        verdict = str(normalized.get("verdict", "uncertain")).lower()
        if verdict not in {"passed", "failed", "uncertain"}:
            verdict = "uncertain"
        normalized["verdict"] = verdict
        normalized["method"] = str(normalized.get("method") or "semantic_llm")

        raw_cause = normalized.get("causes_overall_failure")
        if isinstance(raw_cause, str):
            raw_cause = raw_cause.strip().lower() == "true"
        normalized["causes_overall_failure"] = bool(raw_cause) if raw_cause is not None else (
            overall_verdict == "failed" and verdict == "failed"
        )
        if overall_verdict != "failed" or verdict != "failed":
            normalized["causes_overall_failure"] = False

        allowed_codes = {
            "artifact_id_missing",
            "artifact_id_duplicate",
            "artifact_id_evidence_conflict",
            "sequence_crop_conflict",
            "figure_caption_missing",
            "figure_caption_evidence_conflict",
            "color_plate_relation_conflict",
            "artifact_crop_missing",
            "text_evidence_conflict",
            "structured_measurements_error",
            "structured_classification_error",
            "structured_field_evidence_conflict",
        }
        code = str(normalized.get("failure_code", "")).strip()
        if code not in allowed_codes:
            field = str(normalized.get("field", "")).strip().lower()
            aliases = {
                "identifier": "artifact_id_evidence_conflict",
                "artifact_id": "artifact_id_evidence_conflict",
                "sequence_crop_relation": "sequence_crop_conflict",
                "number_crop_relation": "sequence_crop_conflict",
                "figure": "figure_caption_evidence_conflict",
                "figure_caption": "figure_caption_evidence_conflict",
                "caption": "figure_caption_evidence_conflict",
                "color_plate_relation": "color_plate_relation_conflict",
                "artifact_crop": "artifact_crop_missing",
                "text_evidence": "text_evidence_conflict",
                "measurements": "structured_measurements_error",
                "classification": "structured_classification_error",
            }
            code = aliases.get(field, "structured_field_evidence_conflict")
        normalized["failure_code"] = code
        return normalized

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
        relation_types = {
            str(relation_by_id[relation_id].get("relation_type", ""))
            for relation_id in relation_ids
        }
        return {
            "artifact_crop_present": bool(kinds & {"artifact", "line_drawing"}),
            "color_plate_present": "color_plate" in kinds,
            "caption_present": "caption" in kinds,
            "number_present": "number" in kinds,
            "relation_count": len(relation_ids),
            "relation_types": sorted(item for item in relation_types if item),
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
            "model_unavailable": True,
        }
