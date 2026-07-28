import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.models.schemas import ExtractionConfig
from app.services.relation_matcher import RelationMatcher
from app.services.visual_reference import sequence_text_score

DEFAULT_EVIDENCE_KIND_BY_FIELD = {
    "artifact_id": "number",
    "figure_caption": "caption",
}


@dataclass(slots=True)
class FusionOutput:
    records: list[dict[str, Any]]
    relations: list[dict[str, Any]]


class ResultFusionService:
    provider = "archfact"
    model = "spatial-evidence-fusion"
    version = "7"
    page_window = 3
    link_hint_min_score = 0.62
    _artifact_line_pattern = re.compile(
        r"^\s*[A-Z]{1,6}\s*\d+[A-Z]?\s*[:：]\s*[A-Z]?\d+[A-Z]?\b",
        re.IGNORECASE,
    )
    _visual_reference_pattern = re.compile(
        r"(?:图|彩版|图版|fig(?:ure)?|plate)",
        re.IGNORECASE,
    )
    _measurement_value_pattern = re.compile(
        r"(?P<label>最大径|直径|残高|通高|全高|口径|底径|腹径|孔径|刃宽|足高|耳高|"
        r"高|长|宽|厚|径)\s*(?P<approx>约)?\s*"
        r"(?P<value>\d+(?:[.,]\d+)?)\s*"
        r"(?P<unit>厘米|毫米|米|cm|mm|m)?",
        re.IGNORECASE,
    )
    hint_weights = {
        "figure_refs": 1.0,
        "figure_item_nos": 1.0,
        "caption_texts": 0.98,
        "plate_refs": 0.98,
        "artifact_ids": 0.96,
        "aliases": 0.86,
    }

    def fuse(
        self,
        *,
        job_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        config: ExtractionConfig,
        model_run_id: str,
    ) -> FusionOutput:
        region_by_id = {region["id"]: region for region in regions}
        relation_by_id = {relation["id"]: relation for relation in relations}
        expected_kinds = {
            field.key: field.evidence_kind or DEFAULT_EVIDENCE_KIND_BY_FIELD.get(field.key)
            for field in config.fields
        }
        entries_by_group: dict[tuple[int, str], list[dict[str, Any]]] = {}
        matched_counts = [0] * len(records)
        expected_counts = [0] * len(records)
        has_caption_regions = any(region.get("kind") == "caption" for region in regions)

        for record_index, record in enumerate(records):
            record.setdefault("region_ids", [])
            record.setdefault("relation_ids", [])
            for field_key, field in record.get("fields", {}).items():
                expected_kind = expected_kinds.get(field_key)
                if expected_kind is None:
                    continue
                if expected_kind == "number" and has_caption_regions and self._record_hints(record):
                    # A caption-aware report must enter the visual graph through
                    # the caption. Direct text-to-number fusion is only a fallback
                    # for pages where the detector found no caption.
                    continue
                for evidence_index, evidence in enumerate(field.get("evidence", [])):
                    bbox = evidence.get("bbox")
                    page_no = evidence.get("page")
                    if not self._is_bbox(bbox) or not isinstance(page_no, int):
                        continue
                    entries_by_group.setdefault((page_no, expected_kind), []).append(
                        {
                            "record_index": record_index,
                            "field_key": field_key,
                            "evidence_index": evidence_index,
                            "bbox": bbox,
                        }
                    )

        for (page_no, expected_kind), entries in entries_by_group.items():
            candidates = [
                region
                for region in regions
                if region.get("page") == page_no and region.get("kind") == expected_kind
            ]
            if not candidates:
                continue
            for entry in entries:
                expected_counts[entry["record_index"]] += 1
            scores = [
                [self._spatial_score(entry["bbox"], candidate["bbox"]) for candidate in candidates]
                for entry in entries
            ]
            matches = RelationMatcher._maximum_assignment(scores, min_score=0.2)
            for entry_index, candidate_index, score in matches:
                entry = entries[entry_index]
                candidate = candidates[candidate_index]
                record = records[entry["record_index"]]
                evidence = record["fields"][entry["field_key"]]["evidence"][entry["evidence_index"]]
                matched_counts[entry["record_index"]] += 1
                linked_region_ids = set(evidence.get("linked_region_ids", []))
                linked_region_ids.add(candidate["id"])
                relation_ids = set(evidence.get("relation_ids", []))

                evidence_region_id = evidence.get("region_id")
                if evidence_region_id and evidence_region_id in region_by_id:
                    fusion_relation = self._fusion_relation(
                        job_id=job_id,
                        source_region_id=evidence_region_id,
                        target_region_id=candidate["id"],
                        score=score,
                        model_run_id=model_run_id,
                    )
                    relation_by_id[fusion_relation["id"]] = fusion_relation
                    relation_ids.add(fusion_relation["id"])
                    record["region_ids"].append(evidence_region_id)

                for relation in list(relation_by_id.values()):
                    if relation.get("relation_type") == "evidence_for":
                        continue
                    if relation["source_region_id"] == candidate["id"]:
                        relation_ids.add(relation["id"])
                        linked_region_ids.add(relation["target_region_id"])
                    elif relation["target_region_id"] == candidate["id"]:
                        relation_ids.add(relation["id"])
                        linked_region_ids.add(relation["source_region_id"])

                evidence["linked_region_ids"] = sorted(linked_region_ids)
                evidence["relation_ids"] = sorted(relation_ids)
                evidence["source"] = self._combined_source(
                    evidence.get("source"),
                    candidate.get("source"),
                )
                record["region_ids"].extend(linked_region_ids)
                record["relation_ids"].extend(relation_ids)

        hint_matched_records = self._fuse_link_hints(
            job_id=job_id,
            records=records,
            regions=regions,
            region_by_id=region_by_id,
            relation_by_id=relation_by_id,
            model_run_id=model_run_id,
        )
        for record_index in hint_matched_records:
            expected_counts[record_index] += 1
            matched_counts[record_index] += 1

        visual_fallback_records = self._fuse_nearest_visual_regions(
            job_id=job_id,
            records=records,
            regions=regions,
            region_by_id=region_by_id,
            relation_by_id=relation_by_id,
            model_run_id=model_run_id,
        )
        for record_index in visual_fallback_records:
            expected_counts[record_index] += 1
            matched_counts[record_index] += 1

        self.complete_record_text_evidence(
            records=records,
            regions=regions,
            region_by_id=region_by_id,
        )
        self.complete_multiline_figure_caption_evidence(
            records=records,
            regions=regions,
            region_by_id=region_by_id,
        )

        for index, record in enumerate(records):
            record["region_ids"] = sorted(set(record.get("region_ids", [])))
            record["relation_ids"] = sorted(set(record.get("relation_ids", [])))
            primary_link = self._select_primary_visual_link(
                record=record,
                regions=regions,
                region_by_id=region_by_id,
                relation_by_id=relation_by_id,
            )
            if primary_link is not None:
                number_id, artifact_id, relation_id, relation_score = primary_link
                record["primary_number_region_id"] = number_id
                record["primary_artifact_region_id"] = artifact_id
                record["primary_relation_id"] = relation_id
                record["primary_link_score"] = relation_score
                record["region_ids"] = sorted(
                    set(record["region_ids"]) | {number_id, artifact_id}
                )
                record["relation_ids"] = sorted(
                    set(record["relation_ids"]) | {relation_id}
                )
            else:
                record["primary_number_region_id"] = None
                record["primary_artifact_region_id"] = None
                record["primary_relation_id"] = None
                record["primary_link_score"] = None
            visual_region_ids = [
                region_id
                for region_id in record["region_ids"]
                if region_by_id.get(region_id, {}).get("kind")
                in {"artifact", "line_drawing", "color_plate", "grave_drawing"}
            ]
            record["thumbnail_region_id"] = (
                record.get("primary_artifact_region_id")
                or (visual_region_ids[0] if visual_region_ids else None)
            )
            record["associated_pages"] = sorted(
                set(record.get("source_pages", []))
                | {
                    int(region_by_id[region_id]["page"])
                    for region_id in record["region_ids"]
                    if region_id in region_by_id
                }
            )
            if matched_counts[index] == 0:
                record["fusion_status"] = "unlinked"
            elif matched_counts[index] == expected_counts[index]:
                record["fusion_status"] = "linked"
            else:
                record["fusion_status"] = "partial"

        return FusionOutput(records=records, relations=list(relation_by_id.values()))

    @classmethod
    def complete_record_text_evidence(
        cls,
        *,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Build complete artifact paragraphs from consecutive OCR text lines."""

        text_regions_by_page: dict[int, list[dict[str, Any]]] = {}
        for region in regions:
            page = region.get("page")
            if (
                region.get("kind") == "text"
                and isinstance(page, int)
                and cls._is_bbox(region.get("bbox"))
                and cls._region_text(region)
            ):
                text_regions_by_page.setdefault(page, []).append(region)
        for page_regions in text_regions_by_page.values():
            page_regions.sort(
                key=lambda region: (
                    float(region["bbox"][1]),
                    float(region["bbox"][0]),
                )
            )

        for record in records:
            fields = record.get("fields", {})
            artifact_field = fields.get("artifact_id", {}) if isinstance(fields, dict) else {}
            if not isinstance(artifact_field, dict):
                continue
            expected_identifier = cls._normalize_artifact_identifier(
                artifact_field.get("value") or artifact_field.get("raw_value")
            )
            artifact_evidence = artifact_field.get("evidence", [])
            if not isinstance(artifact_evidence, list):
                artifact_evidence = []

            evidence_anchors = [
                region_by_id[str(evidence["region_id"])]
                for evidence in artifact_evidence
                if isinstance(evidence, dict)
                and evidence.get("region_id")
                and str(evidence["region_id"]) in region_by_id
            ]
            anchor = next(
                (
                    region
                    for region in evidence_anchors
                    if cls._region_artifact_identifier(region) == expected_identifier
                ),
                None,
            )
            if anchor is None and expected_identifier:
                source_pages = {
                    page
                    for page in record.get("source_pages", [])
                    if isinstance(page, int)
                }
                anchor = next(
                    (
                        region
                        for page in sorted(source_pages)
                        for region in text_regions_by_page.get(page, [])
                        if cls._region_artifact_identifier(region) == expected_identifier
                    ),
                    None,
                )
            if (
                anchor is None
                or anchor.get("kind") != "text"
                or not cls._is_bbox(anchor.get("bbox"))
            ):
                continue

            paragraph_regions = [anchor]
            current = anchor
            for _ in range(5):
                continuation = cls._next_wrapped_text_region(
                    current=current,
                    page_regions=text_regions_by_page.get(int(anchor["page"]), []),
                    excluded_ids={str(region["id"]) for region in paragraph_regions},
                )
                if continuation is None:
                    break
                if cls._artifact_line_pattern.search(
                    unicodedata.normalize("NFKC", cls._region_text(continuation))
                ):
                    break
                paragraph_regions.append(continuation)
                current = continuation

            paragraph_evidence = [
                cls._text_region_evidence(region) for region in paragraph_regions
            ]
            record["text_evidence"] = paragraph_evidence
            record.setdefault("region_ids", []).extend(
                str(region["id"]) for region in paragraph_regions
            )
            cls._complete_paragraph_measurements(
                record=record,
                paragraph_regions=paragraph_regions,
            )
            cls._complete_paragraph_figure_caption(
                record=record,
                paragraph_regions=paragraph_regions,
            )

    @classmethod
    def complete_multiline_figure_caption_evidence(
        cls,
        *,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Attach wrapped OCR continuation lines until a visual reference is closed."""

        text_regions_by_page: dict[int, list[dict[str, Any]]] = {}
        for region in regions:
            page = region.get("page")
            if (
                region.get("kind") == "text"
                and isinstance(page, int)
                and cls._is_bbox(region.get("bbox"))
                and cls._region_text(region)
            ):
                text_regions_by_page.setdefault(page, []).append(region)

        for page_regions in text_regions_by_page.values():
            page_regions.sort(
                key=lambda region: (
                    float(region["bbox"][1]),
                    float(region["bbox"][0]),
                )
            )

        for record in records:
            fields = record.get("fields", {})
            if not isinstance(fields, dict):
                continue
            caption_field = fields.get("figure_caption")
            if not isinstance(caption_field, dict):
                continue
            caption_evidence = caption_field.setdefault("evidence", [])
            if not isinstance(caption_evidence, list):
                continue

            anchor_evidence = next(
                (
                    evidence
                    for evidence in caption_evidence
                    if isinstance(evidence, dict) and evidence.get("region_id") in region_by_id
                ),
                None,
            )
            if anchor_evidence is None:
                artifact_field = fields.get("artifact_id", {})
                artifact_evidence = (
                    artifact_field.get("evidence", [])
                    if isinstance(artifact_field, dict)
                    else []
                )
                anchor_evidence = next(
                    (
                        evidence
                        for evidence in artifact_evidence
                        if isinstance(evidence, dict) and evidence.get("region_id") in region_by_id
                    ),
                    None,
                )
            if anchor_evidence is None:
                continue

            anchor = region_by_id.get(str(anchor_evidence.get("region_id")))
            if (
                not isinstance(anchor, dict)
                or anchor.get("kind") != "text"
                or not cls._is_bbox(anchor.get("bbox"))
            ):
                continue
            anchor_text = cls._region_text(anchor)
            balance = cls._parenthesis_balance(anchor_text)
            if balance <= 0:
                continue

            page = anchor.get("page")
            page_regions = text_regions_by_page.get(page, [])
            continuation_regions: list[dict[str, Any]] = []
            combined_text = anchor_text
            current = anchor
            for _ in range(3):
                continuation = cls._next_wrapped_text_region(
                    current=current,
                    page_regions=page_regions,
                    excluded_ids={
                        str(anchor["id"]),
                        *(str(region["id"]) for region in continuation_regions),
                    },
                )
                if continuation is None:
                    break
                continuation_text = cls._region_text(continuation)
                if cls._artifact_line_pattern.search(
                    unicodedata.normalize("NFKC", continuation_text)
                ):
                    break
                if not cls._visual_reference_pattern.search(
                    unicodedata.normalize("NFKC", combined_text + continuation_text)
                ):
                    break
                continuation_regions.append(continuation)
                combined_text += continuation_text
                balance += cls._parenthesis_balance(continuation_text)
                current = continuation
                if balance <= 0:
                    break

            if not continuation_regions or balance > 0:
                continue
            caption_match = cls._closed_visual_reference(combined_text)
            if caption_match is None:
                continue
            raw_caption, normalized_caption = caption_match
            existing_region_ids = {
                str(evidence.get("region_id"))
                for evidence in caption_evidence
                if isinstance(evidence, dict) and evidence.get("region_id")
            }
            for continuation in continuation_regions:
                continuation_id = str(continuation["id"])
                if continuation_id in existing_region_ids:
                    continue
                caption_evidence.append(
                    {
                        "page": int(continuation["page"]),
                        "quote": cls._region_text(continuation),
                        "bbox": continuation["bbox"],
                        "region_id": continuation_id,
                        "kind": "text",
                        "relation_ids": [],
                        "linked_region_ids": [],
                        "image_id": continuation.get("image_id"),
                        "crop_object_key": continuation.get("crop_object_key"),
                        "confidence": continuation.get("confidence"),
                        "source": continuation.get("source", "unknown"),
                    }
                )
                existing_region_ids.add(continuation_id)
                record.setdefault("region_ids", []).append(continuation_id)

            caption_field["raw_value"] = raw_caption
            caption_field["value"] = normalized_caption
            if caption_field.get("status") == "missing":
                caption_field["status"] = "valid"
            hints = record.setdefault("link_hints", {})
            if isinstance(hints, dict):
                hints["caption_texts"] = list(
                    dict.fromkeys([*hints.get("caption_texts", []), normalized_caption])
                )

    @classmethod
    def _next_wrapped_text_region(
        cls,
        *,
        current: dict[str, Any],
        page_regions: list[dict[str, Any]],
        excluded_ids: set[str],
    ) -> dict[str, Any] | None:
        current_bbox = current["bbox"]
        current_height = max(0.001, float(current_bbox[3]) - float(current_bbox[1]))
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for region in page_regions:
            if str(region["id"]) in excluded_ids:
                continue
            bbox = region["bbox"]
            vertical_gap = float(bbox[1]) - float(current_bbox[3])
            if vertical_gap < -0.003 or vertical_gap > max(0.018, current_height * 1.25):
                continue
            horizontal_overlap = max(
                0.0,
                min(float(current_bbox[2]), float(bbox[2]))
                - max(float(current_bbox[0]), float(bbox[0])),
            )
            candidate_width = max(0.001, float(bbox[2]) - float(bbox[0]))
            overlap_ratio = horizontal_overlap / candidate_width
            left_delta = abs(float(bbox[0]) - float(current_bbox[0]))
            if overlap_ratio < 0.15 and left_delta > 0.12:
                continue
            candidates.append((vertical_gap, -overlap_ratio, region))
        return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None

    @staticmethod
    def _parenthesis_balance(value: str) -> int:
        normalized = unicodedata.normalize("NFKC", value)
        return normalized.count("(") - normalized.count(")")

    @classmethod
    def _closed_visual_reference(cls, value: str) -> tuple[str, str] | None:
        matches = list(
            re.finditer(
                r"[（(]([^()（）]*(?:图|彩版|图版|fig(?:ure)?|plate)[^()（）]*)[）)]",
                value,
                flags=re.IGNORECASE,
            )
        )
        if not matches:
            return None
        match = matches[-1]
        normalized = unicodedata.normalize("NFKC", match.group(1)).strip()
        return match.group(0).strip(), normalized

    @classmethod
    def _region_artifact_identifier(cls, region: dict[str, Any]) -> str:
        match = cls._artifact_line_pattern.search(
            unicodedata.normalize("NFKC", cls._region_text(region))
        )
        return cls._normalize_artifact_identifier(match.group(0) if match else "")

    @staticmethod
    def _normalize_artifact_identifier(value: Any) -> str:
        return re.sub(
            r"\s+",
            "",
            unicodedata.normalize("NFKC", str(value or "")).upper(),
        )

    @classmethod
    def _text_region_evidence(cls, region: dict[str, Any]) -> dict[str, Any]:
        return {
            "page": int(region["page"]),
            "quote": cls._region_text(region),
            "bbox": region["bbox"],
            "region_id": str(region["id"]),
            "kind": "text",
            "relation_ids": [],
            "linked_region_ids": [],
            "image_id": region.get("image_id"),
            "crop_object_key": region.get("crop_object_key"),
            "confidence": region.get("confidence"),
            "source": region.get("source", "unknown"),
        }

    @classmethod
    def _complete_paragraph_measurements(
        cls,
        *,
        record: dict[str, Any],
        paragraph_regions: list[dict[str, Any]],
    ) -> None:
        fields = record.get("fields", {})
        measurement_field = fields.get("measurements") if isinstance(fields, dict) else None
        if not isinstance(measurement_field, dict):
            return

        region_matches = [
            (region, list(cls._measurement_value_pattern.finditer(cls._region_text(region))))
            for region in paragraph_regions
        ]
        matches = [
            match
            for _, current_matches in region_matches
            for match in current_matches
        ]
        explicit_units = [match.group("unit") for match in matches if match.group("unit")]
        if not matches or not explicit_units:
            return
        shared_unit = cls._normalize_measurement_unit(explicit_units[-1])
        normalized_values = []
        raw_values = []
        for match in matches:
            unit = cls._normalize_measurement_unit(match.group("unit") or shared_unit)
            approx = "约" if match.group("approx") else ""
            normalized_values.append(
                f"{match.group('label')} {approx}{match.group('value')} {unit}".strip()
            )
            raw_values.append(match.group(0).strip())

        measurement_field["raw_value"] = "、".join(raw_values)
        measurement_field["value"] = "；".join(normalized_values)
        if measurement_field.get("status") == "missing":
            measurement_field["status"] = "valid"
        evidence = measurement_field.setdefault("evidence", [])
        if not isinstance(evidence, list):
            return
        existing_region_ids = {
            str(item.get("region_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("region_id")
        }
        for region, current_matches in region_matches:
            region_id = str(region["id"])
            if not current_matches or region_id in existing_region_ids:
                continue
            item = cls._text_region_evidence(region)
            item["quote"] = "、".join(match.group(0).strip() for match in current_matches)
            evidence.append(item)
            existing_region_ids.add(region_id)

    @classmethod
    def _complete_paragraph_figure_caption(
        cls,
        *,
        record: dict[str, Any],
        paragraph_regions: list[dict[str, Any]],
    ) -> None:
        fields = record.get("fields", {})
        caption_field = fields.get("figure_caption") if isinstance(fields, dict) else None
        if not isinstance(caption_field, dict):
            return
        combined_text = "".join(cls._region_text(region) for region in paragraph_regions)
        caption_match = cls._closed_visual_reference(combined_text)
        if caption_match is None:
            return
        raw_caption, normalized_caption = caption_match
        caption_field["raw_value"] = raw_caption
        caption_field["value"] = normalized_caption
        if caption_field.get("status") == "missing":
            caption_field["status"] = "valid"
        evidence = caption_field.setdefault("evidence", [])
        if not isinstance(evidence, list):
            return
        existing_region_ids = {
            str(item.get("region_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("region_id")
        }
        for region in paragraph_regions:
            region_id = str(region["id"])
            if (
                region_id in existing_region_ids
                or not cls._visual_reference_pattern.search(cls._region_text(region))
            ):
                continue
            evidence.append(cls._text_region_evidence(region))
            existing_region_ids.add(region_id)
        hints = record.setdefault("link_hints", {})
        if isinstance(hints, dict):
            hints["caption_texts"] = list(
                dict.fromkeys([*hints.get("caption_texts", []), normalized_caption])
            )

    @staticmethod
    def _normalize_measurement_unit(value: str) -> str:
        return {
            "厘米": "cm",
            "毫米": "mm",
            "米": "m",
        }.get(value.casefold(), value.casefold())

    def _fuse_nearest_visual_regions(
        self,
        *,
        job_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
        model_run_id: str,
    ) -> set[int]:
        """Fallback for reports whose YOLO model has no caption/number classes."""
        visual_kinds = {"artifact", "line_drawing", "color_plate", "grave_drawing"}
        candidates = [
            region
            for region in regions
            if region.get("kind") in visual_kinds and region.get("crop_object_key")
        ]
        entries: list[dict[str, Any]] = []
        for record_index, record in enumerate(records):
            if not self._is_meaningful_record(record):
                continue
            if any(
                region_by_id.get(region_id, {}).get("kind") in visual_kinds
                for region_id in record.get("region_ids", [])
            ):
                continue
            evidence = self._display_evidence(record)
            if evidence is None or not self._is_bbox(evidence.get("bbox")):
                continue
            entries.append(
                {
                    "record_index": record_index,
                    "page": int(evidence.get("page", self._record_source_page(record))),
                    "bbox": evidence["bbox"],
                    "evidence": evidence,
                }
            )
        if not entries or not candidates:
            return set()

        scores: list[list[float]] = []
        for entry in entries:
            entry_center = self._center(entry["bbox"])
            row: list[float] = []
            for candidate in candidates:
                page_distance = abs(int(candidate.get("page", entry["page"])) - entry["page"])
                if page_distance > self.page_window:
                    row.append(0.0)
                    continue
                visual_center = self._center(candidate["bbox"])
                distance_score = max(0.0, 1.0 - math.dist(entry_center, visual_center) / 1.1)
                page_score = max(0.0, 1.0 - page_distance / (self.page_window + 1))
                above_caption_bonus = (
                    0.12 if page_distance == 0 and candidate["bbox"][3] <= entry["bbox"][1] else 0.0
                )
                score = 0.78 * distance_score + 0.22 * page_score + above_caption_bonus
                row.append(min(1.0, score))
            scores.append(row)

        matched_records: set[int] = set()
        for entry_index, candidate_index, score in RelationMatcher._maximum_assignment(
            scores,
            min_score=0.25,
        ):
            entry = entries[entry_index]
            candidate = candidates[candidate_index]
            record = records[entry["record_index"]]
            evidence = entry["evidence"]
            evidence_region_id = evidence.get("region_id")
            record.setdefault("region_ids", []).append(candidate["id"])
            linked_region_ids = set(evidence.get("linked_region_ids", []))
            linked_region_ids.add(candidate["id"])
            evidence["linked_region_ids"] = sorted(linked_region_ids)

            if evidence_region_id and evidence_region_id in region_by_id:
                relation = self._fusion_relation(
                    job_id=job_id,
                    source_region_id=evidence_region_id,
                    target_region_id=candidate["id"],
                    score=score,
                    model_run_id=model_run_id,
                    method="nearest_visual_fusion",
                )
                relation_by_id[relation["id"]] = relation
                record.setdefault("region_ids", []).append(evidence_region_id)
                record.setdefault("relation_ids", []).append(relation["id"])
                evidence["relation_ids"] = sorted(
                    set(evidence.get("relation_ids", [])) | {relation["id"]}
                )
            matched_records.add(entry["record_index"])
        return matched_records

    @staticmethod
    def _is_meaningful_record(record: dict[str, Any]) -> bool:
        fields = record.get("fields", {})
        populated = {
            key
            for key, field in fields.items()
            if isinstance(field, dict)
            and field.get("value") is not None
            and str(field.get("value")).strip()
        }
        if populated & {"artifact_id", "context_id", "figure_caption"}:
            return True
        linkage = record.get("linkage", {})
        identity = linkage.get("identity", {}) if isinstance(linkage, dict) else {}
        visual = linkage.get("visual_link", {}) if isinstance(linkage, dict) else {}
        if any(identity.values()) or any(
            visual.get(key)
            for key in (
                "figure_no",
                "figure_item_no",
                "plate_no",
                "plate_item_no",
                "caption_raw",
            )
        ):
            return True
        return len(populated) >= 2

    @staticmethod
    def _display_evidence(record: dict[str, Any]) -> dict[str, Any] | None:
        fields = record.get("fields", {})
        for field_key in ("figure_caption", "artifact_id", "category"):
            evidence = fields.get(field_key, {}).get("evidence", [])
            if evidence:
                return evidence[0]
        for field in fields.values():
            evidence = field.get("evidence", []) if isinstance(field, dict) else []
            if evidence:
                return evidence[0]
        linkage = record.get("linkage", {})
        visual = linkage.get("visual_link", {}) if isinstance(linkage, dict) else {}
        linkage_evidence = visual.get("evidence", []) if isinstance(visual, dict) else []
        if linkage_evidence:
            return linkage_evidence[0]
        return None

    def _fuse_link_hints(
        self,
        *,
        job_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
        model_run_id: str,
    ) -> set[int]:
        matched_records: set[int] = set()
        visual_text_regions = [
            region
            for region in regions
            if region.get("kind") in {"number", "caption"} and self._region_text(region)
        ]
        if not visual_text_regions:
            return matched_records

        for record_index, record in enumerate(records):
            best_matches: dict[str, tuple[float, str, dict[str, Any]]] = {}
            strong_global_matches: dict[str, tuple[float, str, dict[str, Any]]] = {}
            source_page = self._record_source_page(record)
            record_hints = self._record_hints(record)
            has_scoped_item_context = any(
                hint_key in {"artifact_ids", "figure_refs"}
                for hint_key, _ in record_hints
            )
            for hint_key, hint in record_hints:
                if hint_key == "figure_item_nos" and not has_scoped_item_context:
                    # List/section numbers are common in archaeological prose.
                    # Without an artifact ID or figure reference, a bare "4"
                    # must not be compared with detector labels such as M3:4.
                    continue
                if hint_key == "figure_item_nos":
                    expected_kinds = {"number"}
                elif hint_key in {"figure_refs", "caption_texts", "plate_refs"}:
                    expected_kinds = {"caption"}
                else:
                    expected_kinds = {"caption", "number"}
                for region in visual_text_regions:
                    if region.get("kind") not in expected_kinds:
                        continue
                    text_score = self._hint_text_score(
                        hint_key,
                        hint,
                        self._region_text(region),
                    )
                    page_distance = abs(int(region.get("page", source_page)) - source_page)
                    is_strong_global = text_score >= 0.94 and self._is_strong_global_hint(
                        hint_key, hint
                    )
                    if page_distance > self.page_window and not is_strong_global:
                        continue
                    page_score = max(0.0, 1.0 - page_distance / max(self.page_window + 1, 1))
                    score = (0.9 * text_score + 0.1 * page_score) * self.hint_weights.get(
                        hint_key,
                        0.8,
                    )
                    region_kind = str(region.get("kind"))
                    best_match = best_matches.get(region_kind)
                    if best_match is None or score > best_match[0]:
                        best_matches[region_kind] = (score, hint_key, region)
                    if is_strong_global:
                        current = strong_global_matches.get(str(region["id"]))
                        if current is None or score > current[0]:
                            strong_global_matches[str(region["id"])] = (
                                score,
                                hint_key,
                                region,
                            )

            caption_match = best_matches.get("caption")
            number_match = best_matches.get("number")
            preferred_match = (
                caption_match
                if caption_match is not None and caption_match[0] >= self.link_hint_min_score
                else number_match
            )
            selected_matches = dict(strong_global_matches)
            if preferred_match is not None:
                selected_matches[str(preferred_match[2]["id"])] = preferred_match
            selected_matches = {
                region_id: match
                for region_id, match in selected_matches.items()
                if match[0] >= self.link_hint_min_score
            }
            selected_matches = self._anchor_caption_matches_to_item_numbers(
                record=record,
                selected_matches=selected_matches,
                region_by_id=region_by_id,
                relation_by_id=relation_by_id,
            )
            if not selected_matches:
                continue
            for score, hint_key, candidate in selected_matches.values():
                evidence = self._primary_evidence(record, hint_key)
                if evidence is None:
                    continue
                evidence_region_id = evidence.get("region_id")
                if not evidence_region_id or evidence_region_id not in region_by_id:
                    continue

                anchor_number_id = (
                    str(candidate["id"])
                    if candidate.get("kind") == "number"
                    and self._has_caption_number_relation(
                        str(candidate["id"]),
                        relation_by_id,
                    )
                    else None
                )
                linked_region_ids, linked_relation_ids = self._connected_visual_regions(
                    candidate["id"],
                    region_by_id,
                    relation_by_id,
                    anchor_number_id=anchor_number_id,
                )
                page_distance = abs(int(candidate.get("page", source_page)) - source_page)
                fusion_relation = self._fusion_relation(
                    job_id=job_id,
                    source_region_id=evidence_region_id,
                    target_region_id=candidate["id"],
                    score=score,
                    model_run_id=model_run_id,
                    method=self._hint_fusion_method(
                        candidate=candidate,
                        page_distance=page_distance,
                        relation_by_id=relation_by_id,
                    ),
                )
                relation_by_id[fusion_relation["id"]] = fusion_relation
                linked_relation_ids.add(fusion_relation["id"])
                linked_region_ids.add(evidence_region_id)

                evidence["linked_region_ids"] = sorted(
                    set(evidence.get("linked_region_ids", [])) | linked_region_ids
                )
                evidence["relation_ids"] = sorted(
                    set(evidence.get("relation_ids", [])) | linked_relation_ids
                )
                evidence["source"] = self._combined_source(
                    evidence.get("source"),
                    candidate.get("source"),
                )
                record["region_ids"].extend(linked_region_ids)
                record["relation_ids"].extend(linked_relation_ids)
                matched_records.add(record_index)
        return matched_records

    def _anchor_caption_matches_to_item_numbers(
        self,
        *,
        record: dict[str, Any],
        selected_matches: dict[str, tuple[float, str, dict[str, Any]]],
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, tuple[float, str, dict[str, Any]]]:
        item_hints = [
            hint for hint_key, hint in self._record_hints(record) if hint_key == "figure_item_nos"
        ]
        figure_hints = [
            hint for hint_key, hint in self._record_hints(record) if hint_key == "figure_refs"
        ]
        artifact_hints = [
            hint for hint_key, hint in self._record_hints(record) if hint_key == "artifact_ids"
        ]
        # A bare section/list number such as ``4. 玉管、珠...`` is not a visual
        # sequence. Item-only anchoring is safe only when the record explicitly
        # names the figure that owns that item number.
        if not artifact_hints and not figure_hints:
            item_hints = []
        if not item_hints and not artifact_hints:
            return selected_matches

        anchored = dict(selected_matches)
        for region_id, match in list(selected_matches.items()):
            caption_score, _, caption = match
            if caption.get("kind") != "caption":
                continue
            number_ids = {
                str(relation.get("target_region_id"))
                for relation in relation_by_id.values()
                if relation.get("relation_type") == "caption_to_number"
                and relation.get("source_region_id") == caption["id"]
            }
            best_number: tuple[int, float, str, dict[str, Any]] | None = None
            for number_id in number_ids:
                number = region_by_id.get(number_id)
                if number is None or number.get("kind") != "number":
                    continue
                identifier_score = max(
                    (
                        self._identifier_text_score(hint, self._region_text(number))
                        for hint in artifact_hints
                    ),
                    default=0.0,
                )
                item_score = max(
                    (sequence_text_score(hint, self._region_text(number)) for hint in item_hints),
                    default=0.0,
                )
                priority = 2 if identifier_score == 1.0 else 1 if item_score == 1.0 else 0
                score = identifier_score if priority == 2 else item_score
                hint_key = "artifact_ids" if priority == 2 else "figure_item_nos"
                candidate = (priority, score, hint_key, number)
                if best_number is None or candidate[:2] > best_number[:2]:
                    best_number = candidate
            if best_number is None or best_number[0] == 0:
                continue

            _, item_score, hint_key, number = best_number
            anchored.pop(region_id, None)
            anchored[str(number["id"])] = (
                0.55 * caption_score + 0.45 * item_score,
                hint_key,
                number,
            )
        return anchored

    def _select_primary_visual_link(
        self,
        *,
        record: dict[str, Any],
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
    ) -> tuple[str, str, str, float | None] | None:
        """Choose the single number->artifact edge that owns the record's preview.

        A whole-figure caption may legitimately point at many sequence labels. It is
        useful for finding the candidate group, but it must never decide which crop is
        shown for an explicitly identified artifact such as M3:4.
        """

        artifact_hints = [
            hint for hint_key, hint in self._record_hints(record) if hint_key == "artifact_ids"
        ]
        linkage = record.get("linkage", {})
        identity = linkage.get("identity", {}) if isinstance(linkage, dict) else {}
        if isinstance(identity, dict):
            artifact_hints.extend(
                str(value)
                for value in (
                    identity.get("artifact_id_normalized"),
                    identity.get("artifact_id_raw"),
                )
                if value and str(value).strip()
            )
        artifact_field = record.get("fields", {}).get("artifact_id", {})
        if isinstance(artifact_field, dict):
            artifact_hints.extend(
                str(value)
                for value in (artifact_field.get("value"), artifact_field.get("raw_value"))
                if value and str(value).strip()
            )
        artifact_hints = list(dict.fromkeys(artifact_hints))
        item_hints = [
            hint for hint_key, hint in self._record_hints(record) if hint_key == "figure_item_nos"
        ]
        figure_hints = [
            hint for hint_key, hint in self._record_hints(record) if hint_key == "figure_refs"
        ]
        if not artifact_hints and not figure_hints:
            # Do not turn an ordinary numbered heading into M3:4 (or another
            # detector sequence) merely because both contain the digit 4.
            return None
        if not artifact_hints and not item_hints:
            return None

        record_region_ids = set(record.get("region_ids", []))
        artifact_evidence_region_ids = {
            str(region_id)
            for evidence in (
                artifact_field.get("evidence", [])
                if isinstance(artifact_field, dict)
                else []
            )
            if isinstance(evidence, dict)
            for region_id in evidence.get("linked_region_ids", [])
        }
        source_page = self._record_source_page(record)
        candidates: list[
            tuple[tuple[int, int, float, int, int], str, str, str, float | None]
        ] = []
        for relation in relation_by_id.values():
            if relation.get("relation_type") != "number_of":
                continue
            if relation.get("review_status") == "rejected":
                continue
            source_id = str(relation.get("source_region_id", ""))
            target_id = str(relation.get("target_region_id", ""))
            source = region_by_id.get(source_id)
            target = region_by_id.get(target_id)
            if source is None or target is None:
                continue
            if source.get("kind") == "number":
                number, artifact = source, target
            elif target.get("kind") == "number":
                number, artifact = target, source
            else:
                continue
            if artifact.get("kind") not in {
                "artifact",
                "line_drawing",
                "grave_drawing",
            }:
                continue

            number_text = self._region_text(number)
            identifier_match = any(
                self._identifier_text_score(hint, number_text) == 1.0
                for hint in artifact_hints
            )
            spatial_identifier_fallback = (
                bool(artifact_hints)
                and not self._normalize_text(number_text)
                and str(number["id"]) in artifact_evidence_region_ids
            )
            item_match = any(
                sequence_text_score(hint, number_text) == 1.0 for hint in item_hints
            )
            if artifact_hints and not identifier_match and not spatial_identifier_fallback:
                # An explicit M3:4 must not fall back to M3:11 merely because both
                # appear under the same caption. Item-only matching is allowed only
                # when the record itself has no full artifact identifier.
                continue
            if not identifier_match and not spatial_identifier_fallback and not item_match:
                continue

            relation_score = relation.get("score")
            numeric_score = (
                float(relation_score) if isinstance(relation_score, (int, float)) else 0.0
            )
            rank = (
                2 if identifier_match else 1,
                1 if relation.get("review_status") == "accepted" else 0,
                numeric_score,
                1
                if number["id"] in record_region_ids
                or artifact["id"] in record_region_ids
                else 0,
                -abs(int(number.get("page", source_page)) - source_page),
            )
            candidates.append(
                (
                    rank,
                    str(number["id"]),
                    str(artifact["id"]),
                    str(relation["id"]),
                    float(relation_score)
                    if isinstance(relation_score, (int, float))
                    else None,
                )
            )

        if not candidates:
            return None
        _, number_id, artifact_id, relation_id, score = max(
            candidates,
            key=lambda item: item[0],
        )
        return number_id, artifact_id, relation_id, score

    def _hint_fusion_method(
        self,
        *,
        candidate: dict[str, Any],
        page_distance: int,
        relation_by_id: dict[str, dict[str, Any]],
    ) -> str:
        if candidate.get("kind") == "number" and self._has_caption_number_relation(
            str(candidate["id"]),
            relation_by_id,
        ):
            return (
                "global_caption_item_fusion"
                if page_distance > self.page_window
                else "caption_item_identifier_fusion"
            )
        if page_distance > self.page_window:
            return "global_identifier_fusion"
        if candidate.get("kind") == "caption":
            return "caption_first_identifier_fusion"
        return "identifier_evidence_fusion"

    @staticmethod
    def _has_caption_number_relation(
        number_id: str,
        relation_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        return any(
            relation.get("relation_type") == "caption_to_number"
            and relation.get("target_region_id") == number_id
            for relation in relation_by_id.values()
        )

    @classmethod
    def _is_strong_global_hint(cls, hint_key: str, hint: str) -> bool:
        normalized = cls._normalize_text(hint)
        if hint_key == "artifact_ids":
            return len(normalized) >= 3
        if hint_key not in {"figure_refs", "plate_refs"}:
            return False
        normalized_hint = unicodedata.normalize("NFKC", hint).strip()
        if re.fullmatch(
            r"(?:图|彩图|彩版|图版|fig(?:ure)?\.?)\s*\d+"
            r"(?:\s*[-–—:：]\s*\d+)*",
            normalized_hint,
            re.IGNORECASE,
        ):
            return True
        return bool(
            re.search(
                r"[:\uFF1A\-\u2013\u2014\s][A-Za-z0-9]+\s*$",
                hint.strip(),
            )
        )

    @staticmethod
    def _record_source_page(record: dict[str, Any]) -> int:
        pages = record.get("source_pages", [])
        return int(pages[0]) if pages else 0

    @staticmethod
    def _record_hints(record: dict[str, Any]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        hints = record.get("link_hints", {})
        if not isinstance(hints, dict):
            return result
        for key in (
            "figure_refs",
            "figure_item_nos",
            "caption_texts",
            "artifact_ids",
            "plate_refs",
            "aliases",
        ):
            values = hints.get(key, [])
            if isinstance(values, list):
                result.extend((key, str(value)) for value in values if str(value).strip())
        return result

    @staticmethod
    def _primary_evidence(
        record: dict[str, Any],
        hint_key: str,
    ) -> dict[str, Any] | None:
        preferred_fields = {
            "artifact_ids": ("artifact_id", "context_id"),
            "figure_refs": ("figure_caption", "figure_no"),
            "figure_item_nos": ("figure_caption", "figure_item_no"),
            "caption_texts": ("figure_caption",),
            "plate_refs": ("plate_no", "color_plate"),
            "aliases": ("artifact_id", "figure_caption"),
        }.get(hint_key, ())
        fields = record.get("fields", {})
        for field_key in preferred_fields:
            evidence = fields.get(field_key, {}).get("evidence", [])
            if evidence:
                return evidence[0]
        for field in fields.values():
            evidence = field.get("evidence", []) if isinstance(field, dict) else []
            if evidence:
                return evidence[0]
        linkage = record.get("linkage", {})
        visual = linkage.get("visual_link", {}) if isinstance(linkage, dict) else {}
        linkage_evidence = visual.get("evidence", []) if isinstance(visual, dict) else []
        if linkage_evidence:
            return linkage_evidence[0]
        return None

    @staticmethod
    def _region_text(region: dict[str, Any]) -> str:
        return str(region.get("text") or region.get("ocr_raw_text") or "").strip()

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    @classmethod
    def _identifier_text_score(cls, expected: str, observed: str) -> float:
        """Strict identifier comparison; M3:4 and M3:11 must never fuzzy-match."""

        expected_value = cls._normalize_text(expected)
        observed_value = cls._normalize_text(observed)
        if not expected_value or not observed_value:
            return 0.0
        return 1.0 if expected_value == observed_value else 0.0

    @classmethod
    def _text_score(cls, left: str, right: str) -> float:
        left_value = cls._normalize_text(left)
        right_value = cls._normalize_text(right)
        if not left_value or not right_value:
            return 0.0
        if left_value == right_value:
            return 1.0
        if min(len(left_value), len(right_value)) >= 3 and (
            left_value in right_value or right_value in left_value
        ):
            return 0.94
        return SequenceMatcher(None, left_value, right_value).ratio()

    @classmethod
    def _hint_text_score(cls, hint_key: str, hint: str, region_text: str) -> float:
        score = cls._text_score(hint, region_text)
        if hint_key not in {"figure_refs", "plate_refs"}:
            return score
        normalized_hint = unicodedata.normalize("NFKC", hint).casefold().strip()
        normalized_region = unicodedata.normalize("NFKC", region_text).casefold()
        compact_hint = "".join(normalized_hint.split())
        if not compact_hint:
            return score
        pattern = r"\s*".join(re.escape(character) for character in compact_hint)
        if compact_hint[-1].isdigit():
            pattern += r"(?!\d)"
        return max(score, 0.96) if re.search(pattern, normalized_region) else score

    @staticmethod
    def _connected_visual_regions(
        start_region_id: str,
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
        *,
        anchor_number_id: str | None = None,
    ) -> tuple[set[str], set[str]]:
        linked_regions = {start_region_id}
        linked_relations: set[str] = set()
        frontier = {start_region_id}
        traversable_relation_types = {
            "caption_to_number",
            "number_of",
            "caption_of",
            "drawing_of",
            "color_plate_of",
            "image_of",
        }
        for _ in range(2):
            next_frontier: set[str] = set()
            for relation in relation_by_id.values():
                if relation.get("relation_type") not in traversable_relation_types:
                    continue
                if (
                    relation.get("relation_type") == "caption_to_number"
                    and anchor_number_id is not None
                    and relation.get("target_region_id") != anchor_number_id
                ):
                    continue
                source = relation.get("source_region_id")
                target = relation.get("target_region_id")
                adjacent: str | None = None
                if source in frontier:
                    adjacent = target
                elif target in frontier:
                    adjacent = source
                if not adjacent or adjacent not in region_by_id:
                    continue
                linked_relations.add(relation["id"])
                if adjacent not in linked_regions:
                    linked_regions.add(adjacent)
                    next_frontier.add(adjacent)
            frontier = next_frontier
            if not frontier:
                break
        return linked_regions, linked_relations

    def _fusion_relation(
        self,
        *,
        job_id: str,
        source_region_id: str,
        target_region_id: str,
        score: float,
        model_run_id: str,
        method: str = "spatial_evidence_fusion",
    ) -> dict[str, Any]:
        digest = hashlib.sha256(
            f"{job_id}:evidence_for:{source_region_id}:{target_region_id}".encode()
        ).hexdigest()[:24]
        return {
            "id": f"rel_{digest}",
            "source_region_id": source_region_id,
            "target_region_id": target_region_id,
            "relation_type": "evidence_for",
            "score": round(score, 6),
            "method": method,
            "version": self.version,
            "model_run_id": model_run_id,
            "review_status": "unreviewed",
        }

    @staticmethod
    def _spatial_score(source_bbox: list[float], target_bbox: list[float]) -> float:
        source_center = ResultFusionService._center(source_bbox)
        target_center = ResultFusionService._center(target_bbox)
        distance_score = max(0.0, 1.0 - math.dist(source_center, target_center) / 0.6)
        overlap_score = ResultFusionService._intersection_over_smaller(
            source_bbox,
            target_bbox,
        )
        return 0.6 * distance_score + 0.4 * overlap_score

    @staticmethod
    def _intersection_over_smaller(first: list[float], second: list[float]) -> float:
        width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
        height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
        intersection = width * height
        first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
        second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
        smaller = min(first_area, second_area)
        return intersection / smaller if smaller > 0 else 0.0

    @staticmethod
    def _center(bbox: list[float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    @staticmethod
    def _is_bbox(bbox: Any) -> bool:
        return (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in bbox)
            and bbox[0] < bbox[2]
            and bbox[1] < bbox[3]
        )

    @staticmethod
    def _combined_source(first: Any, second: Any) -> str:
        sources = [str(source) for source in (first, second) if source and source != "unknown"]
        return "+".join(dict.fromkeys(sources)) or "fusion"
