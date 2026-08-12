import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class EntityLinkOutput:
    records: list[dict[str, Any]]
    entities: list[dict[str, Any]]


class ArtifactEntityLinker:
    """Aggregate page-level records and visual regions into document entities."""

    provider = "archfact"
    model = "document-artifact-entity-linker"
    version = "4"
    _visual_kinds = {
        "artifact",
        "line_drawing",
        "color_plate",
        "grave_drawing",
    }
    _combined_reference = re.compile(r"^(.+?)[\s:\uFF1A\-\u2013\u2014]+([A-Za-z0-9]+)$")
    _artifact_identifier = re.compile(
        r"^[A-Z]{1,6}\d+[A-Z]?(?::[A-Z]?\d+[A-Z]?)+$"
    )

    def link(
        self,
        *,
        job_id: str,
        document_id: str,
        records: list[dict[str, Any]],
        regions: list[dict[str, Any]],
    ) -> EntityLinkOutput:
        region_by_id = {str(region["id"]): region for region in regions}
        self._assign_record_ids(job_id, records)
        token_sets = [self._record_tokens(record) for record in records]
        artifact_token_sets = [
            {token for token in tokens if token.startswith("artifact:")}
            for tokens in token_sets
        ]
        parents = list(range(len(records)))
        root_artifact_tokens = [set(tokens) for tokens in artifact_token_sets]

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> bool:
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return True

            # Figure and plate references are supporting evidence, not an identity.
            # A shared figure such as "图3-4" can contain many different artifacts.
            # Never let that weak reference merge two explicitly different IDs.
            left_ids = root_artifact_tokens[left_root]
            right_ids = root_artifact_tokens[right_root]
            if left_ids and right_ids and left_ids.isdisjoint(right_ids):
                return False

            parents[right_root] = left_root
            root_artifact_tokens[left_root] = left_ids | right_ids
            root_artifact_tokens[right_root] = root_artifact_tokens[left_root]
            return True

        # Establish exact artifact identity first. Visual references are evaluated
        # afterwards so an ID-less plate record may attach to an artifact, while a
        # plate/figure shared by M3:4 and M3:5 cannot collapse them into one entity.
        for artifact_phase in (True, False):
            owner_by_token: dict[str, int] = {}
            for record_index, tokens in enumerate(token_sets):
                for token in tokens:
                    if token.startswith("artifact:") is not artifact_phase:
                        continue
                    owner = owner_by_token.setdefault(token, record_index)
                    union(record_index, owner)

        grouped_indexes: dict[int, list[int]] = {}
        for record_index in range(len(records)):
            grouped_indexes.setdefault(find(record_index), []).append(record_index)

        entities: list[dict[str, Any]] = []
        for indexes in grouped_indexes.values():
            group_records = [records[index] for index in indexes]
            match_keys = sorted({token for index in indexes for token in token_sets[index]})
            entity_id = self._entity_id(job_id, match_keys, group_records)
            region_ids = sorted(
                {
                    str(region_id)
                    for record in group_records
                    for region_id in record.get("region_ids", [])
                    if str(region_id) in region_by_id
                }
            )
            relation_ids = sorted(
                {
                    str(relation_id)
                    for record in group_records
                    for relation_id in record.get("relation_ids", [])
                }
            )
            source_pages = sorted(
                {int(page) for record in group_records for page in record.get("source_pages", [])}
            )
            associated_pages = sorted(
                set(source_pages)
                | {int(region_by_id[region_id]["page"]) for region_id in region_ids}
            )
            visual_region_ids = [
                region_id
                for region_id in region_ids
                if region_by_id[region_id].get("kind") in self._visual_kinds
            ]
            confidence, link_status = self._link_quality(match_keys, visual_region_ids)
            canonical_artifact_id = self._canonical_artifact_id(group_records)
            entity_thumbnail = self._thumbnail_region_id(
                visual_region_ids,
                region_by_id,
                preferred_pages=source_pages,
            )
            entity = {
                "id": entity_id,
                "job_id": job_id,
                "document_id": document_id,
                "canonical_artifact_id": canonical_artifact_id,
                "aliases": self._values(group_records, "artifact_ids"),
                "figure_refs": self._values(group_records, "figure_refs"),
                "plate_refs": self._values(group_records, "plate_refs"),
                "match_keys": match_keys,
                "record_ids": sorted(str(record["id"]) for record in group_records),
                "region_ids": region_ids,
                "relation_ids": relation_ids,
                "source_pages": source_pages,
                "associated_pages": associated_pages,
                "thumbnail_region_id": entity_thumbnail,
                "confidence": confidence,
                "link_status": link_status,
                "link_reasons": sorted({self._token_reason(token) for token in match_keys}),
                "version": self.version,
            }
            entities.append(entity)
            for record in group_records:
                # Page-level records keep provenance-only region/relation IDs.
                # The entity document owns the cross-page union for navigation.
                record["entity_id"] = entity_id
                record["entity_confidence"] = confidence
                record["entity_match_status"] = link_status
                record["associated_pages"] = self._record_associated_pages(
                    record,
                    region_by_id,
                )
                self._assign_displayable_thumbnail(
                    record=record,
                    region_by_id=region_by_id,
                    entity_thumbnail=entity_thumbnail,
                )

        return EntityLinkOutput(records=records, entities=entities)

    @classmethod
    def _record_associated_pages(
        cls,
        record: dict[str, Any],
        region_by_id: dict[str, dict[str, Any]],
    ) -> list[int]:
        pages = {
            int(page)
            for page in record.get("source_pages", [])
            if isinstance(page, int) or str(page).isdigit()
        }
        for region_id in record.get("region_ids", []):
            region = region_by_id.get(str(region_id))
            if region is None:
                continue
            page = region.get("page")
            if isinstance(page, int):
                pages.add(page)
        return sorted(pages)

    @classmethod
    def _assign_displayable_thumbnail(
        cls,
        *,
        record: dict[str, Any],
        region_by_id: dict[str, dict[str, Any]],
        entity_thumbnail: str | None,
    ) -> None:
        current = region_by_id.get(str(record.get("thumbnail_region_id") or ""))
        if cls._region_has_displayable_crop(current):
            return
        record_pages = {
            int(page) for page in record.get("source_pages") or []
        }
        thumb_region = region_by_id.get(str(entity_thumbnail or ""))
        if cls._region_has_displayable_crop(thumb_region) and (
            not record_pages or int(thumb_region.get("page", -1)) in record_pages
        ):
            record["thumbnail_region_id"] = entity_thumbnail
            return
        # Prefer a displayable crop already owned by this page-level record.
        owned = cls._thumbnail_region_id(
            [str(region_id) for region_id in record.get("region_ids", [])],
            region_by_id,
            preferred_pages=sorted(record_pages),
        )
        record["thumbnail_region_id"] = owned

    @staticmethod
    def _region_has_displayable_crop(region: dict[str, Any] | None) -> bool:
        if region is None:
            return False
        if region.get("approximate") and not region.get("crop_object_key"):
            return False
        # Real crop files are always displayable. Non-approximate visuals without a
        # key remain allowed for unit fixtures / pre-crop pipeline states.
        return True if region.get("crop_object_key") else not bool(region.get("approximate"))

    @classmethod
    def _record_tokens(cls, record: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        linkage = record.get("linkage", {})
        identity = linkage.get("identity", {}) if isinstance(linkage, dict) else {}
        visual = linkage.get("visual_link", {}) if isinstance(linkage, dict) else {}
        hints = record.get("link_hints", {})

        artifact_values = [
            identity.get("artifact_id_normalized") if isinstance(identity, dict) else None,
            identity.get("artifact_id_raw") if isinstance(identity, dict) else None,
            *(hints.get("artifact_ids", []) if isinstance(hints, dict) else []),
        ]
        for value in artifact_values:
            normalized = cls._normalize_artifact_identifier(value)
            if normalized:
                tokens.add(f"artifact:{normalized}")

        if isinstance(visual, dict):
            figure_no = cls._normalize(visual.get("figure_no"))
            figure_item_no = cls._normalize(visual.get("figure_item_no"))
            if figure_no and figure_item_no:
                tokens.add(f"figure:{figure_no}:{figure_item_no}")
            plate_no = cls._normalize(visual.get("plate_no"))
            plate_item_no = cls._normalize(visual.get("plate_item_no"))
            if plate_no and plate_item_no:
                tokens.add(f"plate:{plate_no}:{plate_item_no}")

        if isinstance(hints, dict):
            for hint_key, prefix in (("figure_refs", "figure"), ("plate_refs", "plate")):
                for value in hints.get(hint_key, []):
                    combined = cls._combined_reference.match(str(value).strip())
                    if combined is None:
                        continue
                    reference = cls._normalize(combined.group(1))
                    item_no = cls._normalize(combined.group(2))
                    if reference and item_no:
                        tokens.add(f"{prefix}:{reference}:{item_no}")
        return tokens

    @staticmethod
    def _assign_record_ids(job_id: str, records: list[dict[str, Any]]) -> None:
        for index, record in enumerate(records):
            if record.get("id"):
                continue
            marker = (
                f"{job_id}:{index}:{record.get('record_type')}:"
                f"{record.get('source_pages')}:{record.get('link_hints')}"
            )
            digest = hashlib.sha256(marker.encode()).hexdigest()[:24]
            record["id"] = f"rec_{digest}"

    @staticmethod
    def _entity_id(
        job_id: str,
        match_keys: list[str],
        records: list[dict[str, Any]],
    ) -> str:
        marker = "|".join(match_keys) or "|".join(sorted(str(record["id"]) for record in records))
        digest = hashlib.sha256(f"{job_id}:{marker}".encode()).hexdigest()[:24]
        return f"ent_{digest}"

    @staticmethod
    def _link_quality(
        match_keys: list[str],
        visual_region_ids: list[str],
    ) -> tuple[float, str]:
        if any(key.startswith("artifact:") for key in match_keys):
            confidence = 0.98
        elif any(key.startswith(("figure:", "plate:")) for key in match_keys):
            confidence = 0.92
        else:
            confidence = 0.45
        if visual_region_ids and confidence >= 0.85:
            return confidence, "linked"
        if visual_region_ids or match_keys:
            return confidence, "needs_review"
        return confidence, "unlinked"

    @classmethod
    def _canonical_artifact_id(cls, records: list[dict[str, Any]]) -> str | None:
        for record in records:
            linkage = record.get("linkage", {})
            identity = linkage.get("identity", {}) if isinstance(linkage, dict) else {}
            if isinstance(identity, dict):
                value = identity.get("artifact_id_normalized") or identity.get("artifact_id_raw")
                if value and str(value).strip():
                    return str(value).strip()
            field = record.get("fields", {}).get("artifact_id", {})
            value = field.get("value") if isinstance(field, dict) else None
            if value and str(value).strip():
                return str(value).strip()
        return None

    @staticmethod
    def _values(records: list[dict[str, Any]], key: str) -> list[str]:
        return sorted(
            {
                str(value).strip()
                for record in records
                for value in (
                    record.get("link_hints", {}).get(key, [])
                    if isinstance(record.get("link_hints"), dict)
                    else []
                )
                if str(value).strip()
            }
        )

    @classmethod
    def _thumbnail_region_id(
        cls,
        region_ids: list[str],
        region_by_id: dict[str, dict[str, Any]],
        *,
        preferred_pages: list[int] | None = None,
    ) -> str | None:
        """Pick a displayable crop; never use approximate regions without files."""

        priority = {"artifact": 0, "line_drawing": 1, "color_plate": 2, "grave_drawing": 3}
        preferred = {int(page) for page in (preferred_pages or [])}
        candidates: list[tuple[int, int, int, str]] = []
        for region_id in region_ids:
            region = region_by_id.get(region_id)
            if region is None:
                continue
            if region.get("kind") not in cls._visual_kinds:
                continue
            if region.get("approximate") and not region.get("crop_object_key"):
                continue
            page = region.get("page")
            crop_rank = 0 if region.get("crop_object_key") else 1
            page_rank = 0 if isinstance(page, int) and page in preferred else 1
            kind_rank = priority.get(str(region.get("kind")), 99)
            candidates.append((crop_rank, page_rank, kind_rank, region_id))
        if not candidates:
            return None
        return min(candidates)[3]

    @staticmethod
    def _token_reason(token: str) -> str:
        prefix = token.split(":", 1)[0]
        return {
            "artifact": "器物编号一致",
            "figure": "图号与子图序号一致",
            "plate": "彩版号与子图序号一致",
        }.get(prefix, "结构化引用一致")

    @staticmethod
    def _normalize(value: Any) -> str:
        if value is None:
            return ""
        normalized = unicodedata.normalize("NFKC", str(value)).casefold()
        return "".join(character for character in normalized if character.isalnum())

    # Tomb/unit labels such as 仲M4:3 appear on color plates; strip for linking.
    _tomb_unit_prefix_pattern = re.compile(r"^[\u4e00-\u9fff]{1,2}(?=[A-Z])")

    @classmethod
    def _normalize_artifact_identifier(cls, value: Any) -> str:
        """Normalize an artifact ID without erasing its numeric boundaries."""

        if value is None:
            return ""
        normalized = re.sub(
            r"\s+",
            "",
            unicodedata.normalize("NFKC", str(value)).upper(),
        )
        normalized = cls._tomb_unit_prefix_pattern.sub("", normalized)
        if not cls._artifact_identifier.fullmatch(normalized):
            return ""
        return normalized.casefold()
