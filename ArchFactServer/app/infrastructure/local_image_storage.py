import asyncio
from pathlib import Path, PurePosixPath

from app.core.config import Settings
from app.core.errors import DomainError


class LocalImageStorage:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.file_storage_root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def write(self, object_key: str, content: bytes) -> Path:
        target = self.resolve(object_key)
        await asyncio.to_thread(self._write_sync, target, content)
        return target

    def resolve(self, object_key: str) -> Path:
        key = PurePosixPath(object_key)
        if key.is_absolute() or not key.parts or ".." in key.parts:
            raise DomainError("图片存储路径无效", code=4226, status_code=422)

        target = self._root.joinpath(*key.parts).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise DomainError("图片存储路径越界", code=4227, status_code=422) from exc
        return target

    @staticmethod
    def _write_sync(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
