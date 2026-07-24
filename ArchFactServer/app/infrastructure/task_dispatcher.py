import asyncio
from collections.abc import Awaitable, Callable

JobRunner = Callable[[str], Awaitable[None]]


class LocalJobDispatcher:
    """Development dispatcher with the same boundary as a future Celery adapter."""

    def __init__(self) -> None:
        self._runner: JobRunner | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def bind(self, runner: JobRunner) -> None:
        self._runner = runner

    async def dispatch(self, job_id: str) -> None:
        if self._runner is None:
            raise RuntimeError("任务执行器尚未绑定")
        task = asyncio.create_task(self._runner(job_id), name=f"extraction:{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    async def cancel(self, job_id: str) -> bool:
        """Cancel the in-process orchestration task and wait for its cleanup."""
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
