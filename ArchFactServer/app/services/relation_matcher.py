import hashlib
import math
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.services.visual_reference import caption_number_match


@dataclass(frozen=True, slots=True)
class RelationRule:
    source_kind: str
    target_kind: str
    relation_type: str


DEFAULT_RELATION_RULES = (
    RelationRule("number", "artifact", "number_of"),
    RelationRule("caption", "artifact", "caption_of"),
    RelationRule("color_plate", "artifact", "color_plate_of"),
    RelationRule("line_drawing", "artifact", "drawing_of"),
)


@dataclass(frozen=True, slots=True)
class RelationMatcherConfig:
    min_score: float
    max_distance: float
    group_containment_threshold: float
    layout_weight: float
    distance_weight: float
    overlap_weight: float
    confidence_weight: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "RelationMatcherConfig":
        return cls(
            min_score=settings.relation_matching_min_score,
            max_distance=settings.relation_matching_max_distance,
            group_containment_threshold=settings.relation_group_containment_threshold,
            layout_weight=settings.relation_layout_weight,
            distance_weight=settings.relation_distance_weight,
            overlap_weight=settings.relation_overlap_weight,
            confidence_weight=settings.relation_confidence_weight,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "min_score": self.min_score,
            "max_distance": self.max_distance,
            "group_containment_threshold": self.group_containment_threshold,
            "layout_weight": self.layout_weight,
            "distance_weight": self.distance_weight,
            "overlap_weight": self.overlap_weight,
            "confidence_weight": self.confidence_weight,
        }


