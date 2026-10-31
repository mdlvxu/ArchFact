from __future__ import annotations

import asyncio
import hashlib
import io
import re
import unicodedata
from pathlib import Path
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.core.errors import ConflictError, DomainError
from app.domain.identifiers import (
    canonical_artifact_id,
    clean_value,
    normalize_identifier,
    normalize_report_name,
)
from app.domain.time import utc_now
from app.repositories.mongo_repository import MongoRepository

FIELD_MAPPING = {
    "尺寸": "measurements",
    "外形描述": "morphological_description",
    "颜色": "surface_color",
    "器型1": "artifact_group",
    "器型2": "category",
    "型别": "type",
    "式别": "subtype",
    "材质": "texture",
    "完整程度": "completeness",
    "图注": "figure_caption",
    "地层": "stratigraphy",
    "备注": "notes",
}

REGION_KIND = {
    0: "artifact",
    1: "number",
    2: "caption",
    3: "grave_drawing",
    4: "group",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
EMPTY_MARKERS = {"", "-", "—", "－", "无", "none", "null", "nan"}
_BASELINE_LOCKS: dict[str, asyncio.Lock] = {}


def baseline_lock(dataset_id: str) -> asyncio.Lock:
    return _BASELINE_LOCKS.setdefault(dataset_id, asyncio.Lock())


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]}"


