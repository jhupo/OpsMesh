"""Wait for durable Run state without assigning ownership of the Run to its waiter."""

import asyncio
from collections.abc import Awaitable, Callable

from opsmesh.modules.runtime.api import TERMINAL_STATES, AgentRunView


async def wait_for_run(
    read: Callable[[], Awaitable[AgentRunView]],
    *,
    interval: float,
    initial: AgentRunView | None = None,
    validate: Callable[[AgentRunView], None] | None = None,
) -> AgentRunView:
    run = initial or await read()
    while True:
        if validate is not None:
            validate(run)
        if run.status in TERMINAL_STATES or run.status == "waiting_approval":
            return run
        await asyncio.sleep(interval)
        run = await read()
