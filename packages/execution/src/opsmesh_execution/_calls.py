"""Process-local active tasks owned by the execution lifecycle."""

import asyncio
import logging
from dataclasses import dataclass, field

from .errors import ExecutionClosed

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ActiveCall:
    """One consumer task and its cleanup completion signal."""

    task: asyncio.Task
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class ActiveCalls:
    """Stop admission and cancel active consumers during host shutdown."""

    def __init__(self) -> None:
        self.accepting = True
        self._calls: set[ActiveCall] = set()

    def begin(self) -> ActiveCall:
        if not self.accepting:
            raise ExecutionClosed("Execution is stopping")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Execution requires an asyncio task")
        call = ActiveCall(task)
        self._calls.add(call)
        return call

    def end(self, call: ActiveCall) -> None:
        self._calls.discard(call)
        call.finished.set()

    def stop_accepting(self) -> None:
        self.accepting = False

    async def close(self, timeout: float) -> None:
        self.stop_accepting()
        calls = tuple(self._calls)
        current = asyncio.current_task()
        for task in {call.task for call in calls if call.task is not current}:
            task.cancel()
        if not calls:
            return
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*(call.finished.wait() for call in calls))
        except TimeoutError:
            logger.warning("Execution cleanup exceeded the shutdown budget")
