"""Keyset iteration over a bounded Run query, even when processed Runs change status."""

from collections.abc import AsyncIterator

from .api import AgentRuntime, AgentRunView


async def iter_runs(
    runtime: AgentRuntime, *, statuses: tuple[str, ...]
) -> AsyncIterator[AgentRunView]:
    cursor: str | None = None
    while page := await runtime.list_runs(statuses=statuses, after_id=cursor):
        cursor = page[-1].id
        for run in page:
            yield run
