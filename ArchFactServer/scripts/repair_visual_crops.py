"""Restore missing crops and region OCR from a prior run of the same PDF.

The default is a read-only dry run. Pass --apply only after verifying that the
two jobs belong to the same document and extraction output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import Settings
from app.infrastructure.local_image_storage import LocalImageStorage

_VISUAL_KINDS = {"artifact", "line_drawing", "color_plate", "grave_drawing"}
_OCR_KINDS = {"number", "caption"}
_CROP_KINDS = _VISUAL_KINDS | _OCR_KINDS


def _region_signature(region: dict[str, Any]) -> tuple[Any, ...]:
    bbox = region.get("bbox") or []
    return (
        region.get("page"),
        region.get("kind"),
        tuple(round(float(value), 7) for value in bbox),
    )


def _object_key(region: dict[str, Any], document_id: str) -> str:
    return (
        f"documents/{document_id}/pages/{int(region['page']):04d}/crops/"
        f"{region['kind']}/{region['_id']}.png"
    )


async def repair(current_job_id: str, reference_job_id: str, *, apply: bool) -> None:
    settings = Settings()
    client = AsyncMongoClient(settings.mongodb_uri)
    try:
        database = client[settings.mongodb_database]
        current_job = await database.extraction_jobs.find_one({"_id": current_job_id})
        reference_job = await database.extraction_jobs.find_one({"_id": reference_job_id})
        if current_job is None or reference_job is None:
            raise SystemExit("One or both extraction jobs do not exist.")
        document_id = str(current_job.get("document_id") or "")
        if not document_id or document_id != str(reference_job.get("document_id") or ""):
            raise SystemExit("The jobs do not reference the same uploaded PDF; no changes made.")
        if current_job.get("config", {}).get("template_id") != reference_job.get(
            "config", {}
        ).get("template_id"):
            raise SystemExit("The jobs use different extraction templates; no changes made.")

        current_regions = await database.source_regions.find(
            {"job_id": current_job_id, "kind": {"$in": sorted(_CROP_KINDS)}}
        ).to_list(length=100000)
        reference_regions = await database.source_regions.find(
            {"job_id": reference_job_id, "kind": {"$in": sorted(_CROP_KINDS)}}
        ).to_list(length=100000)
        reference_by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}
        duplicate_reference_signatures: set[tuple[Any, ...]] = set()
        for region in reference_regions:
            if not region.get("crop_object_key"):
                continue
            signature = _region_signature(region)
            if signature in reference_by_signature:
                duplicate_reference_signatures.add(signature)
            else:
                reference_by_signature[signature] = region

        storage = LocalImageStorage(settings)
        repairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        ocr_repairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        current_by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}
        for region in current_regions:
            signature = _region_signature(region)
            if signature in current_by_signature:
                raise SystemExit("Current regions contain duplicate signatures; no changes made.")
            current_by_signature[signature] = region
            if signature in duplicate_reference_signatures:
                continue
            reference = reference_by_signature.get(signature)
            if reference is None:
                continue
            source_path = storage.resolve(reference["crop_object_key"])
            if not region.get("crop_object_key") and source_path.is_file():
                repairs.append((region, reference, _object_key(region, document_id)))
            if (
                region.get("kind") in _OCR_KINDS
                and not region.get("ocr_raw_text")
                and reference.get("ocr_raw_text")
            ):
                ocr_repairs.append((region, reference))

        current_entities = await database.artifact_entities.find(
            {"job_id": current_job_id}
        ).to_list(length=10000)
        reference_entities = await database.artifact_entities.find(
            {"job_id": reference_job_id}
        ).to_list(length=10000)
        current_ids = Counter(
            entity.get("canonical_artifact_id")
            for entity in current_entities
            if entity.get("canonical_artifact_id")
        )
        reference_ids = Counter(
            entity.get("canonical_artifact_id")
            for entity in reference_entities
            if entity.get("canonical_artifact_id")
        )
        reference_entity_by_id = {
            entity["canonical_artifact_id"]: entity
            for entity in reference_entities
            if entity.get("canonical_artifact_id")
            and reference_ids[entity["canonical_artifact_id"]] == 1
            and entity.get("thumbnail_region_id")
            and current_ids[entity["canonical_artifact_id"]] == 1
        }
        reference_region_by_id = {str(region["_id"]): region for region in reference_regions}
        repair_region_ids = {str(region["_id"]) for region, _, _ in repairs}
        thumbnail_updates: list[tuple[dict[str, Any], str]] = []
        for entity in current_entities:
            artifact_id = entity.get("canonical_artifact_id")
            reference_entity = reference_entity_by_id.get(artifact_id)
            if reference_entity is None:
                continue
            reference_region = reference_region_by_id.get(
                str(reference_entity["thumbnail_region_id"])
            )
            if reference_region is None:
                continue
            current_region = current_by_signature.get(_region_signature(reference_region))
            if current_region is None:
                continue
            current_region_id = str(current_region["_id"])
            current_thumbnail_id = str(entity.get("thumbnail_region_id") or "")
            current_thumbnail = next(
                (
                    region
                    for region in current_regions
                    if str(region["_id"]) == current_thumbnail_id
                ),
                None,
            )
            has_usable_current_thumbnail = bool(
                current_thumbnail
                and (
                    current_thumbnail.get("crop_object_key")
                    or str(current_thumbnail["_id"]) in repair_region_ids
                )
            )
            if not has_usable_current_thumbnail:
                thumbnail_updates.append((entity, current_region_id))

        print(json.dumps({
            "mode": "apply" if apply else "dry-run",
            "current_job_id": current_job_id,
            "reference_job_id": reference_job_id,
            "same_pdf": True,
            "missing_crops_recoverable": len(repairs),
            "missing_region_ocr_recoverable": len(ocr_repairs),
            "unique_id_entity_thumbnails_recoverable": len(thumbnail_updates),
        }, ensure_ascii=False))
        if not apply:
            return

        for region, reference, object_key in repairs:
            source_path = storage.resolve(reference["crop_object_key"])
            await storage.write(object_key, source_path.read_bytes())
            await database.source_regions.update_one(
                {"_id": region["_id"], "job_id": current_job_id},
                {
                    "$set": {
                        "crop_object_key": object_key,
                        "crop_width": reference.get("crop_width"),
                        "crop_height": reference.get("crop_height"),
                        "crop_content_type": reference.get("crop_content_type") or "image/png",
                    },
                    "$unset": {"crop_error": ""},
                },
            )

        for region, reference in ocr_repairs:
            await database.source_regions.update_one(
                {"_id": region["_id"], "job_id": current_job_id},
                {"$set": {
                    "text": reference.get("text") or reference["ocr_raw_text"],
                    "ocr_raw_text": reference["ocr_raw_text"],
                    "ocr_confidence": reference.get("ocr_confidence"),
                    "ocr_source": reference.get("ocr_source"),
                }},
            )

        for entity, region_id in thumbnail_updates:
            await database.artifact_entities.update_one(
                {"_id": entity["_id"], "job_id": current_job_id},
                {"$set": {"thumbnail_region_id": region_id}},
            )
        print(json.dumps({
            "restored_crops": len(repairs),
            "restored_region_ocr": len(ocr_repairs),
            "restored_entity_thumbnails": len(thumbnail_updates),
        }))
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-job-id", required=True)
    parser.add_argument("--reference-job-id", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Write verified crop and thumbnail backfills."
    )
    args = parser.parse_args()
    asyncio.run(repair(args.current_job_id, args.reference_job_id, apply=args.apply))


if __name__ == "__main__":
    main()
