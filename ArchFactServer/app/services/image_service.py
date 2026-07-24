import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

import fitz

from app.core.errors import DomainError
from app.infrastructure.gridfs_storage import GridFsStorage
from app.infrastructure.local_image_storage import LocalImageStorage
from app.repositories.mongo_repository import MongoRepository


class ImageService:
    def __init__(
        self,
        repository: MongoRepository,
        pdf_storage: GridFsStorage,
        image_storage: LocalImageStorage,
    ) -> None:
        self._repository = repository
        self._pdf_storage = pdf_storage
        self._image_storage = image_storage

    async def render_page(self, document_id: str, page_no: int) -> dict[str, Any]:
        if page_no < 1:
            raise DomainError("页码必须大于 0", code=4228, status_code=422)

        document = await self._repository.get_document(document_id)
        existing = await self._repository.get_page_render_image(document_id, page_no)
        if (
            existing is not None
            and self._image_storage.resolve(existing["storage"]["object_key"]).is_file()
        ):
            return existing

        with tempfile.TemporaryDirectory(prefix="archfact-image-") as temporary_directory:
            pdf_path = Path(temporary_directory) / "document.pdf"
            await self._pdf_storage.download_to_path(document["storage"]["file_id"], pdf_path)
            content, width, height = await asyncio.to_thread(self._render_sync, pdf_path, page_no)

        image_id = (
            "img_"
            + hashlib.sha256(f"{document_id}:page:{page_no}:render".encode()).hexdigest()[:24]
        )
        object_key = f"documents/{document_id}/pages/{page_no:04d}/rendered/page.png"
        await self._image_storage.write(object_key, content)
        return await self._repository.upsert_document_image(
            {
                "id": image_id,
                "document_id": document_id,
                "page_no": page_no,
                "image_type": "page_render",
                "content_type": "image/png",
                "width": width,
                "height": height,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "storage": {"type": "local", "object_key": object_key},
            }
        )

    async def list_images(self, document_id: str) -> list[dict[str, Any]]:
        await self._repository.get_document(document_id)
        return await self._repository.list_document_images(document_id)

    async def get_image(self, document_id: str, image_id: str) -> dict[str, Any]:
        await self._repository.get_document(document_id)
        return await self._repository.get_document_image(document_id, image_id)

    def get_content_path(self, image: dict[str, Any]) -> Path:
        path = self._image_storage.resolve(image["storage"]["object_key"])
        if not path.is_file():
            raise DomainError("图片文件不存在", code=4042, status_code=404)
        return path

    async def get_region_crop_path(self, job_id: str, region_id: str) -> Path:
        region = await self._repository.get_region(job_id, region_id)
        object_key = region.get("crop_object_key")
        if not object_key:
            raise DomainError("当前检测区域没有裁剪图", code=4043, status_code=404)
        path = self._image_storage.resolve(object_key)
        if not path.is_file():
            raise DomainError("检测区域裁剪图不存在", code=4044, status_code=404)
        return path

    @staticmethod
    def _render_sync(pdf_path: Path, page_no: int) -> tuple[bytes, int, int]:
        with fitz.open(pdf_path) as document:
            if page_no > document.page_count:
                raise DomainError("页码超出 PDF 总页数", code=4229, status_code=422)
            page = document.load_page(page_no - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            return pixmap.tobytes("png"), pixmap.width, pixmap.height
