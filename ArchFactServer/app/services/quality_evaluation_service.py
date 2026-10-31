from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from app.core.config import Settings
from app.core.errors import ConflictError, DomainError
from app.domain.identifiers import clean_value, normalize_identifier, normalize_report_name
from app.domain.time import utc_now
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.repositories.mongo_repository import MongoRepository
from app.services.gold_dataset_service import baseline_lock
from app.services.quality_catalog import preview_field_value, select_preview_cards
from app.services.quality_semantics import SEMANTIC_FIELDS, score_semantic_pairs

FIELD_LABELS = {
    "artifact_id": "器物编号",
    "measurements": "尺寸",
    "morphological_description": "形态描述",
    "surface_color": "表面颜色",
    "artifact_group": "器物组",
    "category": "类别",
    "type": "型别",
    "subtype": "式别",
    "texture": "质地",
    "completeness": "完整度",
    "figure_caption": "图注",
    "stratigraphy": "地层",
    "notes": "备注",
}

REGION_KINDS = ("artifact", "number", "caption", "grave_drawing", "group")
OCR_ANCHOR_FIELDS = ("artifact_id", "measurements", "figure_caption")


def extracted_field_value(record: dict[str, Any], key: str) -> Any:
    field = record.get("fields", {}).get(key)
    if isinstance(field, dict):
        return field.get("value", field.get("raw_value"))
    return field


def record_artifact_id(record: dict[str, Any]) -> str:
    identity = record.get("linkage", {}).get("identity", {})
    candidate = (
        identity.get("artifact_id_normalized")
        or identity.get("artifact_id_raw")
        or extracted_field_value(record, "artifact_id")
    )
    return normalize_identifier(candidate)


def linked_color_plate_region_id(
    record: dict[str, Any],
    region_by_id: dict[str, dict[str, Any]],
    relation_by_id: dict[str, dict[str, Any]],
) -> str | None:
    region_ids = [str(value) for value in record.get("region_ids") or []]
    for relation_id in record.get("relation_ids") or []:
        relation = relation_by_id.get(str(relation_id))
        if relation:
            region_ids.extend(str(relation.get(key) or "") for key in (
                "source_region_id", "target_region_id"
            ))
    return next(
        (region_id for region_id in region_ids
         if region_by_id.get(region_id, {}).get("kind") == "color_plate"),
        None,
    )


def compact_text(value: Any) -> str:
    cleaned = clean_value(value)
    if cleaned is None:
        return ""
    text = unicodedata.normalize("NFKC", cleaned).lower()
    text = text.replace("厘米", "cm").replace("公分", "cm").replace("毫米", "mm").replace("米", "m")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff.]+", "", text)


def text_similarity(actual: Any, expected: Any) -> float:
    left, right = compact_text(actual), compact_text(expected)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        shorter, longer = min(len(left), len(right)), max(len(left), len(right))
        return max(0.9, shorter / longer)
    return SequenceMatcher(None, left, right).ratio()


def _measurement_tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("厘米", "cm").replace("公分", "cm").replace("毫米", "mm")
    tokens = set()
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(cm|mm|m)?", text):
        amount = Decimal(number)
        if unit == "mm":
            amount /= 10
            unit = "cm"
        elif unit == "m":
            amount *= 100
            unit = "cm"
        tokens.add(f"{amount.normalize()}{unit}")
    return tokens


def _measurement_values(value: Any) -> list[tuple[Decimal, str | None]]:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("厘米", "cm").replace("公分", "cm").replace("毫米", "mm")
    return [
        (Decimal(number), unit or None)
        for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(cm|mm|m)?", text)
    ]


def _measurement_overlap(expected: Any, actual: Any) -> float:
    expected_values = _measurement_values(expected)
    actual_values = _measurement_values(actual)
    if not expected_values:
        return 0.0
    matched_actual: set[int] = set()
    matched = 0
    for expected_value, expected_unit in expected_values:
        for index, (actual_value, actual_unit) in enumerate(actual_values):
            if index in matched_actual:
                continue
            if expected_unit and actual_unit:
                expected_token = _measurement_tokens(f"{expected_value}{expected_unit}")
                actual_token = _measurement_tokens(f"{actual_value}{actual_unit}")
                equal = expected_token == actual_token
            else:
                equal = expected_value == actual_value
            if equal:
                matched_actual.add(index)
                matched += 1
                break
    return matched / len(expected_values)