def normalize_reference(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("，", ",").replace("：", ",").replace(":", ",")
    text = text.replace("－", "-").replace("—", "-")
    return re.sub(r"\s+", "", text).strip(".,;；")


def extract_color_plate_keys(value: Any) -> list[str]:
    text = clean_value(value) or ""
    matches = re.findall(
        r"彩版[〇零一二三四五六七八九十百0-9O○\-]+(?:[,，][〇零一二三四五六七八九十百0-9]+)?",
        text,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(normalize_reference(match) for match in matches))


def split_figure_references(value: Any) -> tuple[list[str], list[str]]:
    """Split figure and color-plate citations while preserving the source field."""
    text = unicodedata.normalize("NFKC", clean_value(value) or "")
    pattern = (
        r"(?:彩版|彩图|图版|图|figure|fig\.?|plate)\s*"
        r"[一二三四五六七八九十百〇零0-9A-Za-z\-]+"
        r"(?:\s*[,.:：，]\s*[一二三四五六七八九十百〇零0-9A-Za-z\-]+)*"
    )
    figures: list[str] = []
    plates: list[str] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        reference = match.group(0).strip()
        destination = (
            plates if re.match(r"^(彩版|彩图|plate)", reference, re.IGNORECASE) else figures
        )
        if reference not in destination:
            destination.append(reference)
    return figures, plates


class GoldDatasetService:
    """Imports human annotations into an isolated evaluation namespace.

    These documents are never queried by the extraction pipeline. They are only
    resolved after a human verification session has been submitted.
    """

    def __init__(self, settings: Settings, repository: MongoRepository) -> None:
        self._settings = settings
        self._repository = repository

    @staticmethod
    def preview_workbook(*, filename: str, content: bytes) -> dict[str, Any]:
        """Read workbook headers and a small preview without persisting it."""
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover
            raise DomainError("缺少 openpyxl，无法读取人工标注 Excel") from exc
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise DomainError("无法读取该 Excel 文件，请确认文件格式正确") from exc
        worksheet = workbook[workbook.sheetnames[0]]
        rows = worksheet.iter_rows(values_only=True)
        first = next(rows, None)
        headers = [clean_value(value) or "" for value in (first or ())]
        if not headers or not any(headers):
            workbook.close()
            raise DomainError("Excel 首行未找到字段标题")
        sample_rows = [
            [clean_value(value) for value in row] for _, row in zip(range(8), rows, strict=False)
        ]
        workbook.close()
        return {
            "filename": filename,
            "sheet_name": worksheet.title,
            "headers": headers,
            "sample_rows": sample_rows,
        }

    async def import_workbook(
        self,
        *,
        document_id: str,
        name: str,
        version: str,
        filename: str,
        content: bytes,
        key_mode: str,
        field_mapping: dict[str, str],
    ) -> dict[str, Any]:
        """Import a report-specific spreadsheet as a reusable evaluation baseline."""
        if key_mode not in {"artifact_id", "context_sequence"}:
            raise DomainError("器物主键方式无效")
        document = await self._repository.get_document(document_id)
        report_key = normalize_report_name(document.get("filename", ""))
        existing = next(
            (
                dataset
                for dataset in await self._repository.list_gold_datasets()
                if dataset.get("status") == "ready"
                and normalize_report_name(dataset.get("document_filename", "")) == report_key
            ),
            None,
        )
        if existing:
            raise ConflictError("该考古报告的人工标注基准已导入，无需重复导入")
        preview = self.preview_workbook(filename=filename, content=content)
        headers = set(preview["headers"])
        if key_mode == "artifact_id" and not field_mapping.get("artifact_id"):
            raise DomainError("请选择器物编号列作为主键")
        if key_mode == "context_sequence" and not all(
            field_mapping.get(key) for key in ("context_id", "sequence_no")
        ):
            raise DomainError("请选择遗迹号列和序号列作为联合主键")
        missing_columns = [
            column for column in field_mapping.values() if column and column not in headers
        ]
        if missing_columns:
            raise DomainError(f"映射的 Excel 列不存在：{', '.join(missing_columns)}")

        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        worksheet = workbook[workbook.sheetnames[0]]
        iterator = worksheet.iter_rows(values_only=True)
        columns = [clean_value(value) or "" for value in next(iterator, ())]
        dataset_id = stable_id("gold", document_id, version, hashlib.sha256(content).hexdigest())
        records: list[dict[str, Any]] = []
        unkeyed_rows: list[int] = []
        duplicate_keys: dict[str, int] = {}
        source_rows: list[tuple[int, dict[str, Any], str, dict[str, Any]]] = []
        for source_row, values in enumerate(iterator, start=2):
            raw = dict(zip(columns, values, strict=False))
            if not any(clean_value(value) for value in values):
                continue
            if key_mode == "artifact_id":
                artifact_id = normalize_identifier(
                    clean_value(raw.get(field_mapping["artifact_id"])) or ""
                )
            else:
                artifact_id = canonical_artifact_id(
                    raw.get(field_mapping["context_id"]),
                    raw.get(field_mapping["sequence_no"]),
                )
            if not artifact_id:
                unkeyed_rows.append(source_row)
                continue
            fields = {
                key: clean_value(raw.get(column)) if column else None
                for key, column in field_mapping.items()
                if key not in {"context_id", "sequence_no"}
            }
            fields["artifact_id"] = artifact_id
            source_rows.append(
                (
                    source_row,
                    fields,
                    artifact_id,
                    {key: clean_value(value) for key, value in raw.items() if key},
                )
            )
            duplicate_keys[artifact_id] = duplicate_keys.get(artifact_id, 0) + 1
        workbook.close()
        for source_row, fields, artifact_id, raw in source_rows:
            figure_references, color_plate_references = split_figure_references(
                fields.get("figure_caption")
            )
            records.append(
                {
                    "_id": stable_id("goldrec", dataset_id, source_row),
                    "dataset_id": dataset_id,
                    "source_row": source_row,
                    "canonical_artifact_id": artifact_id,
                    "fields": fields,
                    "raw": raw,
                    "figure_references": figure_references,
                    "color_plate_references": color_plate_references,
                    "duplicate_key_count": duplicate_keys[artifact_id],
                }
            )
        warnings = ["人工标注表由用户绑定到当前报告，系统未通过配套 PDF 指纹独立验证。"]
        if unkeyed_rows:
            warnings.append(f"{len(unkeyed_rows)} 行缺少有效主键，未纳入对照")
        duplicated = sum(count > 1 for count in duplicate_keys.values())
        if duplicated:
            warnings.append(f"{duplicated} 个主键存在重复行，评测时将标记为歧义而不强行配对")
        if not records:
            raise DomainError("未找到包含有效器物主键的数据行")
        now = utc_now()
        dataset = {
            "_id": dataset_id,
            "name": name.strip() or filename,
            "document_id": document_id,
            "document_filename": document.get("filename", ""),
            "report_key": report_key,
            "version": version,
            "status": "ready",
            "source_type": "human_annotation",
            "workbook_name": filename,
            "key_mode": key_mode,
            "field_mapping": field_mapping,
            "source_headers": columns,
            "record_count": len(records),
            "source_row_count": len(records) + len(unkeyed_rows),
            "unkeyed_row_count": len(unkeyed_rows),
            "region_count": 0,
            "asset_count": 0,
            "link_count": 0,
            "matched_artifact_assets": 0,
            "matched_color_plate_assets": 0,
            "capabilities": {"has_detection_labels": False, "has_relation_labels": False},
            "source_document_verified": False,
            "warnings": warnings,
            "created_at": now,
            "updated_at": now,
        }
        try:
            async with baseline_lock(dataset_id):
                await self._repository.replace_gold_dataset(
                    dataset=dataset,
                    records=records,
                    regions=[],
                    assets=[],
                    links=[],
                )
        except DuplicateKeyError as exc:
            raise ConflictError("该考古报告的人工标注基准已导入，无需重复导入") from exc
        return dataset

    async def delete_unreferenced_dataset(self, dataset_id: str) -> None:
        async with baseline_lock(dataset_id):
            await self._repository.get_gold_dataset(dataset_id)
            if await self._repository.has_gold_dataset_references(dataset_id):
                raise ConflictError("该基准已有关联评测，为保留历史结果不能删除")
            await self._repository.delete_gold_dataset(dataset_id)

    async def import_wenjiashan(
        self,
        *,
        document_id: str,
        version: str,
        replace: bool,
    ) -> dict[str, Any]:
        document = await self._repository.get_document(document_id)
        root = self._resolve_root()
        source_document_verified = self._verify_source_document(root, document)
        workbook_path = self._find_workbook(root)
        labels_root = root / "labels"
        assets_root = root / "cutpictures"
        if not labels_root.is_dir() or not assets_root.is_dir():
            raise DomainError("人工标注数据目录缺少 labels 或 cutpictures 子目录")

        existing = await self._repository.get_gold_dataset_for_document(
            document_id=document_id,
            version=version,
        )
        if existing and not replace:
            raise ConflictError("该 PDF 的同版本人工标注数据已导入；如需重建请使用 replace=true")

        dataset_id = existing["_id"] if existing else stable_id("gold", document_id, version)
        records = self._read_records(workbook_path, dataset_id)
        assets = self._read_assets(assets_root, root, dataset_id, records)
        regions = self._read_regions(labels_root, dataset_id)
        links = self._build_asset_links(dataset_id, records, assets)

        artifact_matches = len(
            {link["record_id"] for link in links if link["link_type"] == "artifact_crop"}
        )
        plate_matches = len(
            {link["record_id"] for link in links if link["link_type"] == "color_plate"}
        )
        warnings: list[str] = []
        if source_document_verified is None:
            warnings.append("未找到配套源 PDF，人工标注数据绑定未执行文件指纹确认")
        artifact_expected = sum(bool(record["canonical_artifact_id"]) for record in records)
        if artifact_matches < artifact_expected:
            warnings.append(f"{artifact_expected - artifact_matches} 条记录未找到同名器物裁剪图")
        records_with_plate = sum(bool(record.get("color_plate_keys")) for record in records)
        if plate_matches < records_with_plate:
            warnings.append(f"{records_with_plate - plate_matches} 条彩版引用未找到直接文件匹配")

        now = utc_now()
        dataset = {
            "_id": dataset_id,
            "name": "文家山人工标注数据",
            "document_id": document_id,
            "document_filename": document.get("filename", ""),
            "version": version,
            "status": "ready",
            "source_type": "human_annotation",
            "source_root_key": "wenjiashan_gold_dataset",
            "source_document_verified": source_document_verified is True,
            "workbook_name": workbook_path.name,
            "record_count": len(records),
            "region_count": len(regions),
            "asset_count": len(assets),
            "link_count": len(links),
            "matched_artifact_assets": artifact_matches,
            "matched_color_plate_assets": plate_matches,
            "warnings": warnings,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        async with baseline_lock(dataset_id):
            await self._repository.replace_gold_dataset(
                dataset=dataset,
                records=records,
                regions=regions,
                assets=assets,
                links=links,
            )
        return dataset

    def _resolve_root(self) -> Path:
        configured = self._settings.gold_dataset_root
        if configured is None:
            raise DomainError("尚未配置 GOLD_DATASET_ROOT")
        configured = configured.expanduser()
        project_root = Path(__file__).resolve().parents[2]
        root = (
            configured.resolve()
            if configured.is_absolute()
            else (project_root / configured).resolve()
        )
        if not root.is_dir():
            raise DomainError(f"人工标注数据目录不存在：{root}")
        return root

    @staticmethod
    def _find_workbook(root: Path) -> Path:
        candidates = sorted(path for path in root.glob("*.xlsx") if not path.name.startswith("~$"))
        if not candidates:
            raise DomainError("人工标注数据目录中没有找到 xlsx 文件")
        return candidates[0]

    @staticmethod
    def _verify_source_document(root: Path, document: dict[str, Any]) -> bool | None:
        candidates = [
            path for path in root.parent.glob("*.pdf") if "文家山" in path.name and path.is_file()
        ]
        if not candidates:
            return None
        source_pdf = sorted(candidates, key=lambda path: path.name)[0]
        digest = hashlib.sha256()
        with source_pdf.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != document.get("sha256"):
            raise ConflictError("上传的 PDF 与文家山人工标注数据配套源文件指纹不一致，已拒绝绑定")
        return True

    @staticmethod
    def _read_records(workbook_path: Path, dataset_id: str) -> list[dict[str, Any]]:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise DomainError("缺少 openpyxl，无法导入人工标注 Excel") from exc

        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        worksheet = workbook[workbook.sheetnames[0]]
        rows = worksheet.iter_rows(values_only=True)
        headers = [clean_value(value) or "" for value in next(rows)]
        records: list[dict[str, Any]] = []
        for source_row, values in enumerate(rows, start=2):
            raw = dict(zip(headers, values, strict=False))
            if not any(clean_value(value) for value in values):
                continue
            context = clean_value(raw.get("遗迹号"))
            sequence = clean_value(raw.get("序号"))
            artifact_id = canonical_artifact_id(context, sequence)
            fields = {
                target: clean_value(raw.get(source)) for source, target in FIELD_MAPPING.items()
            }
            fields["artifact_id"] = artifact_id or None
            records.append(
                {
                    "_id": stable_id("goldrec", dataset_id, source_row),
                    "dataset_id": dataset_id,
                    "source_row": source_row,
                    "context_id": context,
                    "sequence_no": sequence,
                    "canonical_artifact_id": artifact_id,
                    "fields": fields,
                    "color_plate_keys": extract_color_plate_keys(raw.get("图注")),
                    "raw": {key: clean_value(value) for key, value in raw.items()},
                }
            )
        workbook.close()
        return records

    @staticmethod
    def _read_assets(
        assets_root: Path,
        root: Path,
        dataset_id: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        record_ids = {record["canonical_artifact_id"] for record in records}
        assets: list[dict[str, Any]] = []
        for path in sorted(assets_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            normalized_stem = normalize_identifier(path.stem)
            reference_keys = [normalize_reference(path.stem)]
            if path.stem.startswith("彩版"):
                asset_type = "color_plate"
            elif normalized_stem in record_ids:
                asset_type = "artifact_crop"
                reference_keys.append(normalized_stem)
            else:
                asset_type = "reference_image"
            relative_path = path.relative_to(root).as_posix()
            assets.append(
                {
                    "_id": stable_id("goldasset", dataset_id, relative_path),
                    "dataset_id": dataset_id,
                    "asset_type": asset_type,
                    "object_key": relative_path,
                    "filename": path.name,
                    "normalized_stem": normalized_stem,
                    "reference_keys": list(dict.fromkeys(reference_keys)),
                    "size": path.stat().st_size,
                }
            )
        return assets

    @staticmethod
    def _read_regions(labels_root: Path, dataset_id: str) -> list[dict[str, Any]]:
        regions: list[dict[str, Any]] = []
        for path in sorted(labels_root.glob("*.txt")):
            page_match = re.search(r"(\d+)$", path.stem)
            if not page_match:
                continue
            page = int(page_match.group(1))
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            for position, line in enumerate(lines, start=1):
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                try:
                    class_id = int(parts[0])
                    x_center, y_center, width, height = map(float, parts[1:5])
                except ValueError:
                    continue
                kind = REGION_KIND.get(class_id, "other")
                bbox = [
                    max(0.0, x_center - width / 2),
                    max(0.0, y_center - height / 2),
                    min(1.0, x_center + width / 2),
                    min(1.0, y_center + height / 2),
                ]
                regions.append(
                    {
                        "_id": stable_id("goldregion", dataset_id, page, position),
                        "dataset_id": dataset_id,
                        "page": page,
                        "kind": kind,
                        "class_id": class_id,
                        "bbox": bbox,
                        "source_file": path.name,
                        "source_line": position,
                    }
                )
        return regions

    @staticmethod
    def _build_asset_links(
        dataset_id: str,
        records: list[dict[str, Any]],
        assets: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        artifact_assets = {
            asset["normalized_stem"]: asset
            for asset in assets
            if asset["asset_type"] == "artifact_crop"
        }
        plate_assets: dict[str, list[dict[str, Any]]] = {}
        for asset in assets:
            if asset["asset_type"] != "color_plate":
                continue
            for key in asset["reference_keys"]:
                plate_assets.setdefault(key, []).append(asset)

        links: list[dict[str, Any]] = []
        for record in records:
            artifact_asset = artifact_assets.get(record["canonical_artifact_id"])
            if artifact_asset:
                links.append(
                    {
                        "_id": stable_id("goldlink", record["_id"], artifact_asset["_id"]),
                        "dataset_id": dataset_id,
                        "record_id": record["_id"],
                        "asset_id": artifact_asset["_id"],
                        "link_type": "artifact_crop",
                        "status": "confirmed",
                        "method": "normalized_filename_exact",
                        "confidence": 1.0,
                    }
                )
            for plate_key in record.get("color_plate_keys", []):
                for plate_asset in plate_assets.get(plate_key, []):
                    links.append(
                        {
                            "_id": stable_id("goldlink", record["_id"], plate_asset["_id"]),
                            "dataset_id": dataset_id,
                            "record_id": record["_id"],
                            "asset_id": plate_asset["_id"],
                            "link_type": "color_plate",
                            "status": "confirmed",
                            "method": "figure_reference_exact",
                            "confidence": 1.0,
                        }
                    )
        return links
