import asyncio
import hashlib
from pathlib import Path
from typing import BinaryIO

from bson import ObjectId
from fastapi import UploadFile
from gridfs import GridFSBucket

from app.core.config import Settings
from app.core.errors import DomainError
from app.infrastructure.mongodb import MongoDatabase


class GridFsStorage:
    def __init__(self, database: MongoDatabase, settings: Settings) -> None:
        self._settings = settings
        self._bucket = GridFSBucket(
            database.sync_database,
            bucket_name=settings.gridfs_bucket,
        )

    async def upload_pdf(self, file: UploadFile) -> tuple[str, str, int]:
        return await asyncio.to_thread(
            self._upload_pdf_sync,
            file.file,
            file.filename or "document.pdf",
            file.content_type or "application/pdf",
        )

    def _upload_pdf_sync(
        self,
        source: BinaryIO,
        filename: str,
        content_type: str,
    ) -> tuple[str, str, int]:
        digest = hashlib.sha256()
        size = 0
        source.seek(0)
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > self._settings.max_upload_bytes:
                raise DomainError(
                    f"PDF 不能超过 {self._settings.max_upload_bytes // (1024 * 1024)} MB",
                    code=4130,
                    status_code=413,
                )
            digest.update(chunk)

        source.seek(0)
        header = source.read(5)
        if header != b"%PDF-":
            raise DomainError("上传内容不是有效的 PDF 文件", code=4150, status_code=415)

        source.seek(0)
        file_id = self._bucket.upload_from_stream(
            filename,
            source,
            metadata={"content_type": content_type, "sha256": digest.hexdigest()},
        )
        return str(file_id), digest.hexdigest(), size

    async def download_to_path(self, file_id: str, target: Path) -> None:
        await asyncio.to_thread(self._download_to_path_sync, file_id, target)

    def _download_to_path_sync(self, file_id: str, target: Path) -> None:
        with target.open("wb") as output:
            self._bucket.download_to_stream(ObjectId(file_id), output)

    async def delete(self, file_id: str) -> None:
        await asyncio.to_thread(self._bucket.delete, ObjectId(file_id))