def _figure_reference_tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text).replace("：", ":").replace("，", ",")
    return {
        match.group(0).casefold()
        for match in re.finditer(
            r"(?:图|彩版|彩图|图版|figure|fig\.?|plate)"
            r"[一二三四五六七八九十百〇零0-9A-Za-z-]+(?:[,.:][一二三四五六七八九十百〇零0-9A-Za-z-]+)*",
            text,
            re.IGNORECASE,
        )
    }


def compare_field(key: str, actual: Any, expected: Any) -> dict[str, Any]:
    actual_text, expected_text = compact_text(actual), compact_text(expected)
    if not expected_text:
        # The baseline is sparse; an unannotated field is not a negative label.
        verdict, score = "not_applicable", 0.0
    elif not actual_text:
        verdict, score = "missing", 0.0
    elif key == "measurements":
        if _measurement_values(expected):
            overlap = _measurement_overlap(expected, actual)
            score = overlap
            verdict = "matched" if math.isclose(overlap, 1.0) else "mismatched"
        else:
            score = text_similarity(actual, expected)
            verdict = "matched" if score >= 0.9 else "mismatched"
    elif key == "figure_caption" and _figure_reference_tokens(expected):
        expected_tokens = _figure_reference_tokens(expected)
        actual_tokens = _figure_reference_tokens(actual)
        score = len(expected_tokens & actual_tokens) / len(expected_tokens)
        verdict = "matched" if math.isclose(score, 1.0) else "mismatched"
    else:
        score = text_similarity(actual, expected)
        threshold = 1.0 if key == "artifact_id" else 0.85
        verdict = "matched" if score >= threshold else "mismatched"
    return {
        "key": key,
        "label": FIELD_LABELS.get(key, key),
        "verdict": verdict,
        "score": round(score, 4),
        "exact": bool(actual_text and actual_text == expected_text),
        "actual": actual,
        "expected": expected,
    }


