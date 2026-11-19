"""Live saga runs: each checkout saga is a background task that keeps going if the caller disconnects, and its
events are kept so a second caller (the double-click) can attach and see the whole story from the start."""
import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)
KEEP_FINISHED_SECONDS = 30


class RunBroken(Exception):
    """The saga died before finishing (crash or lost dependency); recovery will settle the order."""


class SagaRun:
    def __init__(self, order_id: str):
        self.order_id = order_id
        self.events: list = []
        self.done = False
        self.broken = False
        self._changed = asyncio.Condition()

    @property
    def live(self) -> bool:
        return not (self.done or self.broken)

    async def emit(self, event) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    async def end(self, broken: bool = False) -> None:
        async with self._changed:
            self.done, self.broken = True, broken
            self._changed.notify_all()

    async def stream(self):
        """Every event so far, then new ones as they happen. Raises RunBroken at the end of a broken run."""
        index = 0
        while True:
            async with self._changed:
                await self._changed.wait_for(lambda: index < len(self.events) or self.done)
                batch, finished = self.events[index:], self.done  # `done` is set after the last emit: the batch is complete
            index += len(batch)
            for event in batch:
                yield event
            if finished:
                if self.broken:
                    raise RunBroken(self.order_id)
                return


class Runs:
    """Registry of running sagas, so attaching callers and recovery can see what is alive."""

    def __init__(self):
        self._runs: dict[str, SagaRun] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, order_id: str) -> SagaRun | None:
        return self._runs.get(order_id)

    def is_live(self, order_id: str) -> bool:
        run = self._runs.get(order_id)
        return run is not None and run.live

    def start(self, order_id: str, work: Callable[[SagaRun], Coroutine[Any, Any, None]]) -> SagaRun:
        """Register the run BEFORE any await so a concurrent caller with the same key always finds it."""
        run = SagaRun(order_id)
        self._runs[order_id] = run
        task = asyncio.create_task(self._drive(run, work), name=f"saga-{order_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _drive(self, run: SagaRun, work) -> None:
        broken = False
        try:
            await work(run)
        except asyncio.CancelledError:
            broken = True
            raise
        except Exception:
            broken = True
            log.exception("saga for %s was interrupted; recovery will settle it", run.order_id)
        finally:
            await asyncio.shield(run.end(broken=broken))
            asyncio.get_running_loop().call_later(KEEP_FINISHED_SECONDS, self._forget, run)

    def _forget(self, run: SagaRun) -> None:
        if self._runs.get(run.order_id) is run:
            del self._runs[run.order_id]

    async def cancel_all(self) -> None:
        """Server shutdown: running sagas die here, exactly like a crash, and are left for recovery."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