class RelationMatcher:
    provider = "archfact"
    model = "group-aware-region-matcher"
    version = "5"
    _containable_kinds = {
        "artifact",
        "number",
        "caption",
        "grave_drawing",
        "line_drawing",
        "color_plate",
    }

    def __init__(
        self,
        config: RelationMatcherConfig,
        rules: tuple[RelationRule, ...] = DEFAULT_RELATION_RULES,
    ) -> None:
        self.config = config
        self.rules = rules

    def match_page(
        self,
        *,
        job_id: str,
        page_no: int,
        regions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        page_regions = [region for region in regions if int(region.get("page", -1)) == page_no]
        relations: list[dict[str, Any]] = []
        groups = [region for region in page_regions if region.get("kind") == "group"]
        group_by_region: dict[str, str] = {}
        containment_scores: dict[str, float] = {}

        for region in page_regions:
            if region.get("kind") not in self._containable_kinds:
                continue
            best_group, score = self._best_group(region, groups)
            if best_group is None or score < self.config.group_containment_threshold:
                continue
            region_id = str(region["id"])
            group_by_region[region_id] = str(best_group["id"])
            containment_scores[region_id] = score
            relations.append(
                self._build_relation(
                    job_id=job_id,
                    relation_type="contains",
                    source=best_group,
                    target=region,
                    score=score,
                    method="spatial_containment",
                )
            )

        captions = [region for region in page_regions if region.get("kind") == "caption"]
        numbers = [region for region in page_regions if region.get("kind") == "number"]
        artifacts = [region for region in page_regions if region.get("kind") == "artifact"]
        caption_by_region: dict[str, str] = {}
        if captions:
            caption_by_region, caption_scope_scores = self._assign_caption_scopes(
                captions=captions,
                regions=[*numbers, *artifacts],
            )
            caption_by_id = {str(caption["id"]): caption for caption in captions}
            for number in numbers:
                caption_id = caption_by_region.get(str(number["id"]))
                caption = caption_by_id.get(caption_id or "")
                if caption is None:
                    continue
                relations.append(
                    self._build_relation(
                        job_id=job_id,
                        relation_type="caption_to_number",
                        source=caption,
                        target=number,
                        score=caption_scope_scores[str(number["id"])],
                        method=(
                            "caption_ocr_scope"
                            if caption_number_match(caption, number) == 1.0
                            else "caption_scope_fallback"
                        ),
                    )
                )

            for caption in captions:
                caption_id = str(caption["id"])
                bucket_numbers = [
                    number
                    for number in numbers
                    if caption_by_region.get(str(number["id"])) == caption_id
                ]
                bucket_artifacts = [
                    artifact
                    for artifact in artifacts
                    if caption_by_region.get(str(artifact["id"])) == caption_id
                ]
                assignment_method = (
                    "caption_ocr_constrained_assignment"
                    if any(
                        caption_number_match(caption, number) == 1.0 for number in bucket_numbers
                    )
                    else "caption_constrained_assignment"
                )
                relations.extend(
                    self._match_number_artifacts(
                        job_id=job_id,
                        numbers=bucket_numbers,
                        artifacts=bucket_artifacts,
                        method=assignment_method,
                    )
                )

            assigned_number_ids = set(caption_by_region) & {str(number["id"]) for number in numbers}
            assigned_artifact_ids = set(caption_by_region) & {
                str(artifact["id"]) for artifact in artifacts
            }
            unassigned_numbers = [
                number for number in numbers if str(number["id"]) not in assigned_number_ids
            ]
            unassigned_artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact["id"]) not in assigned_artifact_ids
            ]
            relations.extend(
                self._match_number_artifacts(
                    job_id=job_id,
                    numbers=unassigned_numbers,
                    artifacts=unassigned_artifacts,
                    method="directional_assignment_unscoped",
                )
            )
        else:
            bucket_ids: set[str | None] = {None}
            if groups:
                bucket_ids.update(str(group["id"]) for group in groups)
            for group_id in bucket_ids:
                bucket_numbers = [
                    number
                    for number in numbers
                    if group_by_region.get(str(number["id"])) == group_id
                ]
                bucket_artifacts = [
                    artifact
                    for artifact in artifacts
                    if group_by_region.get(str(artifact["id"])) == group_id
                ]
                relations.extend(
                    self._match_number_artifacts(
                        job_id=job_id,
                        numbers=bucket_numbers,
                        artifacts=bucket_artifacts,
                        method=(
                            "directional_assignment_grouped"
                            if group_id is not None
                            else "directional_assignment"
                        ),
                    )
                )

        for rule in self.rules:
            sources = [region for region in page_regions if region.get("kind") == rule.source_kind]
            targets = [region for region in page_regions if region.get("kind") == rule.target_kind]
            if not sources or not targets:
                continue

            # Number-to-artifact matching is handled above with caption scope and
            # a stricter directional geometry model.
            if rule.source_kind == "number" and rule.target_kind == "artifact":
                continue

            if groups and rule.source_kind == "caption" and rule.target_kind == "artifact":
                relations.extend(
                    self._match_group_captions(
                        job_id=job_id,
                        groups=groups,
                        captions=sources,
                        group_by_region=group_by_region,
                        containment_scores=containment_scores,
                    )
                )
                sources = [source for source in sources if source["id"] not in group_by_region]
                targets = [target for target in targets if target["id"] not in group_by_region]
                relations.extend(
                    self._match_assignment(
                        job_id=job_id,
                        rule=rule,
                        sources=sources,
                        targets=targets,
                        method="global_assignment_ungrouped",
                    )
                )
                continue

            bucket_ids: set[str | None] = {None}
            if groups:
                bucket_ids.update(group["id"] for group in groups)
            for group_id in bucket_ids:
                bucket_sources = [
                    source for source in sources if group_by_region.get(source["id"]) == group_id
                ]
                bucket_targets = [
                    target for target in targets if group_by_region.get(target["id"]) == group_id
                ]
                relations.extend(
                    self._match_assignment(
                        job_id=job_id,
                        rule=rule,
                        sources=bucket_sources,
                        targets=bucket_targets,
                        method=(
                            "global_assignment"
                            if not groups
                            else (
                                "global_assignment_grouped"
                                if group_id is not None
                                else "global_assignment_ungrouped"
                            )
                        ),
                    )
                )
        return relations

    def _assign_caption_scopes(
        self,
        *,
        captions: list[dict[str, Any]],
        regions: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, float]]:
        assignments: dict[str, str] = {}
        scores: dict[str, float] = {}
        for region in regions:
            candidates = [
                (caption, self._caption_region_score(caption, region)) for caption in captions
            ]
            caption, score = max(candidates, key=lambda item: item[1])
            if score < max(0.35, self.config.min_score * 0.8):
                continue
            region_id = str(region["id"])
            assignments[region_id] = str(caption["id"])
            scores[region_id] = score
        return assignments, scores

    def _caption_region_score(
        self,
        caption: dict[str, Any],
        region: dict[str, Any],
    ) -> float:
        layout_score = self._caption_scope_score(caption, region)
        if region.get("kind") != "number" or layout_score <= 0:
            return layout_score

        text_score = caption_number_match(caption, region)
        if text_score is None:
            return layout_score
        if text_score == 0:
            # Trusted OCR disagreement is stronger than layout proximity. Keep a
            # small score so the caller can reject it and leave the link for review.
            return layout_score * 0.2
        return min(1.0, 0.55 * text_score + 0.45 * layout_score)

    def _match_number_artifacts(
        self,
        *,
        job_id: str,
        numbers: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        method: str,
    ) -> list[dict[str, Any]]:
        if not numbers or not artifacts:
            return []
        scores = [
            [self._number_artifact_score(number, artifact) for artifact in artifacts]
            for number in numbers
        ]
        return [
            self._build_relation(
                job_id=job_id,
                relation_type="number_of",
                source=numbers[number_index],
                target=artifacts[artifact_index],
                score=score,
                method=method,
            )
            for number_index, artifact_index, score in self._maximum_assignment(
                scores,
                min_score=self.config.min_score,
            )
        ]

    def _caption_scope_score(
        self,
        caption: dict[str, Any],
        region: dict[str, Any],
    ) -> float:
        caption_bbox = caption["bbox"]
        region_bbox = region["bbox"]
        caption_center = self._center(caption_bbox)
        region_center = self._center(region_bbox)

        # Archaeological plate captions normally sit directly below the visual
        # group. Reject regions substantially below a caption.
        if region_center[1] > caption_bbox[3] + 0.04:
            return 0.0
        horizontal_gap = max(
            caption_bbox[0] - region_center[0],
            region_center[0] - caption_bbox[2],
            0.0,
        )
        if horizontal_gap > 0.12:
            return 0.0
        horizontal_scope = max(0.0, 1.0 - horizontal_gap / 0.12)
        caption_width = max(0.08, caption_bbox[2] - caption_bbox[0])
        center_alignment = max(
            0.0,
            1.0 - abs(region_center[0] - caption_center[0]) / (caption_width * 0.9),
        )
        vertical_gap = max(0.0, caption_bbox[1] - region_bbox[3])
        vertical_score = max(0.0, 1.0 - vertical_gap / 0.35)
        confidence_score = (
            self._confidence(caption.get("confidence")) + self._confidence(region.get("confidence"))
        ) / 2
        return (
            0.45 * horizontal_scope
            + 0.30 * center_alignment
            + 0.15 * vertical_score
            + 0.10 * confidence_score
        )

    def _number_artifact_score(
        self,
        number: dict[str, Any],
        artifact: dict[str, Any],
    ) -> float:
        number_bbox = number["bbox"]
        artifact_bbox = artifact["bbox"]
        number_center = self._center(number_bbox)
        artifact_center = self._center(artifact_bbox)
        artifact_width = artifact_bbox[2] - artifact_bbox[0]
        artifact_height = artifact_bbox[3] - artifact_bbox[1]

        # Sequence labels are expected at the lower part of an artifact or just
        # beneath it. This blocks visually close labels from the row above.
        if number_center[1] < artifact_center[1] - max(0.015, artifact_height * 0.15):
            return 0.0
        horizontal_distance = abs(number_center[0] - artifact_center[0])
        horizontal_limit = max(0.055, artifact_width * 1.1)
        if horizontal_distance > horizontal_limit:
            return 0.0
        bottom_distance = abs(number_center[1] - artifact_bbox[3])
        vertical_limit = max(0.055, artifact_height * 0.9)
        if bottom_distance > vertical_limit:
            return 0.0

        horizontal_score = max(0.0, 1.0 - horizontal_distance / horizontal_limit)
        vertical_score = max(0.0, 1.0 - bottom_distance / vertical_limit)
        overlap_score = self._axis_overlap(
            number_bbox[0],
            number_bbox[2],
            artifact_bbox[0],
            artifact_bbox[2],
        )
        confidence_score = (
            self._confidence(number.get("confidence"))
            + self._confidence(artifact.get("confidence"))
        ) / 2
        return (
            0.35 * horizontal_score
            + 0.35 * vertical_score
            + 0.15 * overlap_score
            + 0.15 * confidence_score
        )

    def _match_group_captions(
        self,
        *,
        job_id: str,
        groups: list[dict[str, Any]],
        captions: list[dict[str, Any]],
        group_by_region: dict[str, str],
        containment_scores: dict[str, float],
    ) -> list[dict[str, Any]]:
        relations: list[dict[str, Any]] = []
        for group in groups:
            group_id = str(group["id"])
            group_captions = [
                caption for caption in captions if group_by_region.get(caption["id"]) == group_id
            ]
            for caption in group_captions:
                caption_score = containment_scores[caption["id"]]
                relations.append(
                    self._build_relation(
                        job_id=job_id,
                        relation_type="caption_of_group",
                        source=caption,
                        target=group,
                        score=caption_score,
                        method="group_containment",
                    )
                )
        return relations

    def _match_assignment(
        self,
        *,
        job_id: str,
        rule: RelationRule,
        sources: list[dict[str, Any]],
        targets: list[dict[str, Any]],
        method: str,
    ) -> list[dict[str, Any]]:
        if not sources or not targets:
            return []
        scores = [[self._score(source, target, rule) for target in targets] for source in sources]
        return [
            self._build_relation(
                job_id=job_id,
                relation_type=rule.relation_type,
                source=sources[source_index],
                target=targets[target_index],
                score=score,
                method=method,
            )
            for source_index, target_index, score in self._maximum_assignment(
                scores,
                min_score=self.config.min_score,
            )
        ]

    def _best_group(
        self,
        region: dict[str, Any],
        groups: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, float]:
        if not groups:
            return None, 0.0
        scored = [
            (group, self._containment_ratio(region["bbox"], group["bbox"])) for group in groups
        ]
        return max(scored, key=lambda item: item[1])

    def _build_relation(
        self,
        *,
        job_id: str,
        relation_type: str,
        source: dict[str, Any],
        target: dict[str, Any],
        score: float,
        method: str,
    ) -> dict[str, Any]:
        return {
            "id": self._relation_id(job_id, relation_type, source["id"], target["id"]),
            "source_region_id": source["id"],
            "target_region_id": target["id"],
            "relation_type": relation_type,
            "score": round(score, 6),
            "method": method,
            "version": self.version,
            "review_status": "unreviewed",
        }

    def _score(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        rule: RelationRule,
    ) -> float:
        source_bbox = source["bbox"]
        target_bbox = target["bbox"]
        source_center = self._center(source_bbox)
        target_center = self._center(target_bbox)
        distance = math.dist(source_center, target_center)
        if distance > self.config.max_distance:
            return 0.0

        distance_score = max(0.0, 1.0 - distance / self.config.max_distance)
        x_alignment = max(
            0.0,
            1.0 - abs(source_center[0] - target_center[0]) / self.config.max_distance,
        )
        if rule.source_kind in {"number", "caption"}:
            vertical_offset = source_center[1] - target_center[1]
            below_score = (
                1.0
                if vertical_offset >= 0
                else max(0.0, 1.0 + vertical_offset / self.config.max_distance)
            )
            layout_score = 0.6 * below_score + 0.4 * x_alignment
        else:
            layout_score = 0.5 * distance_score + 0.5 * x_alignment

        overlap_score = max(
            self._axis_overlap(source_bbox[0], source_bbox[2], target_bbox[0], target_bbox[2]),
            self._axis_overlap(source_bbox[1], source_bbox[3], target_bbox[1], target_bbox[3]),
        )
        confidence_score = (
            self._confidence(source.get("confidence")) + self._confidence(target.get("confidence"))
        ) / 2
        weights = (
            self.config.layout_weight,
            self.config.distance_weight,
            self.config.overlap_weight,
            self.config.confidence_weight,
        )
        weight_total = sum(weights)
        if weight_total <= 0:
            return 0.0
        return (
            layout_score * weights[0]
            + distance_score * weights[1]
            + overlap_score * weights[2]
            + confidence_score * weights[3]
        ) / weight_total

    @staticmethod
    def _maximum_assignment(
        scores: list[list[float]],
        *,
        min_score: float,
    ) -> list[tuple[int, int, float]]:
        if not scores or not scores[0]:
            return []
        row_count = len(scores)
        real_column_count = len(scores[0])
        dummy_column_count = row_count
        invalid_cost = 1_000_000.0
        dummy_cost = 1.0 - min_score + 1e-9
        costs = []
        for row in scores:
            costs.append(
                [1.0 - score if score >= min_score else invalid_cost for score in row]
                + [dummy_cost] * dummy_column_count
            )

        assignments = RelationMatcher._hungarian_minimize(costs)
        matched = []
        for row_index, column_index in enumerate(assignments):
            if column_index < 0 or column_index >= real_column_count:
                continue
            score = scores[row_index][column_index]
            if score >= min_score:
                matched.append((row_index, column_index, score))
        return matched

    @staticmethod
    def _hungarian_minimize(costs: list[list[float]]) -> list[int]:
        row_count = len(costs)
        column_count = len(costs[0])
        if row_count > column_count:
            raise ValueError("Hungarian matcher requires rows <= columns")

        u = [0.0] * (row_count + 1)
        v = [0.0] * (column_count + 1)
        matched_row = [0] * (column_count + 1)
        previous_column = [0] * (column_count + 1)

        for row in range(1, row_count + 1):
            matched_row[0] = row
            column0 = 0
            minimum = [math.inf] * (column_count + 1)
            used = [False] * (column_count + 1)
            while True:
                used[column0] = True
                current_row = matched_row[column0]
                delta = math.inf
                column1 = 0
                for column in range(1, column_count + 1):
                    if used[column]:
                        continue
                    current = costs[current_row - 1][column - 1] - u[current_row] - v[column]
                    if current < minimum[column]:
                        minimum[column] = current
                        previous_column[column] = column0
                    if minimum[column] < delta:
                        delta = minimum[column]
                        column1 = column
                for column in range(column_count + 1):
                    if used[column]:
                        u[matched_row[column]] += delta
                        v[column] -= delta
                    else:
                        minimum[column] -= delta
                column0 = column1
                if matched_row[column0] == 0:
                    break
            while True:
                column1 = previous_column[column0]
                matched_row[column0] = matched_row[column1]
                column0 = column1
                if column0 == 0:
                    break

        assignment = [-1] * row_count
        for column in range(1, column_count + 1):
            if matched_row[column] != 0:
                assignment[matched_row[column] - 1] = column - 1
        return assignment

    @staticmethod
    def _center(bbox: list[float]) -> tuple[float, float]:
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    @staticmethod
    def _axis_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
        intersection = max(0.0, min(end_a, end_b) - max(start_a, start_b))
        smaller = min(end_a - start_a, end_b - start_b)
        return intersection / smaller if smaller > 0 else 0.0

    @staticmethod
    def _containment_ratio(child: list[float], parent: list[float]) -> float:
        intersection_width = max(0.0, min(child[2], parent[2]) - max(child[0], parent[0]))
        intersection_height = max(0.0, min(child[3], parent[3]) - max(child[1], parent[1]))
        child_area = max(0.0, child[2] - child[0]) * max(0.0, child[3] - child[1])
        if child_area <= 0:
            return 0.0
        return intersection_width * intersection_height / child_area

    @staticmethod
    def _confidence(value: Any) -> float:
        return float(value) if isinstance(value, (int, float)) else 0.5

    @staticmethod
    def _relation_id(job_id: str, relation_type: str, source_id: str, target_id: str) -> str:
        digest = hashlib.sha256(
            f"{job_id}:{relation_type}:{source_id}:{target_id}".encode()
        ).hexdigest()[:24]
        return f"rel_{digest}"
