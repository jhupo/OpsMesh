"""Node extension contracts contain no database or scheduling SDK objects."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from opsmesh.workflows.context import GraphResult, WorkflowContext
from opsmesh.workflows.contracts import CompiledWorkflow, WorkflowGraph, WorkflowNode


class GraphExecutor(Protocol):
    context: WorkflowContext

    async def graph(
        self,
        graph: WorkflowGraph,
        value: object,
        path: str,
        limits: tuple[asyncio.Semaphore, ...],
        depth: int,
    ) -> GraphResult: ...
    async def compiled(
        self,
        compiled: CompiledWorkflow,
        value: object,
        path: str,
        limits: tuple[asyncio.Semaphore, ...],
        depth: int,
    ) -> object: ...
    async def leaf(
        self, limits: tuple[asyncio.Semaphore, ...], operation: Callable[[], Awaitable[object]]
    ) -> object: ...
    async def all(self, tasks: list[asyncio.Task[object]]) -> list[object]: ...
    async def quorum(self, tasks: list[asyncio.Task[object]], count: int) -> object: ...


@dataclass(frozen=True)
class NodeRequest:
    node: WorkflowNode
    arguments: dict[str, object]
    scope: dict[str, object]
    path: str
    limits: tuple[asyncio.Semaphore, ...]
    depth: int
    executor: GraphExecutor


class NodeHandler(Protocol):
    async def __call__(self, request: NodeRequest) -> object: ...
