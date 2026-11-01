"""Supervise independent background loops with explicit task dependencies."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
Iteration = Callable[[], Awaitable[None]]


class BackgroundCoordinator:
    def __init__(
        self, *, interval: float, iterations: tuple[Iteration, ...], synchronize: Iteration
    ) -> None:
        self.interval, self.iterations, self.synchronize = interval, iterations, synchronize

    async def synchronize_plugin_policy(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.synchronize()
            except Exception:
                logger.exception("Plugin policy synchronization failed")

    async def tick(self) -> None:
        for callback in self.iterations:
            await callback()

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            for callback in self.iterations:
                group.create_task(self._poll(callback), name=f"coordinator:{callback.__name__}")

    async def _poll(self, callback: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Coordinator iteration failed")
            await asyncio.sleep(self.interval)
