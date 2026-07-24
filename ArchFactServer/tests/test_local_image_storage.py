import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import DomainError
from app.infrastructure.local_image_storage import LocalImageStorage


def test_local_image_storage_writes_relative_object_key(tmp_path: Path) -> None:
    storage = LocalImageStorage(Settings(app_env="test", file_storage_root=tmp_path))
    object_key = "documents/doc_test/pages/0001/rendered/page.png"

    path = asyncio.run(storage.write(object_key, b"png-content"))

    assert path == tmp_path / "documents/doc_test/pages/0001/rendered/page.png"
    assert path.read_bytes() == b"png-content"


def test_local_image_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalImageStorage(Settings(app_env="test", file_storage_root=tmp_path))

    with pytest.raises(DomainError):
        storage.resolve("../outside.png")