def bbox_iou(left: list[float], right: list[float]) -> float:
    if len(left) != 4 or len(right) != 4:
        return 0.0
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def detection_metric(
    *,
    kind: str,
    predicted: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    candidates: list[tuple[float, int, int]] = []
    for pred_index, pred in enumerate(predicted):
        for gold_index, target in enumerate(gold):
            if pred.get("page") != target.get("page"):
                continue
            score = bbox_iou(pred.get("bbox", []), target.get("bbox", []))
            if score >= iou_threshold:
                candidates.append((score, pred_index, gold_index))
    matched_predicted: set[int] = set()
    matched_gold: set[int] = set()
    ious: list[float] = []
    for score, pred_index, gold_index in sorted(candidates, reverse=True):
        if pred_index in matched_predicted or gold_index in matched_gold:
            continue
        matched_predicted.add(pred_index)
        matched_gold.add(gold_index)
        ious.append(score)
    matched = len(ious)
    precision = matched / len(predicted) if predicted else (1.0 if not gold else 0.0)
    recall = matched / len(gold) if gold else (1.0 if not predicted else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "kind": kind,
        "predicted_count": len(predicted),
        "gold_count": len(gold),
        "matched_count": matched,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "mean_iou": round(sum(ious) / len(ious), 4) if ious else 0.0,
        "iou_threshold": iou_threshold,
    }


class QualityEvaluationService:
    """Runs deterministic quality evaluation without changing production records."""

    def __init__(
        self,
        *,
        repository: MongoRepository,
        dispatcher: LocalJobDispatcher,
        settings: Settings | None = None,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher
        self._settings = settings
        self._control_locks: dict[str, asyncio.Lock] = {}

    def _control_lock(self, run_id: str) -> asyncio.Lock:
        return self._control_locks.setdefault(run_id, asyncio.Lock())

    async def control(self, job_id: str, run_id: str, action: str) -> dict[str, Any]:
        async with self._control_lock(run_id):
            run = await self._repository.get_quality_evaluation_run(
                job_id=job_id, evaluation_id=run_id
            )
            transitions = {
                "pause": ({"queued", "running"}, "paused", "已暂停评测；已发出的请求可能继续完成"),
                "resume": ({"paused"}, "running", "已继续评测"),
                "terminate": (
                    {"queued", "running", "paused"}, "terminated", "评测已终止，可重新开始",
                ),
            }
            allowed, state, message = transitions[action]
            if run["status"] not in allowed:
                raise ConflictError("当前评测状态不支持此操作")
            fields: dict[str, Any] = {
                "status": state,
                "logs": [*run.get("logs", []), {
                    "at": utc_now().isoformat(), "stage": state, "message": message,
                }],
            }
            if action == "terminate":
                fields["completed_at"] = utc_now()
            updated = await self._repository.update_quality_evaluation_run(run_id, **fields)
        if action == "terminate":
            await self._dispatcher.cancel(run_id)
        return updated

    async def _checkpoint(self, run_id: str) -> None:
        while True:
            run = await self._repository.get_quality_evaluation_run_by_id(run_id)
            if run["status"] == "terminated":
                raise asyncio.CancelledError()
            if run["status"] != "paused":
                return
            await asyncio.sleep(0.25)

    async def create(
        self,
        *,
        job_id: str,
        gold_dataset_id: str | None = None,
    ) -> dict[str, Any]:
        job = await self._repository.get_job(job_id)
        if job.get("status") not in {"completed", "completed_with_warnings"}:
            raise ConflictError("抽取任务完成后才能执行质量评测")
        if await self._repository.is_job_rematch_applying(job_id):
            raise ConflictError("正在应用新的融合结果，请完成后再发起 GT 评测")
        document = await self._repository.get_document(job["document_id"])
        report_key = normalize_report_name(document.get("filename", ""))
        dataset = (
            await self._repository.get_gold_dataset(gold_dataset_id)
            if gold_dataset_id
            else next(
                (
                    candidate
                    for candidate in await self._repository.list_gold_datasets()
                    if candidate.get("status") == "ready"
                    and normalize_report_name(candidate.get("document_filename", "")) == report_key
                ),
                None,
            )
        )
        if (
            not report_key
            or dataset is None
            or dataset.get("status") != "ready"
            or normalize_report_name(dataset.get("document_filename", "")) != report_key
        ):
            raise DomainError("当前 PDF 尚未绑定可用的人工标注数据")
        async with baseline_lock(dataset["_id"]):
            dataset = await self._repository.get_gold_dataset(dataset["_id"])
            if dataset.get("status") != "ready":
                raise DomainError("当前 PDF 尚未绑定可用的人工标注数据")
            run = await self._repository.create_quality_evaluation_run(
                job_id=job_id,
                document_id=job["document_id"],
                dataset_id=dataset["_id"],
                dataset_version=dataset.get("version", ""),
                matching_version_id=job.get("active_matching_version_id", "M0"),
                template_name=(job.get("config") or {}).get("template_name", ""),
                semantic_model=(
                    self._settings.verification_llm_model or self._settings.llm_model
                    if self._settings else ""
                ),
            )
        await self._dispatcher.dispatch(run["_id"])
        return run

    async def run(self, run_id: str) -> None:
        run: dict[str, Any] | None = None
        try:
            while True:
                await self._checkpoint(run_id)
                async with self._control_lock(run_id):
                    current = await self._repository.get_quality_evaluation_run_by_id(run_id)
                    if current["status"] == "paused":
                        continue
                    if current["status"] == "terminated":
                        raise asyncio.CancelledError()
                    run = await self._repository.update_quality_evaluation_run(
                        run_id, status="running",
                        progress={"current": 0, "total": 5, "percent": 0, "stage": "loading"},
                        error=None, logs=[*current.get("logs", []), {
                            "at": utc_now().isoformat(), "stage": "running",
                            "message": "开始加载人工基准与融合卡片",
                        }],
                    )
                    break
            job = await self._repository.get_job(run["job_id"])
            if await self._repository.is_job_rematch_applying(run["job_id"]):
                raise ConflictError("正在应用新的融合结果，请完成后再发起 GT 评测")
            if job.get("active_matching_version_id", "M0") != run.get("matching_version_id", "M0"):
                raise ConflictError("融合结果已更新，请基于最新匹配版本重新发起 GT 评测")
            document = await self._repository.get_document(run["document_id"])
            dataset = await self._repository.get_gold_dataset(run["dataset_id"])
            stored_records = await self._repository.list_job_records(run["job_id"])
            regions = await self._repository.list_job_regions(run["job_id"])
            relations = await self._repository.list_job_relations(run["job_id"])
            region_by_id = {str(region["_id"]): region for region in regions}
            relation_by_id = {str(relation["_id"]): relation for relation in relations}
            crop_by_entity = {
                str(record["entity_id"]): (
                    record.get("primary_artifact_region_id")
                    or record.get("thumbnail_region_id")
                )
                for record in stored_records
                if record.get("entity_id") and (
                    record.get("primary_artifact_region_id")
                    or record.get("thumbnail_region_id")
                )
            }
            plate_by_entity = {
                str(record["entity_id"]): plate_id
                for record in stored_records
                if record.get("entity_id")
                for plate_id in [linked_color_plate_region_id(record, region_by_id, relation_by_id)]
                if plate_id
            }
            records = [
                {
                    **record,
                    "evaluation_crop_region_id": (
                        record.get("primary_artifact_region_id")
                        or record.get("thumbnail_region_id")
                        or crop_by_entity.get(str(record.get("entity_id")))
                    ),
                    "evaluation_plate_region_id": (
                        linked_color_plate_region_id(record, region_by_id, relation_by_id)
                        or plate_by_entity.get(str(record.get("entity_id")))
                    ),
                }
                for record in select_preview_cards(stored_records)
            ]
            gold_records = await self._repository.list_gold_records(run["dataset_id"])
            gold_regions = await self._repository.list_gold_regions(run["dataset_id"])
            gold_links = await self._repository.list_gold_links(run["dataset_id"])
            pages = await self._repository.list_document_pages(run["document_id"])
            await self._progress(
                run_id, 1, "matching_records",
                f"已加载 {len(gold_records)} 条人工标注与 {len(records)} 张最终融合卡片",
            )

            result = self._evaluate(
                job=job,
                document=document,
                dataset=dataset,
                records=records,
                gold_records=gold_records,
                regions=regions,
                relations=relations,
                gold_regions=gold_regions,
                gold_links=gold_links,
                pages=pages,
            )
            await self._progress(run_id, 2, "semantic_comparison")
            semantic_count = await self._score_free_text(result, run_id=run_id)
            await self._progress(
                run_id, 3, "semantic_completed",
                f"语义模型已评测 {semantic_count} 组自由文本字段",
            )
            await self._progress(run_id, 4, "saving")
            while True:
                await self._checkpoint(run_id)
                async with self._control_lock(run_id):
                    latest = await self._repository.get_quality_evaluation_run_by_id(run_id)
                    if latest["status"] == "paused":
                        continue
                    if latest["status"] == "terminated":
                        raise asyncio.CancelledError()
                    current_job = await self._repository.get_job(run["job_id"])
                    if current_job.get("active_matching_version_id", "M0") != run.get(
                        "matching_version_id", "M0"
                    ):
                        raise ConflictError("融合结果已更新，本次 GT 评测无效；请重新发起评测")
                    await self._repository.replace_quality_evaluation_items(
                        run_id=run_id, job_id=run["job_id"], items=result.pop("items"),
                    )
                    await self._repository.update_quality_evaluation_run(
                        run_id,
                        status="completed",
                        completed_at=utc_now(),
                        progress={"current": 5, "total": 5, "percent": 100, "stage": "completed"},
                        logs=[
                            *latest.get("logs", []),
                            {
                                "at": utc_now().isoformat(),
                                "stage": "completed",
                                "message": "评测结果与逐条明细已保存",
                            },
                        ],
                        **result,
                    )
                    break
        except Exception as exc:
            if run is not None:
                async with self._control_lock(run_id):
                    latest = await self._repository.get_quality_evaluation_run_by_id(run_id)
                    if latest["status"] == "terminated":
                        return
                    await self._repository.update_quality_evaluation_run(
                        run_id,
                        status="failed",
                        error=str(exc)[:1000],
                        completed_at=utc_now(),
                        progress={"current": 5, "total": 5, "percent": 100, "stage": "failed"},
                        logs=[
                            *latest.get("logs", []),
                            {
                                "at": utc_now().isoformat(), "stage": "failed",
                                "message": str(exc)[:500],
                            },
                        ],
                    )

    async def _progress(
        self, run_id: str, current: int, stage: str, message: str | None = None
    ) -> None:
        labels = {
            "matching_records": "按人工标注主键关联最终融合卡片",
            "semantic_comparison": "计算自由文本语义一致度",
            "saving": "保存评测明细",
        }
        while True:
            await self._checkpoint(run_id)
            async with self._control_lock(run_id):
                run = await self._repository.get_quality_evaluation_run_by_id(run_id)
                if run["status"] == "paused":
                    continue
                if run["status"] == "terminated":
                    raise asyncio.CancelledError()
                await self._repository.update_quality_evaluation_run(
                    run_id,
                    progress={
                        "current": current, "total": 5, "percent": current * 20, "stage": stage,
                    },
                    logs=[*run.get("logs", []), {
                        "at": utc_now().isoformat(), "stage": stage,
                        "message": message or labels.get(stage, stage),
                    }],
                )
                return

    async def _score_free_text(self, result: dict[str, Any], *, run_id: str | None = None) -> int:
        pairs: list[dict[str, Any]] = []
        for item in result["items"]:
            if item["match_status"] != "matched":
                continue
            for field in item["field_results"]:
                if (
                    field["key"] in SEMANTIC_FIELDS
                    and compact_text(field["actual"])
                    and compact_text(field["expected"])
                ):
                    pairs.append(
                        {
                            "id": f"{item['gold_record_id']}:{field['key']}",
                            "machine": str(field["actual"])[:1200],
                            "human": str(field["expected"])[:1200],
                        }
                    )
        if not pairs:
            return 0
        if self._settings is None:
            raise DomainError("语义评测配置不存在")
        if run_id:
            judgments = await score_semantic_pairs(
                self._settings, pairs, checkpoint=lambda: self._checkpoint(run_id),
            )
        else:
            judgments = await score_semantic_pairs(self._settings, pairs)
        for item in result["items"]:
            for field in item["field_results"]:
                pair_id = f"{item['gold_record_id']}:{field['key']}"
                if pair_id not in judgments:
                    continue
                judgment = judgments[pair_id]
                field["verdict"] = "matched" if judgment["matched"] else "mismatched"
                field["score"] = round(float(judgment["score"]), 4)
                field["method"] = "semantic_llm"
                field["reason"] = str(judgment.get("reason") or "")[:500]
        for metric in result["field_metrics"]:
            if metric["key"] not in SEMANTIC_FIELDS:
                continue
            fields = [
                field
                for item in result["items"]
                for field in item["field_results"]
                if field["key"] == metric["key"]
                and field["verdict"] not in {"not_applicable", "not_evaluated"}
            ]
            metric["evaluated"] = len(fields)
            metric["matched"] = sum(field["verdict"] == "matched" for field in fields)
            metric["missing"] = sum(field["verdict"] == "missing" for field in fields)
            metric["mismatched"] = sum(field["verdict"] == "mismatched" for field in fields)
            metric["extra"] = sum(field["verdict"] == "extra" for field in fields)
            metric["score"] = (
                round(sum(field["score"] for field in fields) / len(fields), 4) if fields else None
            )
        scores = [
            metric["score"] for metric in result["field_metrics"] if metric["score"] is not None
        ]
        result["summary"]["field_macro_score"] = (
            round(sum(scores) / len(scores), 4) if scores else None
        )
        return len(pairs)

    @staticmethod
    def _evaluate(
        *,
        job: dict[str, Any],
        document: dict[str, Any],
        records: list[dict[str, Any]],
        gold_records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        gold_regions: list[dict[str, Any]],
        gold_links: list[dict[str, Any]],
        pages: list[dict[str, Any]],
        dataset: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        gold_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for gold in gold_records:
            if gold.get("canonical_artifact_id"):
                gold_by_id[str(gold["canonical_artifact_id"])].append(gold)
        predicted_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            identifier = record_artifact_id(record)
            if identifier:
                predicted_by_id[identifier].append(record)

        region_by_id = {str(region["_id"]): region for region in regions}
        relation_by_id = {str(relation["_id"]): relation for relation in relations}
        page_ocr = {int(page["page_no"]): str(page.get("ocr_text") or "") for page in pages}
        gold_links_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in gold_links:
            gold_links_by_record[str(link.get("record_id"))].append(link)

        field_totals: dict[str, dict[str, Any]] = {
            key: {
                "key": key,
                "label": label,
                "evaluated": 0,
                "matched": 0,
                "exact": 0,
                "missing": 0,
                "mismatched": 0,
                "extra": 0,
                "score_sum": 0.0,
            }
            for key, label in FIELD_LABELS.items()
        }
        ocr_totals = {
            key: {"key": key, "label": FIELD_LABELS[key], "evaluated": 0, "matched": 0}
            for key in OCR_ANCHOR_FIELDS
        }
        capabilities = (dataset or {}).get("capabilities", {})
        has_detection_labels = capabilities.get("has_detection_labels", bool(gold_regions))
        has_relation_labels = capabilities.get("has_relation_labels", bool(gold_links))
        relation_totals = (
            {
                "artifact_crop": {"evaluated": 0, "matched": 0},
                "color_plate": {"evaluated": 0, "matched": 0},
                "evidence_chain": {"evaluated": 0, "matched": 0},
            }
            if has_relation_labels
            else {}
        )
        items: list[dict[str, Any]] = []
        matched_gold_ids: set[str] = set()
        unmatched_predicted: list[str] = []
        ambiguous: list[str] = []
        unmatched_predicted = [
            record_artifact_id(record) or str(record.get("_id"))
            for record in records
            if record_artifact_id(record) not in gold_by_id
        ]

        # Human-left join: every annotated row produces exactly one saved item.
        # Duplicate keys remain visible but are never force-paired by row order.
        for gold in gold_records:
            artifact_id = str(gold.get("canonical_artifact_id") or "")
            matches = predicted_by_id.get(artifact_id, [])
            match_status = "matched"
            if len(matches) > 1 or len(gold_by_id.get(artifact_id, [])) > 1:
                match_status = "ambiguous"
                ambiguous.append(artifact_id)
            elif not matches:
                match_status = "not_found"
            if match_status != "matched":
                unscored_fields = [
                    {
                        "key": key,
                        "label": FIELD_LABELS[key],
                        "verdict": "not_evaluated",
                        "score": None,
                        "exact": False,
                        "actual": None,
                        "expected": gold.get("fields", {}).get(key),
                    }
                    for key in FIELD_LABELS
                    if clean_value(gold.get("fields", {}).get(key)) is not None
                ]
                items.append(
                    {
                        "record_id": str(matches[0].get("_id")) if len(matches) == 1 else "",
                        "crop_region_id": (
                            matches[0].get("evaluation_crop_region_id")
                            if len(matches) == 1 else None
                        ),
                        "color_plate_region_id": (
                            matches[0].get("evaluation_plate_region_id")
                            if len(matches) == 1 else None
                        ),
                        "artifact_id": artifact_id,
                        "gold_record_id": str(gold["_id"]),
                        "match_status": match_status,
                        "source_pages": matches[0].get("source_pages", [])
                        if len(matches) == 1
                        else [],
                        "field_results": unscored_fields,
                        "ocr_results": [],
                        "relation_results": [],
                    }
                )
                continue
            record = matches[0]
            matched_gold_ids.add(str(gold["_id"]))
            field_results: list[dict[str, Any]] = []
            for key in FIELD_LABELS:
                actual = artifact_id if key == "artifact_id" else preview_field_value(record, key)
                expected = gold.get("fields", {}).get(key)
                comparison = compare_field(key, actual, expected)
                field_results.append(comparison)
                totals = field_totals[key]
                if comparison["verdict"] != "not_applicable":
                    totals["evaluated"] += 1
                    totals["score_sum"] += comparison["score"]
                if comparison["verdict"] in totals:
                    totals[comparison["verdict"]] += 1
                totals["exact"] += int(comparison["exact"])

            source_pages = sorted(
                {
                    int(page)
                    for page in [
                        *record.get("source_pages", []),
                        *record.get("associated_pages", []),
                    ]
                    if str(page).isdigit()
                }
            )
            ocr_text = "\n".join(page_ocr.get(page, "") for page in source_pages)
            ocr_results: list[dict[str, Any]] = []
            for key in OCR_ANCHOR_FIELDS:
                expected = gold.get("fields", {}).get(key)
                if not expected or not ocr_text:
                    continue
                if key == "measurements":
                    anchors = _measurement_tokens(expected)
                    present = bool(anchors) and anchors.issubset(_measurement_tokens(ocr_text))
                else:
                    anchors = {compact_text(expected)}
                    present = all(anchor and anchor in compact_text(ocr_text) for anchor in anchors)
                ocr_totals[key]["evaluated"] += 1
                ocr_totals[key]["matched"] += int(present)
                ocr_results.append(
                    {"key": key, "matched": present, "expected": expected, "pages": source_pages}
                )

            record_region_ids = {str(value) for value in record.get("region_ids", [])}
            for relation_id in record.get("relation_ids", []):
                relation = relation_by_id.get(str(relation_id))
                if relation is None:
                    continue
                record_region_ids.update(
                    {
                        str(relation.get("source_region_id", "")),
                        str(relation.get("target_region_id", "")),
                    }
                )
            region_kinds = {
                region_by_id[value].get("kind", "other")
                for value in record_region_ids
                if value in region_by_id
            }
            valid_relation_count = sum(
                str(value) in relation_by_id for value in record.get("relation_ids", [])
            )
            relation_results = []
            if has_relation_labels:
                record_gold_links = gold_links_by_record[str(gold["_id"])]
                expects_artifact = any(
                    link.get("link_type") == "artifact_crop" for link in record_gold_links
                )
                expects_plate = any(
                    link.get("link_type") == "color_plate" for link in record_gold_links
                )
                if expects_artifact:
                    present = bool(region_kinds & {"artifact", "line_drawing"})
                    relation_totals["artifact_crop"]["evaluated"] += 1
                    relation_totals["artifact_crop"]["matched"] += int(present)
                    relation_results.append({"key": "artifact_crop", "matched": present})
                if expects_plate:
                    present = "color_plate" in region_kinds
                    relation_totals["color_plate"]["evaluated"] += 1
                    relation_totals["color_plate"]["matched"] += int(present)
                    relation_results.append({"key": "color_plate", "matched": present})
                relation_totals["evidence_chain"]["evaluated"] += 1
                relation_totals["evidence_chain"]["matched"] += int(valid_relation_count > 0)
                relation_results.append(
                    {"key": "evidence_chain", "matched": valid_relation_count > 0}
                )

            items.append(
                {
                    "record_id": str(record.get("_id")),
                    "crop_region_id": record.get("evaluation_crop_region_id"),
                    "color_plate_region_id": record.get("evaluation_plate_region_id"),
                    "artifact_id": artifact_id,
                    "gold_record_id": str(gold["_id"]),
                    "match_status": "matched",
                    "source_pages": source_pages,
                    "field_results": field_results,
                    "ocr_results": ocr_results,
                    "relation_results": relation_results,
                }
            )

        field_metrics = []
        for totals in field_totals.values():
            evaluated = totals.pop("evaluated")
            score_sum = totals.pop("score_sum")
            field_metrics.append(
                {
                    **totals,
                    "evaluated": evaluated,
                    "score": round(score_sum / evaluated, 4) if evaluated else None,
                    "exact_score": round(totals["exact"] / evaluated, 4) if evaluated else None,
                }
            )
        ocr_metrics = [
            {
                **metric,
                "score": (
                    round(metric["matched"] / metric["evaluated"], 4)
                    if metric["evaluated"]
                    else None
                ),
            }
            for metric in ocr_totals.values()
        ]
        relation_metrics = {
            key: {
                **metric,
                "score": (
                    round(metric["matched"] / metric["evaluated"], 4)
                    if metric["evaluated"]
                    else None
                ),
            }
            for key, metric in relation_totals.items()
        }

        effective_pages = sorted(
            set(job.get("effective_pages") or job.get("requested_pages") or [])
        )
        page_count = int(document.get("page_count") or 0)
        full_document = bool(page_count and len(effective_pages) >= page_count)
        evaluated_pages = set(effective_pages)
        predicted_regions = [region for region in regions if region.get("page") in evaluated_pages]
        scoped_gold_regions = [
            region for region in gold_regions if region.get("page") in evaluated_pages
        ]
        detection_metrics = (
            [
                detection_metric(
                    kind=kind,
                    predicted=[
                        region for region in predicted_regions if region.get("kind") == kind
                    ],
                    gold=[region for region in scoped_gold_regions if region.get("kind") == kind],
                )
                for kind in REGION_KINDS
            ]
            if has_detection_labels
            else []
        )

        matched_records = len(matched_gold_ids)
        predicted_with_id = sum(bool(record_artifact_id(record)) for record in records)
        artifact_precision = matched_records / predicted_with_id if predicted_with_id else 0.0
        artifact_recall = (
            matched_records / len(gold_records) if full_document and gold_records else None
        )
        field_scores = [metric["score"] for metric in field_metrics if metric["score"] is not None]
        ocr_scores = [metric["score"] for metric in ocr_metrics if metric["score"] is not None]
        relation_scores = [
            metric["score"] for metric in relation_metrics.values() if metric["score"] is not None
        ]
        detection_scores = [metric["f1"] for metric in detection_metrics if metric["gold_count"]]
        warnings: list[str] = []
        if not full_document:
            warnings.append("当前任务只覆盖部分页面，不计算整本器物召回率。")
        if not any(page_ocr.values()):
            warnings.append("当前文档没有可用的 OCR 文本，OCR 锚点指标暂不可计算。")
        warnings.append(
            "人工标注数据没有逐字整页转写，当前 OCR 指标是编号、尺寸和图注锚点命中率，"
            "不是字符错误率 CER。"
        )

        def mean_or_none(scores: list[float]) -> float | None:
            return round(sum(scores) / len(scores), 4) if scores else None

        return {
            "summary": {
                "predicted_records": len(records),
                "gold_records": len(gold_records),
                "matched_records": matched_records,
                "unmatched_gold_records": sum(
                    item["match_status"] == "not_found" for item in items
                ),
                "unmatched_predicted_records": len(unmatched_predicted),
                "ambiguous_records": len(ambiguous),
                "artifact_id_precision": round(artifact_precision, 4),
                "artifact_id_recall": (
                    round(artifact_recall, 4) if artifact_recall is not None else None
                ),
                "field_macro_score": mean_or_none(field_scores),
                "ocr_anchor_score": mean_or_none(ocr_scores),
                "relation_score": mean_or_none(relation_scores),
                "detection_macro_f1": mean_or_none(detection_scores),
                "full_document_scope": full_document,
                "evaluated_pages": effective_pages,
            },
            "field_metrics": field_metrics,
            "ocr_metrics": ocr_metrics,
            "detection_metrics": detection_metrics,
            "relation_metrics": relation_metrics,
            "unmatched": {
                "predicted": unmatched_predicted[:200],
                "gold": [
                    str(gold.get("canonical_artifact_id") or gold.get("_id"))
                    for gold in gold_records
                    if str(gold.get("_id")) not in matched_gold_ids
                ][:200]
                if full_document
                else [],
                "ambiguous": ambiguous[:200],
            },
            "warnings": warnings,
            "items": items,
        }
