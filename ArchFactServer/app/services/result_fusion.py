import copy
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.models.schemas import ExtractionConfig
from app.services.page_semantics import PageSemantics
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
    version = "16"
    page_window = 3
    link_hint_min_score = 0.62
    _artifact_line_pattern = re.compile(
        r"^\s*(?:[\u4e00-\u9fff]{1,2})?[A-Z]{1,6}\s*\d+[A-Z]?\s*[:：]\s*[A-Z]?\d+[A-Z]?\b",
        re.IGNORECASE,
    )
    _artifact_identifier_pattern = re.compile(
        r"(?<![A-Z0-9])"
        r"(?:[\u4e00-\u9fff]{1,2})?"
        r"([A-Z]{1,6}\s*\d+[A-Z]?\s*[:：]\s*[A-Z]?\d+[A-Z]?)"
        r"(?![A-Z0-9])",
        re.IGNORECASE,
    )
    # Tomb/unit labels such as 仲M4:3 are plate captions, not distinct artifact IDs.
    _tomb_unit_prefix_pattern = re.compile(r"^[\u4e00-\u9fff]{1,2}(?=[A-Z])")
    _plate_item_caption_pattern = re.compile(
        r"^\s*\d+\s*[.．、:：]"
        r".{0,24}"
        r"[（(][^）)]*[A-Z]{1,6}\s*\d+",
        re.IGNORECASE,
    )
    _body_field_keys = (
        "morphological_description",
        "measurements",
        "texture",
        "surface_color",
        "completeness",
    )
    _ocr_tolerant_artifact_line_pattern = re.compile(
        r"^\s*(?P<prefix>[A-Z]{1,6}?)(?P<left>[0-9ILOQ|]+)"
        r"(?P<left_suffix>[A-HJ-KM-NPR-Z]?)\s*[:：]\s*"
        r"(?P<right_prefix>[A-Z]?)(?P<right>\d+)(?P<right_suffix>[A-Z]?)\b",
        re.IGNORECASE,
    )
    _ocr_missing_t_prefix_artifact_line_pattern = re.compile(
        r"^\s*1(?P<left>\d{2,})(?P<left_suffix>[A-Z]?)\s*[:：]\s*"
        r"(?P<right_prefix>[A-Z]?)(?P<right>\d+)(?P<right_suffix>[A-Z]?)\b",
        re.IGNORECASE,
    )
    _visual_reference_pattern = re.compile(
        r"(?:图|彩版|图版|fig(?:ure)?|plate)",
        re.IGNORECASE,
    )
    _plate_reference_pattern = re.compile(
        r"(?:彩版|彩图|彩圖|图版|圖版|plate)\s*"
        r"(?P<plate>[〇零一二两兩三四五六七八九十百0-9]+)"
        r"(?:\s*[,，:：\-]\s*"
        r"(?P<item>[〇零一二两兩三四五六七八九十百0-9]+))?",
        re.IGNORECASE,
    )
    _plate_item_pattern = re.compile(
        r"^\s*(?P<item>[〇零一二两兩三四五六七八九十百0-9]+)"
        r"\s*[.．、:：]",
        re.IGNORECASE,
    )
    _reference_digits = {
        "〇": 0,
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "兩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    _reference_units = {"十": 10, "百": 100}
    _measurement_value_pattern = re.compile(
        r"(?P<label>最大径|直径|残高|通高|全高|口径|底径|腹径|孔径|刃宽|足高|耳高|"
        r"高|长|宽|厚|径)\s*(?P<approx>约)?\s*"
        r"(?P<value>\d+(?:[.,]\d+)?(?:\s*[~～〜\-–]\s*\d+(?:[.,]\d+)?)?)\s*"
        r"(?P<unit>厘米|毫米|米|cm|mm|m)?",
        re.IGNORECASE,
    )
    # OCR sometimes collapses ``M1:38`` into ``M138.``; still treat as a new entry.
    _next_artifact_entry_line_pattern = re.compile(
        r"^\s*[A-Z]{1,6}\s*\d+[A-Z]?(?:\s*[:：]\s*[A-Z]?\d+[A-Z]?|[.\u3002、])",
        re.IGNORECASE,
    )
    _visual_parenthetical_pattern = re.compile(
        r"[（(][^()（）]*(?:图|彩版|图版|fig(?:ure)?|plate)[^()（）]*[）)]",
        re.IGNORECASE,
    )
    # Leading short vessel/name clause after the artifact ID, e.g. "陶尊。" / "玉珠。"
    _category_lead_pattern = re.compile(
        r"^[，,、:：\s]*(?P<category>[\u4e00-\u9fffA-Za-z0-9ⅣⅢⅡⅠ]{1,12})"
        r"(?=[。．.；;，,、]|$)"
    )
    _texture_phrase_pattern = re.compile(
        r"(?P<texture>"
        r"(?:泥质|夹砂|夹炭|细泥|粗泥|硬陶)"
        r"[\u4e00-\u9fff]{0,6}"
        r"陶"
        r"|[\u4e00-\u9fff]{0,4}(?:闪玉|玉|石|青铜|铜|铁)"
        r")"
    )
    _strict_artifact_id_pattern = re.compile(
        r"^[A-Z]{1,6}\d+[A-Z]?(?::[A-Z]?\d+[A-Z]?)+$"
    )
    _ocr_digit_confusions = {
        "I": "1",
        "L": "1",
        "|": "1",
        "O": "0",
        "Q": "0",
    }
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
        page_metadata: dict[int, dict[str, Any]] | None = None,
    ) -> FusionOutput:
        reference_index_pages = PageSemantics.reference_index_pages_from_regions(regions)
        if reference_index_pages:
            records = [
                record
                for record in records
                if self._record_source_page(record) not in reference_index_pages
            ]
        self._normalize_artifact_identifiers(records)
        records = self._merge_split_artifact_records(records=records, regions=regions)
        records = self._absorb_color_plate_caption_records(
            records=records,
            regions=regions,
            page_metadata=page_metadata or {},
        )
        self._infer_color_plate_regions(
            job_id=job_id,
            records=records,
            regions=regions,
            page_metadata=page_metadata or {},
            model_run_id=model_run_id,
        )
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
        self.prune_cross_artifact_field_evidence(
            records=records,
            region_by_id=region_by_id,
        )
        plate_matched_records = self._fuse_plate_reference_regions(
            job_id=job_id,
            records=records,
            regions=regions,
            region_by_id=region_by_id,
            relation_by_id=relation_by_id,
            model_run_id=model_run_id,
        )
        for record_index in plate_matched_records:
            expected_counts[record_index] += 1
            matched_counts[record_index] += 1

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
            if record.get("primary_artifact_region_id"):
                for color_region_id in [
                    region_id
                    for region_id in record["region_ids"]
                    if region_by_id.get(region_id, {}).get("kind") == "color_plate"
                    and region_by_id.get(region_id, {}).get("approximate")
                ]:
                    color_relation = self._color_plate_relation(
                        job_id=job_id,
                        color_region_id=color_region_id,
                        artifact_region_id=str(record["primary_artifact_region_id"]),
                        model_run_id=model_run_id,
                        score=float(
                            region_by_id[color_region_id].get("confidence") or 0.96
                        ),
                    )
                    relation_by_id[color_relation["id"]] = color_relation
                    record["relation_ids"] = sorted(
                        set(record["relation_ids"]) | {color_relation["id"]}
                    )
            record["thumbnail_region_id"] = self._select_thumbnail_region_id(
                record=record,
                region_by_id=region_by_id,
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

        self._sanitize_thumbnail_region_ids(records, region_by_id)
        return FusionOutput(records=records, relations=list(relation_by_id.values()))

    @classmethod
    def _sanitize_thumbnail_region_ids(
        cls,
        records: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Drop catalog thumbnails that the crop API cannot serve."""

        for record in records:
            thumb_id = record.get("thumbnail_region_id")
            if not thumb_id:
                record["thumbnail_region_id"] = None
                continue
            region = region_by_id.get(str(thumb_id))
            if region is None:
                record["thumbnail_region_id"] = None
                continue
            if region.get("approximate") and not region.get("crop_object_key"):
                record["thumbnail_region_id"] = None

    @classmethod
    def _normalize_artifact_identifiers(
        cls,
        records: list[dict[str, Any]],
    ) -> None:
        observed: list[str] = []
        for record in records:
            fields = record.get("fields", {})
            artifact_field = fields.get("artifact_id", {}) if isinstance(fields, dict) else {}
            if isinstance(artifact_field, dict):
                for value in (
                    artifact_field.get("value"),
                    artifact_field.get("raw_value"),
                ):
                    compact = cls._normalize_artifact_identifier(value)
                    if compact:
                        observed.append(compact)
            hints = record.get("link_hints", {})
            if isinstance(hints, dict):
                observed.extend(
                    cls._normalize_artifact_identifier(value)
                    for value in hints.get("artifact_ids", [])
                    if cls._normalize_artifact_identifier(value)
                )

        strict_ids = {
            value for value in observed if cls._strict_artifact_id_pattern.fullmatch(value)
        }
        left_counts: dict[str, int] = {}
        for value in observed:
            if not cls._strict_artifact_id_pattern.fullmatch(value):
                continue
            left = value.split(":", 1)[0]
            left_counts[left] = left_counts.get(left, 0) + 1

        for record in records:
            fields = record.get("fields", {})
            artifact_field = fields.get("artifact_id", {}) if isinstance(fields, dict) else {}
            if not isinstance(artifact_field, dict):
                continue
            original = cls._normalize_artifact_identifier(
                artifact_field.get("value") or artifact_field.get("raw_value")
            )
            if not original:
                continue
            canonical = cls._canonical_artifact_identifier(
                original,
                strict_ids=strict_ids,
                left_counts=left_counts,
            )
            artifact_field["value"] = canonical
            if canonical == original:
                continue

            warnings = record.setdefault("warnings", [])
            warning = f"器物编号已按数字位置纠正：{original} → {canonical}"
            if warning not in warnings:
                warnings.append(warning)

            linkage = record.setdefault("linkage", {})
            if isinstance(linkage, dict):
                identity = linkage.setdefault("identity", {})
                if isinstance(identity, dict):
                    identity.setdefault(
                        "artifact_id_raw",
                        artifact_field.get("raw_value") or original,
                    )
                    identity["artifact_id_normalized"] = canonical

            hints = record.setdefault("link_hints", {})
            if isinstance(hints, dict):
                raw_hints = hints.get("artifact_ids", [])
                if not isinstance(raw_hints, list):
                    raw_hints = []
                hints["artifact_ids"] = cls._unique_values(
                    [canonical, *raw_hints, original]
                )
                aliases = hints.get("aliases", [])
                if not isinstance(aliases, list):
                    aliases = []
                hints["aliases"] = cls._unique_values([*aliases, original])

    @classmethod
    def _canonical_artifact_identifier(
        cls,
        value: str,
        *,
        strict_ids: set[str],
        left_counts: dict[str, int],
    ) -> str:
        if cls._strict_artifact_id_pattern.fullmatch(value):
            return value
        candidates = cls._artifact_identifier_digit_variants(value)
        exact = next((candidate for candidate in candidates if candidate in strict_ids), None)
        if exact:
            return exact
        contextual = next(
            (
                candidate
                for candidate in candidates
                if left_counts.get(candidate.split(":", 1)[0], 0) >= 3
            ),
            None,
        )
        return contextual or value

    @classmethod
    def _artifact_identifier_digit_variants(cls, value: str) -> list[str]:
        variants = [value]
        for index, character in enumerate(value):
            replacement = cls._ocr_digit_confusions.get(character)
            if replacement is None or index == 0:
                continue
            variants = [
                *variants,
                *[
                    f"{variant[:index]}{replacement}{variant[index + 1:]}"
                    for variant in variants
                    if variant[index] == character
                ],
            ]
        return sorted(
            {
                variant
                for variant in variants
                if variant != value and cls._strict_artifact_id_pattern.fullmatch(variant)
            },
            key=lambda candidate: sum(
                left != right for left, right in zip(value, candidate, strict=False)
            ),
        )

    @classmethod
    def _merge_split_artifact_records(
        cls,
        *,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        region_by_id = {str(region["id"]): region for region in regions}
        spans = {
            id(record): cls._record_evidence_span(record, region_by_id)
            for record in records
        }
        removed: set[int] = set()
        ordered = sorted(
            records,
            key=lambda record: (
                cls._record_source_page(record),
                (spans[id(record)] or (2.0, 2.0))[0],
            ),
        )
        for follower in ordered:
            if id(follower) in removed or cls._record_artifact_identifier(follower):
                continue
            follower_span = spans[id(follower)]
            if follower_span is None:
                continue
            # A semantic fragment may have lost its artifact_id field even though
            # its OCR line clearly starts a new artifact. That line is a record
            # boundary and must not be folded into the preceding artifact.
            if any(
                cls._region_artifact_identifier(region)
                for region in cls._record_evidence_regions(follower, region_by_id)
            ):
                continue
            page = cls._record_source_page(follower)
            candidates: list[tuple[float, dict[str, Any]]] = []
            for owner in ordered:
                if id(owner) in removed or owner is follower:
                    continue
                if cls._record_source_page(owner) != page:
                    continue
                if not cls._record_artifact_identifier(owner):
                    continue
                owner_span = spans[id(owner)]
                if owner_span is None or owner_span[1] > follower_span[0] + 0.003:
                    continue
                gap = follower_span[0] - owner_span[1]
                if gap <= 0.03:
                    candidates.append((gap, owner))
            if not candidates:
                continue
            _, owner = min(candidates, key=lambda item: item[0])
            owner_regions = cls._record_evidence_regions(owner, region_by_id)
            if not owner_regions:
                continue
            owner_text = cls._region_text(owner_regions[-1])
            if re.search(r"[。！？!?；;）)]\s*$", owner_text):
                continue
            cls._merge_record_values(owner, follower)
            removed.add(id(follower))
            owner_span = cls._record_evidence_span(owner, region_by_id)
            spans[id(owner)] = owner_span

        return [record for record in records if id(record) not in removed]

    @classmethod
    def _merge_record_values(
        cls,
        owner: dict[str, Any],
        follower: dict[str, Any],
    ) -> None:
        owner_fields = owner.setdefault("fields", {})
        follower_fields = follower.get("fields", {})
        if isinstance(owner_fields, dict) and isinstance(follower_fields, dict):
            for key, follower_field in follower_fields.items():
                if not isinstance(follower_field, dict):
                    continue
                owner_field = owner_fields.get(key)
                if not isinstance(owner_field, dict) or not cls._field_has_value(owner_field):
                    owner_fields[key] = copy.deepcopy(follower_field)
                    continue
                if not cls._field_has_value(follower_field):
                    continue
                if key == "morphological_description":
                    for value_key in ("raw_value", "value"):
                        left = str(owner_field.get(value_key) or "").strip()
                        right = str(follower_field.get(value_key) or "").strip()
                        if right and right not in left:
                            owner_field[value_key] = f"{left}{right}"
                owner_evidence = owner_field.setdefault("evidence", [])
                follower_evidence = follower_field.get("evidence", [])
                if isinstance(owner_evidence, list) and isinstance(follower_evidence, list):
                    owner_field["evidence"] = cls._unique_evidence(
                        [*owner_evidence, *copy.deepcopy(follower_evidence)]
                    )

        for key in ("link_hints",):
            owner_values = owner.setdefault(key, {})
            follower_values = follower.get(key, {})
            if not isinstance(owner_values, dict) or not isinstance(follower_values, dict):
                continue
            for hint_key, values in follower_values.items():
                current = owner_values.get(hint_key, [])
                owner_values[hint_key] = cls._unique_values(
                    [
                        *(current if isinstance(current, list) else []),
                        *(values if isinstance(values, list) else []),
                    ]
                )

        owner_linkage = owner.setdefault("linkage", {})
        follower_linkage = follower.get("linkage", {})
        if isinstance(owner_linkage, dict) and isinstance(follower_linkage, dict):
            owner_visual = owner_linkage.setdefault("visual_link", {})
            follower_visual = follower_linkage.get("visual_link", {})
            if isinstance(owner_visual, dict) and isinstance(follower_visual, dict):
                for key, value in follower_visual.items():
                    if not owner_visual.get(key) and value:
                        owner_visual[key] = copy.deepcopy(value)

        for list_key in (
            "region_ids",
            "relation_ids",
            "model_run_ids",
            "source_pages",
            "associated_pages",
        ):
            owner[list_key] = cls._unique_values(
                [
                    *(owner.get(list_key, []) if isinstance(owner.get(list_key), list) else []),
                    *(
                        follower.get(list_key, [])
                        if isinstance(follower.get(list_key), list)
                        else []
                    ),
                ]
            )
        owner["text_evidence"] = cls._unique_evidence(
            [
                *(
                    owner.get("text_evidence", [])
                    if isinstance(owner.get("text_evidence"), list)
                    else []
                ),
                *(
                    follower.get("text_evidence", [])
                    if isinstance(follower.get("text_evidence"), list)
                    else []
                ),
            ]
        )
        warnings = owner.setdefault("warnings", [])
        message = "已合并同一器物被分割到相邻 OCR 行的结构化记录"
        if isinstance(warnings, list) and message not in warnings:
            warnings.append(message)

    @classmethod
    def _infer_color_plate_regions(
        cls,
        *,
        job_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        page_metadata: dict[int, dict[str, Any]],
        model_run_id: str,
    ) -> None:
        color_pages = {
            int(page_no)
            for page_no, metadata in page_metadata.items()
            if isinstance(metadata, dict) and metadata.get("page_type") == "color_plate"
        }
        if not color_pages:
            return
        region_by_id = {str(region["id"]): region for region in regions}
        existing_keys = {
            (
                int(region.get("page", 0)),
                str(region.get("match_key") or ""),
            )
            for region in regions
            if region.get("kind") == "color_plate"
        }
        for record in records:
            artifact_id = cls._record_artifact_identifier(record)
            if not artifact_id:
                continue
            candidate_pages = {
                page
                for page in (
                    cls._record_source_page(record),
                    *(
                        record.get("associated_pages", [])
                        if isinstance(record.get("associated_pages"), list)
                        else []
                    ),
                    *(
                        record.get("source_pages", [])
                        if isinstance(record.get("source_pages"), list)
                        else []
                    ),
                )
                if isinstance(page, int) and page in color_pages
            }
            for page in sorted(candidate_pages):
                match_key = (
                    f"artifact:{cls._normalize_artifact_identifier(artifact_id).casefold()}"
                )
                if (page, match_key) in existing_keys:
                    continue
                anchor = cls._color_plate_anchor(record, page, region_by_id)
                if anchor is None:
                    continue
                digest = hashlib.sha256(
                    f"{job_id}:inferred-color:{page}:{match_key}:{anchor['id']}".encode()
                ).hexdigest()[:24]
                region_id = f"reg_{digest}"
                inferred = {
                    "id": region_id,
                    "document_id": anchor.get("document_id"),
                    "page": page,
                    "kind": "color_plate",
                    "bbox": list(anchor["bbox"]),
                    "bbox_px": None,
                    "text": cls._region_text(anchor),
                    "confidence": 0.96,
                    "source": "ocr_identifier_inference",
                    "model_run_id": model_run_id,
                    "image_id": anchor.get("image_id"),
                    "crop_object_key": None,
                    "approximate": True,
                    "geometry_type": "caption_anchor",
                    "match_key": match_key,
                    "match_reason": f"彩图页题注与器物编号 {artifact_id} 完全一致",
                }
                regions.append(inferred)
                region_by_id[region_id] = inferred
                record["region_ids"] = sorted(
                    set(record.get("region_ids", [])) | {region_id}
                )
                existing_keys.add((page, match_key))

    @classmethod
    def _color_plate_anchor(
        cls,
        record: dict[str, Any],
        page: int,
        region_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        fields = record.get("fields", {})
        if isinstance(fields, dict):
            for field_key in ("figure_caption", "artifact_id", "category"):
                field = fields.get(field_key, {})
                if not isinstance(field, dict):
                    continue
                for evidence in field.get("evidence", []):
                    if not isinstance(evidence, dict) or evidence.get("page") != page:
                        continue
                    region_id = evidence.get("region_id")
                    region = region_by_id.get(str(region_id)) if region_id else None
                    if region is not None and cls._is_bbox(region.get("bbox")):
                        return region
        # After absorbing plate captions, evidence may live only on region_ids.
        for region_id in record.get("region_ids", []) or []:
            region = region_by_id.get(str(region_id))
            if (
                region is not None
                and region.get("page") == page
                and region.get("kind") in {"text", "caption", "number"}
                and cls._is_bbox(region.get("bbox"))
            ):
                return region
        return None

    @classmethod
    def _fuse_plate_reference_regions(
        cls,
        *,
        job_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
        model_run_id: str,
    ) -> set[int]:
        """Link body-text plate/item references to color-plate pages."""

        page_plate_numbers: dict[int, set[int]] = {}
        for region in regions:
            page = region.get("page")
            if not isinstance(page, int):
                continue
            for plate_no, _ in cls._plate_references(cls._region_text(region)):
                page_plate_numbers.setdefault(page, set()).add(plate_no)

        color_candidates: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for region in regions:
            page = region.get("page")
            if region.get("kind") != "color_plate" or not isinstance(page, int):
                continue
            item_no = cls._plate_item_number(cls._region_text(region))
            if item_no is None:
                continue
            for plate_no in page_plate_numbers.get(page, set()):
                color_candidates.setdefault((plate_no, item_no), []).append(region)
        if not color_candidates:
            return set()

        matched_records: set[int] = set()
        for record_index, record in enumerate(records):
            references = {
                reference
                for value in cls._record_plate_reference_values(record)
                for reference in cls._plate_references(value)
                if reference[1] is not None
            }
            if not references:
                continue
            expected_identifier = cls._record_artifact_identifier(record)
            for plate_no, item_no in sorted(references):
                if item_no is None:
                    continue
                evidence = cls._plate_reference_evidence(
                    record=record,
                    plate_no=plate_no,
                    item_no=item_no,
                    region_by_id=region_by_id,
                )
                if evidence is None or not evidence.get("region_id"):
                    continue
                evidence_region_id = str(evidence["region_id"])
                if evidence_region_id not in region_by_id:
                    continue

                ranked_candidates: list[
                    tuple[tuple[int, int, float], dict[str, Any]]
                ] = []
                for candidate in color_candidates.get((plate_no, item_no), []):
                    candidate_identifiers = cls._artifact_identifiers_in_text(
                        " ".join(
                            str(value)
                            for value in (
                                candidate.get("text"),
                                candidate.get("ocr_raw_text"),
                                candidate.get("match_reason"),
                            )
                            if value
                        )
                    )
                    if (
                        expected_identifier
                        and candidate_identifiers
                        and expected_identifier not in candidate_identifiers
                    ):
                        continue
                    exact_identifier = int(
                        bool(
                            expected_identifier
                            and expected_identifier in candidate_identifiers
                        )
                    )
                    structured_match_key = int(
                        str(candidate.get("match_key") or "")
                        == f"artifact:{expected_identifier.casefold()}"
                    )
                    confidence = float(candidate.get("confidence") or 0.0)
                    ranked_candidates.append(
                        (
                            (
                                exact_identifier,
                                structured_match_key,
                                confidence,
                            ),
                            candidate,
                        )
                    )
                if not ranked_candidates:
                    continue
                _, candidate = max(ranked_candidates, key=lambda item: item[0])
                color_region_id = str(candidate["id"])
                score = (
                    0.99
                    if expected_identifier
                    and expected_identifier
                    in cls._artifact_identifiers_in_text(
                        cls._region_text(candidate)
                    )
                    else 0.96
                )
                relation = cls._plate_reference_relation(
                    job_id=job_id,
                    text_region_id=evidence_region_id,
                    color_region_id=color_region_id,
                    plate_no=plate_no,
                    item_no=item_no,
                    model_run_id=model_run_id,
                    score=score,
                )
                relation_by_id[relation["id"]] = relation
                record.setdefault("region_ids", []).extend(
                    [evidence_region_id, color_region_id]
                )
                record.setdefault("relation_ids", []).append(relation["id"])
                evidence["linked_region_ids"] = sorted(
                    set(evidence.get("linked_region_ids", [])) | {color_region_id}
                )
                evidence["relation_ids"] = sorted(
                    set(evidence.get("relation_ids", [])) | {relation["id"]}
                )
                matched_records.add(record_index)
        return matched_records

    @classmethod
    def _record_plate_reference_values(cls, record: dict[str, Any]) -> list[str]:
        values: list[str] = []
        hints = record.get("link_hints", {})
        if isinstance(hints, dict):
            values.extend(
                str(value)
                for value in hints.get("plate_refs", [])
                if str(value).strip()
            )
        fields = record.get("fields", {})
        if not isinstance(fields, dict):
            return values
        caption = fields.get("figure_caption", {})
        if isinstance(caption, dict):
            values.extend(
                str(value)
                for value in (caption.get("value"), caption.get("raw_value"))
                if value and str(value).strip()
            )
            values.extend(
                str(evidence.get("quote"))
                for evidence in caption.get("evidence", [])
                if isinstance(evidence, dict) and evidence.get("quote")
            )
        return values

    @classmethod
    def _plate_reference_evidence(
        cls,
        *,
        record: dict[str, Any],
        plate_no: int,
        item_no: int,
        region_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        fields = record.get("fields", {})
        if not isinstance(fields, dict):
            return None
        ordered_fields = [
            fields.get("figure_caption"),
            fields.get("artifact_id"),
            *fields.values(),
        ]
        seen: set[int] = set()
        for field in ordered_fields:
            if not isinstance(field, dict) or id(field) in seen:
                continue
            seen.add(id(field))
            for evidence in field.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                region = region_by_id.get(str(evidence.get("region_id") or ""))
                text = " ".join(
                    str(value)
                    for value in (
                        evidence.get("quote"),
                        cls._region_text(region) if region is not None else None,
                    )
                    if value
                )
                if (plate_no, item_no) in cls._plate_references(text):
                    return evidence
        return None

    @classmethod
    def _plate_references(cls, value: Any) -> set[tuple[int, int | None]]:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        references: set[tuple[int, int | None]] = set()
        for match in cls._plate_reference_pattern.finditer(normalized):
            plate_no = cls._reference_number(match.group("plate"))
            item_no = cls._reference_number(match.group("item"))
            if plate_no is not None:
                references.add((plate_no, item_no))
        return references

    @classmethod
    def _plate_item_number(cls, value: Any) -> int | None:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        match = cls._plate_item_pattern.search(normalized)
        return cls._reference_number(match.group("item") if match else None)

    @classmethod
    def _reference_number(cls, value: Any) -> int | None:
        normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return int(normalized)
        if not all(
            character in cls._reference_digits
            or character in cls._reference_units
            or character.isdigit()
            for character in normalized
        ):
            return None
        if not any(character in cls._reference_units for character in normalized):
            digits = [
                str(
                    int(character)
                    if character.isdigit()
                    else cls._reference_digits[character]
                )
                for character in normalized
            ]
            return int("".join(digits))

        total = 0
        number = 0
        for character in normalized:
            if character.isdigit():
                number = int(character)
            elif character in cls._reference_digits:
                number = cls._reference_digits[character]
            else:
                total += (number or 1) * cls._reference_units[character]
                number = 0
        return total + number

    @staticmethod
    def _field_has_value(field: dict[str, Any]) -> bool:
        value = field.get("value")
        return value is not None and str(value).strip() != ""

    @classmethod
    def _should_upgrade_field_value(
        cls,
        *,
        field_key: str,
        current_value: str,
        candidate_value: str,
    ) -> bool:
        """Replace truncated LLM fragments when OCR paragraph text is richer."""

        current = unicodedata.normalize("NFKC", str(current_value or "")).strip()
        candidate = unicodedata.normalize("NFKC", str(candidate_value or "")).strip()
        if not candidate or not current:
            return False
        if field_key != "morphological_description":
            return False
        if len(candidate) < max(12, len(current) + 8):
            return False
        compact_current = re.sub(r"\s+", "", current)
        compact_candidate = re.sub(r"\s+", "", candidate)
        if compact_current and compact_current in compact_candidate and len(current) <= 16:
            return True
        return len(current) <= 8 and len(candidate) >= max(24, len(current) * 4)

    @classmethod
    def _record_artifact_identifier(cls, record: dict[str, Any]) -> str:
        fields = record.get("fields", {})
        field = fields.get("artifact_id", {}) if isinstance(fields, dict) else {}
        if not isinstance(field, dict):
            return ""
        return cls._normalize_artifact_identifier(
            field.get("value") or field.get("raw_value")
        )

    @classmethod
    def _record_evidence_regions(
        cls,
        record: dict[str, Any],
        region_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        fields = record.get("fields", {})
        if isinstance(fields, dict):
            for field in fields.values():
                if not isinstance(field, dict):
                    continue
                for evidence in field.get("evidence", []):
                    if not isinstance(evidence, dict) or not evidence.get("region_id"):
                        continue
                    region = region_by_id.get(str(evidence["region_id"]))
                    if region is not None and region.get("kind") == "text":
                        result[str(region["id"])] = region
        return sorted(
            result.values(),
            key=lambda region: (float(region["bbox"][1]), float(region["bbox"][0])),
        )

    @classmethod
    def _record_evidence_span(
        cls,
        record: dict[str, Any],
        region_by_id: dict[str, dict[str, Any]],
    ) -> tuple[float, float] | None:
        evidence_regions = cls._record_evidence_regions(record, region_by_id)
        if not evidence_regions:
            return None
        return (
            min(float(region["bbox"][1]) for region in evidence_regions),
            max(float(region["bbox"][3]) for region in evidence_regions),
        )

    @staticmethod
    def _unique_values(values: list[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for value in values:
            marker = repr(value)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(value)
        return result

    @staticmethod
    def _unique_evidence(values: list[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        for value in values:
            if not isinstance(value, dict):
                continue
            marker = (
                value.get("page"),
                value.get("region_id"),
                value.get("quote"),
            )
            if marker in seen:
                continue
            seen.add(marker)
            result.append(value)
        return result

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
                continuation_text = cls._region_text(continuation)
                if (
                    cls._artifact_identifier_from_text(continuation_text)
                    or cls._looks_like_next_artifact_entry_line(continuation_text)
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
            cls._complete_paragraph_descriptive_fields(
                record=record,
                paragraph_regions=paragraph_regions,
                expected_identifier=expected_identifier,
            )
            record["paragraph_enrichment_version"] = cls.version

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
                if cls._looks_like_next_artifact_entry_line(continuation_text):
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
    def prune_cross_artifact_field_evidence(
        cls,
        *,
        records: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Remove evidence regions that explicitly name a different artifact."""

        artifact_owner_by_region_id = cls._artifact_owner_by_region_id(region_by_id)
        for record in records:
            expected_identifier = cls._record_artifact_identifier(record)
            fields = record.get("fields", {})
            if not expected_identifier or not isinstance(fields, dict):
                continue
            for field in fields.values():
                if not isinstance(field, dict):
                    continue
                evidence_items = field.get("evidence", [])
                if not isinstance(evidence_items, list):
                    continue
                field["evidence"] = [
                    evidence
                    for evidence in evidence_items
                    if not cls._evidence_has_conflicting_artifact_identifier(
                        evidence=evidence,
                        expected_identifier=expected_identifier,
                        region_by_id=region_by_id,
                        artifact_owner_by_region_id=artifact_owner_by_region_id,
                    )
                ]

            text_evidence = record.get("text_evidence", [])
            if isinstance(text_evidence, list):
                record["text_evidence"] = [
                    evidence
                    for evidence in text_evidence
                    if not cls._evidence_has_conflicting_artifact_identifier(
                        evidence=evidence,
                        expected_identifier=expected_identifier,
                        region_by_id=region_by_id,
                        artifact_owner_by_region_id=artifact_owner_by_region_id,
                    )
                ]

    @classmethod
    def _artifact_owner_by_region_id(
        cls,
        region_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        """Assign wrapped OCR lines to the artifact paragraph that owns them."""

        text_regions_by_page: dict[int, list[dict[str, Any]]] = {}
        for region in region_by_id.values():
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

        owners: dict[str, str] = {}
        for page_regions in text_regions_by_page.values():
            for anchor in page_regions:
                identifier = cls._region_artifact_identifier(anchor)
                if not identifier:
                    continue
                anchor_id = str(anchor["id"])
                owners[anchor_id] = identifier
                paragraph_region_ids = {anchor_id}
                current = anchor
                for _ in range(5):
                    continuation = cls._next_wrapped_text_region(
                        current=current,
                        page_regions=page_regions,
                        excluded_ids=paragraph_region_ids,
                    )
                    if continuation is None:
                        break
                    continuation_text = cls._region_text(continuation)
                    if (
                        cls._region_artifact_identifier(continuation)
                        or cls._looks_like_next_artifact_entry_line(continuation_text)
                    ):
                        break
                    continuation_id = str(continuation["id"])
                    owners.setdefault(continuation_id, identifier)
                    paragraph_region_ids.add(continuation_id)
                    current = continuation
        return owners

    @classmethod
    def _looks_like_next_artifact_entry_line(cls, value: Any) -> bool:
        """True when a wrapped OCR line starts a new catalog entry."""

        normalized = unicodedata.normalize("NFKC", str(value or ""))
        if cls._artifact_identifier_from_text(normalized):
            return True
        return bool(cls._next_artifact_entry_line_pattern.match(normalized))

    @classmethod
    def _evidence_has_conflicting_artifact_identifier(
        cls,
        *,
        evidence: Any,
        expected_identifier: str,
        region_by_id: dict[str, dict[str, Any]],
        artifact_owner_by_region_id: dict[str, str],
    ) -> bool:
        if not isinstance(evidence, dict) or not evidence.get("region_id"):
            return False
        region_id = str(evidence["region_id"])
        region = region_by_id.get(region_id)
        if region is None or region.get("kind") != "text":
            return False
        observed_identifier = (
            cls._region_artifact_identifier(region)
            or artifact_owner_by_region_id.get(region_id, "")
        )
        return bool(
            observed_identifier
            and observed_identifier != expected_identifier
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
        return cls._artifact_identifier_from_text(cls._region_text(region))

    @classmethod
    def _artifact_identifier_from_text(cls, value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        match = cls._artifact_line_pattern.search(normalized)
        identifier = cls._normalize_artifact_identifier(match.group(0) if match else value)
        if cls._strict_artifact_id_pattern.fullmatch(identifier):
            return identifier

        tolerant = cls._ocr_tolerant_artifact_line_pattern.search(normalized)
        if tolerant is not None:
            left_digits = "".join(
                cls._ocr_digit_confusions.get(character, character)
                for character in tolerant.group("left").upper()
            )
            identifier = cls._normalize_artifact_identifier(
                f"{tolerant.group('prefix')}{left_digits}"
                f"{tolerant.group('left_suffix')}:{tolerant.group('right_prefix')}"
                f"{tolerant.group('right')}{tolerant.group('right_suffix')}"
            )
        else:
            # PaddleOCR occasionally reads the narrow leading ``T`` in labels
            # such as ``T0302①:03`` as ``1``. NFKC has already expanded the
            # circled digit, so recover the prefix only for a complete,
            # line-leading identifier shape instead of applying a broad text
            # replacement.
            missing_t = cls._ocr_missing_t_prefix_artifact_line_pattern.search(
                normalized
            )
            if missing_t is None:
                return ""
            identifier = cls._normalize_artifact_identifier(
                f"T{missing_t.group('left')}{missing_t.group('left_suffix')}:"
                f"{missing_t.group('right_prefix')}{missing_t.group('right')}"
                f"{missing_t.group('right_suffix')}"
            )
        return (
            identifier
            if cls._strict_artifact_id_pattern.fullmatch(identifier)
            else ""
        )

    @classmethod
    def _artifact_identifiers_in_text(cls, value: Any) -> set[str]:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        identifiers = {
            cls._normalize_artifact_identifier(match.group(1))
            for match in cls._artifact_identifier_pattern.finditer(normalized)
        }
        line_identifier = cls._artifact_identifier_from_text(normalized)
        if line_identifier:
            identifiers.add(line_identifier)
        return {
            identifier
            for identifier in identifiers
            if cls._strict_artifact_id_pattern.fullmatch(identifier)
        }

    @staticmethod
    def _normalize_artifact_identifier(value: Any) -> str:
        text = re.sub(
            r"\s+",
            "",
            unicodedata.normalize("NFKC", str(value or "")).upper(),
        )
        # Color-plate captions often prefix the true ID with a tomb/unit label
        # such as 仲M4:3. Keep the Latin+digit identity for linking/catalog.
        return ResultFusionService._tomb_unit_prefix_pattern.sub("", text)

    @classmethod
    def _color_plate_pages(
        cls,
        regions: list[dict[str, Any]],
        page_metadata: dict[int, dict[str, Any]],
    ) -> set[int]:
        pages = {
            int(region["page"])
            for region in regions
            if region.get("kind") == "color_plate" and isinstance(region.get("page"), int)
        }
        for page_no, metadata in page_metadata.items():
            if isinstance(page_no, int) and isinstance(metadata, dict):
                if metadata.get("page_type") == "color_plate":
                    pages.add(page_no)
        return pages

    @classmethod
    def _is_caption_only_sparse_record(cls, record: dict[str, Any]) -> bool:
        fields = record.get("fields", {})
        if not isinstance(fields, dict):
            return False
        for key in cls._body_field_keys:
            field = fields.get(key)
            if isinstance(field, dict) and cls._field_has_value(field):
                return False
        populated = {
            key
            for key, field in fields.items()
            if isinstance(field, dict) and cls._field_has_value(field)
        }
        if not populated <= {"artifact_id", "figure_caption", "category"}:
            return False
        # Bare artifact-id stubs are not plate captions; require a caption cue.
        return (
            "figure_caption" in populated
            or cls._raw_artifact_id_has_tomb_prefix(record)
            or cls._looks_like_plate_item_caption(record)
        )

    @classmethod
    def _raw_artifact_id_has_tomb_prefix(cls, record: dict[str, Any]) -> bool:
        fields = record.get("fields", {})
        field = fields.get("artifact_id", {}) if isinstance(fields, dict) else {}
        if not isinstance(field, dict):
            return False
        raw = unicodedata.normalize("NFKC", str(field.get("raw_value") or ""))
        return bool(re.match(r"^[\u4e00-\u9fff]{1,2}[A-Za-z]", raw))

    @classmethod
    def _looks_like_plate_item_caption(cls, record: dict[str, Any]) -> bool:
        fields = record.get("fields", {})
        if not isinstance(fields, dict):
            return False
        for key in ("figure_caption", "artifact_id", "category"):
            field = fields.get(key)
            if not isinstance(field, dict):
                continue
            for value in (field.get("raw_value"), field.get("value")):
                text = unicodedata.normalize("NFKC", str(value or "")).strip()
                if text and cls._plate_item_caption_pattern.search(text):
                    return True
            evidence_items = field.get("evidence", [])
            if not isinstance(evidence_items, list):
                continue
            for evidence in evidence_items:
                if not isinstance(evidence, dict):
                    continue
                quote = unicodedata.normalize("NFKC", str(evidence.get("quote") or "")).strip()
                if quote and cls._plate_item_caption_pattern.search(quote):
                    return True
        return False

    @classmethod
    def _absorb_color_plate_caption_records(
        cls,
        *,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        page_metadata: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Color-plate OCR is linkage only — do not keep sparse caption cards.

        Captions such as ``4.玉锥形饰（仲M4：3）`` should attach to the body-text
        ``M4:3`` record (or be dropped), never appear as empty catalog entities.
        """

        color_pages = cls._color_plate_pages(regions, page_metadata)
        rich: list[dict[str, Any]] = []
        sparse: list[dict[str, Any]] = []
        for record in records:
            page = cls._record_source_page(record)
            is_sparse = cls._is_caption_only_sparse_record(record)
            plate_like = (
                page in color_pages
                or str(record.get("record_type") or "") == "artifact_color_plate"
                or cls._raw_artifact_id_has_tomb_prefix(record)
                or cls._looks_like_plate_item_caption(record)
            )
            if is_sparse and plate_like:
                sparse.append(record)
            else:
                rich.append(record)

        owners_by_id: dict[str, list[dict[str, Any]]] = {}
        for record in rich:
            artifact_id = cls._record_artifact_identifier(record)
            if artifact_id:
                owners_by_id.setdefault(artifact_id, []).append(record)

        def owner_score(record: dict[str, Any]) -> int:
            fields = record.get("fields", {})
            if not isinstance(fields, dict):
                return 0
            score = 0
            for key in cls._body_field_keys:
                field = fields.get(key)
                if isinstance(field, dict) and cls._field_has_value(field):
                    score += 10
            return score

        for caption_record in sparse:
            artifact_id = cls._record_artifact_identifier(caption_record)
            owners = owners_by_id.get(artifact_id, [])
            if not owners:
                # No body-text card exists — still drop the empty plate caption card.
                continue
            owner = max(owners, key=owner_score)
            # Linkage only: never promote color-plate OCR into body fields / text_evidence.
            caption_region_ids = [
                *(
                    caption_record.get("region_ids", [])
                    if isinstance(caption_record.get("region_ids"), list)
                    else []
                )
            ]
            caption_fields = caption_record.get("fields", {})
            if isinstance(caption_fields, dict):
                for field in caption_fields.values():
                    if not isinstance(field, dict):
                        continue
                    for evidence in field.get("evidence", []) or []:
                        if isinstance(evidence, dict) and evidence.get("region_id"):
                            caption_region_ids.append(str(evidence["region_id"]))
            owner["region_ids"] = cls._unique_values(
                [
                    *(
                        owner.get("region_ids", [])
                        if isinstance(owner.get("region_ids"), list)
                        else []
                    ),
                    *caption_region_ids,
                ]
            )
            for list_key in ("relation_ids", "model_run_ids"):
                owner[list_key] = cls._unique_values(
                    [
                        *(
                            owner.get(list_key, [])
                            if isinstance(owner.get(list_key), list)
                            else []
                        ),
                        *(
                            caption_record.get(list_key, [])
                            if isinstance(caption_record.get(list_key), list)
                            else []
                        ),
                    ]
                )
            # Keep plate pages on associated_pages only — not source_pages —
            # so paragraph OCR never anchors on caption lines.
            caption_pages = [
                page
                for page in (
                    *(
                        caption_record.get("source_pages", [])
                        if isinstance(caption_record.get("source_pages"), list)
                        else []
                    ),
                    *(
                        caption_record.get("associated_pages", [])
                        if isinstance(caption_record.get("associated_pages"), list)
                        else []
                    ),
                )
                if isinstance(page, int)
            ]
            # Final fuse() recomputes associated_pages from region pages; keep this
            # interim list for inference that runs before that pass.
            owner["associated_pages"] = cls._unique_values(
                [
                    *(
                        owner.get("associated_pages", [])
                        if isinstance(owner.get("associated_pages"), list)
                        else []
                    ),
                    *(
                        owner.get("source_pages", [])
                        if isinstance(owner.get("source_pages"), list)
                        else []
                    ),
                    *caption_pages,
                ]
            )
            hints = owner.setdefault("link_hints", {})
            caption_hints = caption_record.get("link_hints", {})
            if isinstance(hints, dict) and isinstance(caption_hints, dict):
                for hint_key, values in caption_hints.items():
                    current = hints.get(hint_key, [])
                    hints[hint_key] = cls._unique_values(
                        [
                            *(current if isinstance(current, list) else []),
                            *(values if isinstance(values, list) else []),
                        ]
                    )
            if isinstance(hints, dict):
                caption_field = (caption_record.get("fields") or {}).get(
                    "figure_caption", {}
                )
                caption_value = None
                if isinstance(caption_field, dict):
                    caption_value = caption_field.get("value") or caption_field.get(
                        "raw_value"
                    )
                if caption_value:
                    hints["caption_texts"] = cls._unique_values(
                        [*hints.get("caption_texts", []), caption_value]
                    )
                raw_id = (
                    (caption_record.get("fields") or {})
                    .get("artifact_id", {})
                    .get("raw_value")
                )
                if raw_id:
                    hints["aliases"] = cls._unique_values(
                        [*hints.get("aliases", []), str(raw_id)]
                    )
            owner_linkage = owner.setdefault("linkage", {})
            caption_linkage = caption_record.get("linkage", {})
            if isinstance(owner_linkage, dict) and isinstance(caption_linkage, dict):
                owner_visual = owner_linkage.setdefault("visual_link", {})
                caption_visual = caption_linkage.get("visual_link", {})
                if isinstance(owner_visual, dict) and isinstance(caption_visual, dict):
                    for key, value in caption_visual.items():
                        if not owner_visual.get(key) and value:
                            owner_visual[key] = copy.deepcopy(value)
            warnings = owner.setdefault("warnings", [])
            warning = "彩图页注记仅用于关联，未作为文本证据"
            if warning not in warnings:
                warnings.append(warning)

        return rich

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
        if not matches:
            return
        explicit_units = [match.group("unit") for match in matches if match.group("unit")]
        shared_unit = ""
        if explicit_units:
            shared_unit = cls._normalize_measurement_unit(explicit_units[-1])
        else:
            # Units are often wrapped onto the next OCR line: ``厚0.4~0.6`` / ``厘米。``
            combined_text = "".join(
                cls._region_text(region) for region in paragraph_regions
            )
            # Prefer multi-character units. A bare ``米``/``m`` alternative would
            # incorrectly win inside ``厘米`` with some regex engines/flags.
            trailing_unit = re.search(
                r"(厘米|毫米|cm|mm)",
                unicodedata.normalize("NFKC", combined_text),
                flags=re.IGNORECASE,
            )
            if trailing_unit is None:
                return
            shared_unit = cls._normalize_measurement_unit(trailing_unit.group(1))
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

    @classmethod
    def _complete_paragraph_descriptive_fields(
        cls,
        *,
        record: dict[str, Any],
        paragraph_regions: list[dict[str, Any]],
        expected_identifier: str,
    ) -> None:
        """Backfill category / texture / morphology from OCR paragraphs.

        Semantic extraction often stores only ``artifact_id`` (e.g. ``M1:37``)
        while the full vessel prose lives in consecutive text regions. Those
        lines are already assembled into ``text_evidence``; mirror measurements
        / caption completion so card fields are not left sparse.
        """

        fields = record.get("fields")
        if not isinstance(fields, dict) or not paragraph_regions:
            return

        combined_text = "".join(cls._region_text(region) for region in paragraph_regions)
        remainder = cls._descriptive_paragraph_remainder(
            combined_text,
            expected_identifier=expected_identifier,
        )
        if not remainder:
            return

        category = ""
        category_match = cls._category_lead_pattern.match(remainder)
        if category_match is not None:
            category = category_match.group("category").strip()
            # Avoid treating a texture/material clause as the category.
            if cls._texture_phrase_pattern.fullmatch(category):
                category = ""
            elif len(category) > 8 and any(
                marker in category for marker in ("口", "腹", "底", "足", "领", "沿", "唇")
            ):
                category = ""

        working = remainder
        if category:
            working = working[category_match.end() :].lstrip("。．.；;，,、 ")

        texture = ""
        texture_match = cls._texture_phrase_pattern.search(working)
        if texture_match is not None:
            texture = texture_match.group("texture").strip()
            # Prefer a clause that stands alone near the start (器类后的质地句).
            if texture_match.start() > 24:
                texture = ""

        morphology = working
        if texture:
            morphology = (
                morphology[: texture_match.start()] + morphology[texture_match.end() :]
            )
        morphology = morphology.strip(" ，,、:：;；。．.")
        morphology = re.sub(r"[、,，；;\s]{2,}", "，", morphology).strip("，,、;；")

        if category:
            cls._fill_field_from_paragraph(
                fields=fields,
                field_key="category",
                value=category,
                raw_value=category,
                paragraph_regions=paragraph_regions,
                quote=category,
            )
        if texture:
            cls._fill_field_from_paragraph(
                fields=fields,
                field_key="texture",
                value=texture,
                raw_value=texture,
                paragraph_regions=paragraph_regions,
                quote=texture,
            )
        if morphology and len(morphology) >= 2:
            cls._fill_field_from_paragraph(
                fields=fields,
                field_key="morphological_description",
                value=morphology,
                raw_value=morphology,
                paragraph_regions=paragraph_regions,
                quote=morphology[:80],
            )

    @classmethod
    def _descriptive_paragraph_remainder(
        cls,
        text: str,
        *,
        expected_identifier: str,
    ) -> str:
        normalized = unicodedata.normalize("NFKC", str(text or ""))
        normalized = cls._visual_parenthetical_pattern.sub("", normalized)
        normalized = cls._measurement_value_pattern.sub("", normalized)
        normalized = re.sub(r"[、,，；;\s]*(?:厘米|毫米|米|cm|mm|m)\b", "", normalized, flags=re.I)
        normalized = re.sub(r"[（(]\s*[）)]", "", normalized)

        if expected_identifier:
            id_pattern = re.compile(
                re.escape(expected_identifier).replace(r"\:", r"\s*[:：]\s*"),
                re.IGNORECASE,
            )
            normalized = id_pattern.sub("", normalized, count=1)
        else:
            leading = cls._artifact_identifier_pattern.match(normalized)
            if leading is not None:
                normalized = normalized[leading.end() :]

        return normalized.strip(" ，,、:：;；。．.")

    @classmethod
    def _fill_field_from_paragraph(
        cls,
        *,
        fields: dict[str, Any],
        field_key: str,
        value: str,
        raw_value: str,
        paragraph_regions: list[dict[str, Any]],
        quote: str,
    ) -> None:
        field = fields.get(field_key)
        if not isinstance(field, dict):
            field = {
                "raw_value": None,
                "value": None,
                "status": "missing",
                "evidence": [],
            }
            fields[field_key] = field
        if cls._field_has_value(field) and not cls._should_upgrade_field_value(
            field_key=field_key,
            current_value=str(field.get("value") or field.get("raw_value") or ""),
            candidate_value=value,
        ):
            return

        field["raw_value"] = raw_value
        field["value"] = value
        if field.get("status") in {None, "missing", "absent"} or field_key == (
            "morphological_description"
        ):
            field["status"] = "valid"

        evidence = field.setdefault("evidence", [])
        if not isinstance(evidence, list):
            field["evidence"] = evidence = []
        existing_region_ids = {
            str(item.get("region_id"))
            for item in evidence
            if isinstance(item, dict) and item.get("region_id")
        }
        for region in paragraph_regions:
            region_id = str(region["id"])
            region_text = cls._region_text(region)
            if region_id in existing_region_ids:
                continue
            if quote and quote not in region_text and value not in region_text:
                # Still attach the anchor/continuation lines that built the paragraph.
                if region is not paragraph_regions[0] and region is not paragraph_regions[-1]:
                    continue
            item = cls._text_region_evidence(region)
            item["quote"] = quote if quote in region_text else region_text
            evidence.append(item)
            existing_region_ids.add(region_id)

    @staticmethod
    def _normalize_measurement_unit(value: str) -> str:
        return {
            "厘米": "cm",
            "毫米": "mm",
            "米": "m",
        }.get(value.casefold(), value.casefold())

    @classmethod
    def _visual_candidate_identifier_compatibility(
        cls,
        *,
        record: dict[str, Any],
        candidate: dict[str, Any],
        candidate_identifiers_by_id: dict[str, set[str]],
        page_number_identifiers: dict[int, set[str]],
    ) -> bool | None:
        """Check whether a crop's detected number agrees with the record ID."""

        candidate_identifiers = candidate_identifiers_by_id.get(
            str(candidate.get("id", "")),
            set(),
        )
        expected_identifier = cls._record_artifact_identifier(record)
        if not cls._strict_artifact_id_pattern.fullmatch(expected_identifier):
            # A crop carrying an explicit detected number belongs to the record
            # with that identifier. Do not let a nearby sparse/id-less record
            # claim it through nearest-visual fallback.
            if candidate_identifiers:
                return False
            page = candidate.get("page")
            # Use `in` rather than truthiness: an empty identifier set still
            # means the page has number detectors and must not be scavenged.
            if isinstance(page, int) and page in page_number_identifiers:
                # Even without a number_of edge, crops on a numbered plate page
                # must not be claimed by id-less fragments from nearby pages.
                return False
            return None

        if candidate_identifiers:
            return expected_identifier in candidate_identifiers

        page = candidate.get("page")
        if isinstance(page, int) and page in page_number_identifiers:
            # This is an indexed multi-artifact page, but the candidate has no
            # exact number edge. Proximity is insufficient to choose a crop.
            return False
        return None

    @classmethod
    def _visual_identifier_indexes(
        cls,
        *,
        regions: list[dict[str, Any]],
        region_by_id: dict[str, dict[str, Any]],
        relation_by_id: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, set[str]], dict[int, set[str]]]:
        """Build constant-time visual identifier lookups for fallback scoring."""

        candidate_identifiers_by_id: dict[str, set[str]] = {}
        page_number_identifiers: dict[int, set[str]] = {}
        for region in regions:
            region_id = str(region.get("id", ""))
            identifiers = cls._artifact_identifiers_in_text(
                " ".join(
                    str(value)
                    for value in (
                        region.get("text"),
                        region.get("ocr_raw_text"),
                        region.get("match_reason"),
                    )
                    if value
                )
            )
            if identifiers:
                candidate_identifiers_by_id[region_id] = identifiers
            if region.get("kind") == "number" and isinstance(region.get("page"), int):
                # Always register the page, even when OCR failed to parse an ID.
                page_number_identifiers.setdefault(int(region["page"]), set()).update(
                    identifiers
                )

        for relation in relation_by_id.values():
            if relation.get("relation_type") != "number_of":
                continue
            source_id = str(relation.get("source_region_id", ""))
            target_id = str(relation.get("target_region_id", ""))
            source = region_by_id.get(source_id)
            target = region_by_id.get(target_id)
            if source is None or target is None:
                continue
            if source.get("kind") == "number":
                number, visual = source, target
            elif target.get("kind") == "number":
                number, visual = target, source
            else:
                continue
            number_identifiers = cls._artifact_identifiers_in_text(
                cls._region_text(number)
            )
            if number_identifiers:
                candidate_identifiers_by_id.setdefault(
                    str(visual["id"]),
                    set(),
                ).update(number_identifiers)
        return candidate_identifiers_by_id, page_number_identifiers

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

        (
            candidate_identifiers_by_id,
            page_number_identifiers,
        ) = self._visual_identifier_indexes(
            regions=regions,
            region_by_id=region_by_id,
            relation_by_id=relation_by_id,
        )
        scores: list[list[float]] = []
        for entry in entries:
            entry_center = self._center(entry["bbox"])
            row: list[float] = []
            for candidate in candidates:
                identifier_compatibility = (
                    self._visual_candidate_identifier_compatibility(
                        record=records[entry["record_index"]],
                        candidate=candidate,
                        candidate_identifiers_by_id=candidate_identifiers_by_id,
                        page_number_identifiers=page_number_identifiers,
                    )
                )
                if identifier_compatibility is False:
                    row.append(0.0)
                    continue
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
                identifier_bonus = 0.2 if identifier_compatibility is True else 0.0
                score = (
                    0.78 * distance_score
                    + 0.22 * page_score
                    + above_caption_bonus
                    + identifier_bonus
                )
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
            if not number_ids:
                # A direct caption_of/color_plate_of edge identifies a single
                # visual and does not need item-number disambiguation.
                continue
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
            if (
                best_number is None
                or best_number[0] == 0
                or (
                    artifact_hints
                    and best_number[0] < 2
                    and bool(
                        self._artifact_identifiers_in_text(
                            self._region_text(best_number[3])
                        )
                    )
                )
            ):
                if artifact_hints:
                    # An explicit artifact identifier may not inherit every crop
                    # under a shared caption when none of its number labels match.
                    anchored.pop(region_id, None)
                continue

            _, item_score, hint_key, number = best_number
            anchored.pop(region_id, None)
            anchored[str(number["id"])] = (
                0.55 * caption_score + 0.45 * item_score,
                hint_key,
                number,
            )
        return anchored

    @classmethod
    def _select_thumbnail_region_id(
        cls,
        *,
        record: dict[str, Any],
        region_by_id: dict[str, dict[str, Any]],
    ) -> str | None:
        """Choose a catalog thumbnail that the crop API can actually serve.

        Approximate color-plate anchors are useful for linking, but they have no
        `crop_object_key` and must never become the catalog `<img>` source.
        """

        visual_kinds = {"artifact", "line_drawing", "color_plate", "grave_drawing"}
        priority = {"artifact": 0, "line_drawing": 1, "color_plate": 2, "grave_drawing": 3}
        source_pages = {
            int(page)
            for page in record.get("source_pages", [])
            if isinstance(page, int) or str(page).isdigit()
        }

        def usable(region_id: Any) -> dict[str, Any] | None:
            if not region_id:
                return None
            region = region_by_id.get(str(region_id))
            if region is None or region.get("kind") not in visual_kinds:
                return None
            # Inferred anchors without a crop file always 404 in /regions/.../crop.
            if region.get("approximate") and not region.get("crop_object_key"):
                return None
            return region

        primary = usable(record.get("primary_artifact_region_id"))
        if primary is not None:
            return str(primary["id"])

        candidates: list[tuple[int, int, int, str]] = []
        for region_id in record.get("region_ids", []):
            region = usable(region_id)
            if region is None:
                continue
            page = region.get("page")
            crop_rank = 0 if region.get("crop_object_key") else 1
            page_rank = 0 if isinstance(page, int) and page in source_pages else 1
            kind_rank = priority.get(str(region.get("kind")), 99)
            candidates.append((crop_rank, page_rank, kind_rank, str(region["id"])))
        if not candidates:
            return None
        return min(candidates)[3]

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

        expected_values = cls._artifact_identifiers_in_text(expected)
        observed_values = cls._artifact_identifiers_in_text(observed)
        if not expected_values or not observed_values:
            return 0.0
        return 1.0 if expected_values & observed_values else 0.0

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
        if (
            hint_key in {"artifact_ids", "aliases"}
            and cls._artifact_identifiers_in_text(hint)
        ):
            return cls._identifier_text_score(hint, region_text)
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

    def _color_plate_relation(
        self,
        *,
        job_id: str,
        color_region_id: str,
        artifact_region_id: str,
        model_run_id: str,
        score: float,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(
            f"{job_id}:color_plate_of:{color_region_id}:{artifact_region_id}".encode()
        ).hexdigest()[:24]
        return {
            "id": f"rel_{digest}",
            "source_region_id": color_region_id,
            "target_region_id": artifact_region_id,
            "relation_type": "color_plate_of",
            "score": round(score, 6),
            "method": "exact_artifact_id_color_page",
            "version": self.version,
            "model_run_id": model_run_id,
            "review_status": "unreviewed",
        }

    @classmethod
    def _plate_reference_relation(
        cls,
        *,
        job_id: str,
        text_region_id: str,
        color_region_id: str,
        plate_no: int,
        item_no: int,
        model_run_id: str,
        score: float,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(
            (
                f"{job_id}:plate_reference_to_color:{text_region_id}:"
                f"{color_region_id}:{plate_no}:{item_no}"
            ).encode()
        ).hexdigest()[:24]
        return {
            "id": f"rel_{digest}",
            "source_region_id": text_region_id,
            "target_region_id": color_region_id,
            "relation_type": "plate_reference_to_color",
            "score": round(score, 6),
            "method": "exact_plate_item_reference",
            "version": cls.version,
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
