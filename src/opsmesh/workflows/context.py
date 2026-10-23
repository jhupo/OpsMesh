"""Engine-neutral asynchronous services used by deterministic graph execution."""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from opsmesh.workflows.contracts import AgentBinding, PreparedWorkflow, ToolBinding


class WorkflowRuntime(Protocol):
    async def begin_node(self, path: str, kind: str, arguments: dict[str, object]) -> None: ...
    async def finish_node(self, path: str, status: str, output: object = None) -> None: ...
    async def agent(
        self, path: str, binding: AgentBinding, arguments: dict[str, object]
    ) -> object: ...
    async def tool(
        self, path: str, binding: ToolBinding, operation: str, arguments: dict[str, object]
    ) -> object: ...
    async def sleep(self, seconds: float) -> None: ...
    async def signal(self, path: str, name: str, timeout: float) -> object: ...
    async def wait_tasks(self, tasks: list[asyncio.Task[Any]]) -> None: ...
    def is_cancelled(self, error: BaseException) -> bool: ...


@dataclass(frozen=True)
class WorkflowContext:
    run_id: UUID
    prepared: PreparedWorkflow
    runtime: WorkflowRuntime


@dataclass(frozen=True)
class GraphResult:
    output: object
    scope: dict[str, object]
