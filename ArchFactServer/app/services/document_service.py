from pathlib import Path
from typing import Any

from fastapi import UploadFile

from pymongo.errors import DuplicateKeyError

from app.core.errors import DomainError
from app.infrastructure.gridfs_storage import GridFsStorage
from app.repositories.mongo_repository import MongoRepository


class DocumentService:
    def __init__(self, repository: MongoRepository, storage: GridFsStorage) -> None:
        self._repository = repository
        self._storage = storage

    async def upload(self, file: UploadFile) -> dict[str, Any]:
        filename = file.filename or "document.pdf"
        if Path(filename).suffix.lower() != ".pdf":
            raise DomainError("只支持上传 PDF 文件", code=4151, status_code=415)

        sha256, size = await self._storage.digest_pdf(file)
        existing = await self._repository.get_document_by_sha256(sha256)
        if existing is not None:
            return existing

        file_id = await self._storage.store_pdf(file, sha256=sha256)
        try:
            return await self._repository.create_document(
                filename=filename,
                content_type=file.content_type or "application/pdf",
                size=size,
                sha256=sha256,
                gridfs_id=file_id,
            )
        except DuplicateKeyError:
            await self._storage.delete(file_id)
            reused = await self._repository.get_document_by_sha256(sha256)
            if reused is not None:
                return reused
            raise
        except Exception:
            await self._storage.delete(file_id)
            raise

    async def get(self, document_id: str) -> dict[str, Any]:
        return await self._repository.get_document(document_id)
