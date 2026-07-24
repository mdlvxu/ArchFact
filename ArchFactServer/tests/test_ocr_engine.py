import asyncio
import sys
from pathlib import Path

import pytest

from app.services.ocr_engine import OcrPageInput, TesseractOcrEngine


def test_cancelling_tesseract_recognition_kills_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            started.set()
            await asyncio.Event().wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    process = FakeProcess()

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    image_path = tmp_path / "page.png"
    image_path.touch()
    engine = TesseractOcrEngine(
        command=sys.executable,
        languages="chi_sim",
        page_segmentation_mode=6,
        min_confidence=0.4,
    )

    async def run() -> None:
        task = asyncio.create_task(
            engine.recognize(
                OcrPageInput(
                    page_no=1,
                    image_path=image_path,
                    width=100,
                    height=100,
                )
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert process.killed is True
