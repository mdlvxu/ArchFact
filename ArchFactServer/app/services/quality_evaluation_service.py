from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from app.core.errors import ConflictError, DomainError
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.repositories.mongo_repository import MongoRepository
from app.services.gold_dataset_service import normalize_identifier

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


def utc_now() -> datetime:
    return datetime.now(UTC)


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


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = (
        text.replace("厘米", "cm")
        .replace("公分", "cm")
        .replace("毫米", "mm")
        .replace("米", "m")
    )
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
    return {
        f"{number.rstrip('0').rstrip('.')}{unit}"
        for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(cm|mm|m)?", text)
        if number
    }


def compare_field(key: str, actual: Any, expected: Any) -> dict[str, Any]:
    actual_text, expected_text = compact_text(actual), compact_text(expected)
    if not expected_text:
        verdict = "extra" if actual_text else "not_applicable"
        score = 1.0 if not actual_text else 0.0
    elif not actual_text:
        verdict, score = "missing", 0.0
    elif key == "measurements":
        expected_tokens = _measurement_tokens(expected)
        actual_tokens = _measurement_tokens(actual)
        if expected_tokens:
            overlap = len(expected_tokens & actual_tokens) / len(expected_tokens)
            score = overlap
            verdict = "matched" if math.isclose(overlap, 1.0) else "mismatched"
        else:
            score = text_similarity(actual, expected)
            verdict = "matched" if score >= 0.9 else "mismatched"
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
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher

    async def create(
        self,
        *,
        job_id: str,
        gold_dataset_id: str | None = None,
    ) -> dict[str, Any]:
        job = await self._repository.get_job(job_id)
        if job.get("status") not in {"completed", "completed_with_warnings"}:
            raise ConflictError("抽取任务完成后才能执行质量评测")
        dataset = (
            await self._repository.get_gold_dataset(gold_dataset_id)
            if gold_dataset_id
            else await self._repository.get_gold_dataset_for_document(
                document_id=job["document_id"]
            )
        )
        if dataset is None or dataset.get("document_id") != job.get("document_id"):
            raise DomainError("当前 PDF 尚未绑定可用的人工金标准")
        run = await self._repository.create_quality_evaluation_run(
            job_id=job_id,
            document_id=job["document_id"],
            dataset_id=dataset["_id"],
            dataset_version=dataset.get("version", ""),
            matching_version_id=job.get("active_matching_version_id", "M0"),
        )
        await self._dispatcher.dispatch(run["_id"])
        return run

    async def run(self, run_id: str) -> None:
        run: dict[str, Any] | None = None
        try:
            run = await self._repository.update_quality_evaluation_run(
                run_id,
                status="running",
                progress={"current": 0, "total": 5, "percent": 0, "stage": "loading"},
                error=None,
            )
            job = await self._repository.get_job(run["job_id"])
            document = await self._repository.get_document(run["document_id"])
            records = await self._repository.list_job_records(run["job_id"])
            gold_records = await self._repository.list_gold_records(run["dataset_id"])
            regions = await self._repository.list_job_regions(run["job_id"])
            relations = await self._repository.list_job_relations(run["job_id"])
            gold_regions = await self._repository.list_gold_regions(run["dataset_id"])
            gold_links = await self._repository.list_gold_links(run["dataset_id"])
            pages = await self._repository.list_document_pages(run["document_id"])
            await self._progress(run_id, 1, "matching_records")

            result = self._evaluate(
                job=job,
                document=document,
                records=records,
                gold_records=gold_records,
                regions=regions,
                relations=relations,
                gold_regions=gold_regions,
                gold_links=gold_links,
                pages=pages,
            )
            await self._progress(run_id, 4, "saving")
            await self._repository.replace_quality_evaluation_items(
                run_id=run_id,
                job_id=run["job_id"],
                items=result.pop("items"),
            )
            await self._repository.update_quality_evaluation_run(
                run_id,
                status="completed",
                completed_at=utc_now(),
                progress={"current": 5, "total": 5, "percent": 100, "stage": "completed"},
                **result,
            )
        except Exception as exc:
            if run is not None:
                await self._repository.update_quality_evaluation_run(
                    run_id,
                    status="failed",
                    error=str(exc)[:1000],
                    completed_at=utc_now(),
                    progress={"current": 5, "total": 5, "percent": 100, "stage": "failed"},
                )

    async def _progress(self, run_id: str, current: int, stage: str) -> None:
        await self._repository.update_quality_evaluation_run(
            run_id,
            progress={"current": current, "total": 5, "percent": current * 20, "stage": stage},
        )

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
    ) -> dict[str, Any]:
        gold_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for gold in gold_records:
            if gold.get("canonical_artifact_id"):
                gold_by_id[str(gold["canonical_artifact_id"])].append(gold)

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
        relation_totals = {
            "artifact_crop": {"evaluated": 0, "matched": 0},
            "color_plate": {"evaluated": 0, "matched": 0},
            "evidence_chain": {"evaluated": 0, "matched": 0},
        }
        items: list[dict[str, Any]] = []
        matched_gold_ids: set[str] = set()
        unmatched_predicted: list[str] = []
        ambiguous: list[str] = []
        predicted_id_counts: dict[str, int] = defaultdict(int)
        for record in records:
            identifier = record_artifact_id(record)
            if identifier:
                predicted_id_counts[identifier] += 1

        for record in records:
            artifact_id = record_artifact_id(record)
            matches = gold_by_id.get(artifact_id, []) if artifact_id else []
            match_status = "matched"
            if artifact_id and predicted_id_counts[artifact_id] > 1:
                match_status = "ambiguous"
                ambiguous.append(artifact_id)
            elif not matches:
                match_status = "not_found"
                unmatched_predicted.append(artifact_id or str(record.get("_id")))
            elif len(matches) > 1:
                match_status = "ambiguous"
                ambiguous.append(artifact_id)
            if match_status != "matched":
                items.append(
                    {
                        "record_id": str(record.get("_id")),
                        "artifact_id": artifact_id,
                        "gold_record_id": None,
                        "match_status": match_status,
                        "source_pages": record.get("source_pages", []),
                        "field_results": [],
                        "ocr_results": [],
                        "relation_results": [],
                    }
                )
                continue

            gold = matches[0]
            matched_gold_ids.add(str(gold["_id"]))
            field_results: list[dict[str, Any]] = []
            for key in FIELD_LABELS:
                actual = artifact_id if key == "artifact_id" else extracted_field_value(record, key)
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
            record_gold_links = gold_links_by_record[str(gold["_id"])]
            expects_artifact = any(
                link.get("link_type") == "artifact_crop" for link in record_gold_links
            )
            expects_plate = any(
                link.get("link_type") == "color_plate" for link in record_gold_links
            )
            relation_results = []
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
        detection_metrics = [
            detection_metric(
                kind=kind,
                predicted=[region for region in predicted_regions if region.get("kind") == kind],
                gold=[region for region in scoped_gold_regions if region.get("kind") == kind],
            )
            for kind in REGION_KINDS
        ]

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
            "金标准没有逐字整页转写，当前 OCR 指标是编号、尺寸和图注锚点命中率，"
            "不是字符错误率 CER。"
        )

        def mean_or_none(scores: list[float]) -> float | None:
            return round(sum(scores) / len(scores), 4) if scores else None

        return {
            "summary": {
                "predicted_records": len(records),
                "gold_records": len(gold_records),
                "matched_records": matched_records,
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
                ][:200] if full_document else [],
                "ambiguous": ambiguous[:200],
            },
            "warnings": warnings,
            "items": items,
        }
