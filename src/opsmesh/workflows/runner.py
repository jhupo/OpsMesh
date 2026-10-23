"""Engine-independent bounded graph execution; effects use injected runtime ports."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.context import GraphResult, WorkflowContext
from opsmesh.workflows.contracts import (
    CompiledWorkflow,
    NodeFailure,
    UncertainEffects,
    WorkflowGraph,
    WorkflowNode,
)
from opsmesh.workflows.graph import ordered_nodes
from opsmesh.workflows.mappings import resolve_mapping
from opsmesh.workflows.nodes.base import NodeRequest
from opsmesh.workflows.nodes.registry import NodeRegistry, builtin_nodes
from opsmesh.workflows.values import node_arguments, should_run, validate_value


class GraphRunner:
    def __init__(self, context: WorkflowContext, *, nodes: NodeRegistry | None = None) -> None:
        self.context, self.nodes = context, nodes or builtin_nodes()
        self.steps = 0
        self.limit = asyncio.Semaphore(context.prepared.plan.entry.spec.limits.max_concurrency)

    async def run(self, value: object) -> object:
        return await self.compiled(
            self.context.prepared.plan.entry, value, "entry", (self.limit,), 1
        )

    async def compiled(
        self,
        compiled: CompiledWorkflow,
        value: object,
        path: str,
        limits: tuple[asyncio.Semaphore, ...],
        depth: int,
    ) -> object:
        validate_value(value, compiled.spec.input_schema)
        result = await asyncio.wait_for(
            self.graph(compiled.spec.graph, value, path, limits, depth),
            timeout=compiled.spec.limits.timeout_seconds,
        )
        validate_value(result.output, compiled.spec.output_schema)
        return result.output

    async def graph(
        self,
        graph: WorkflowGraph,
        value: object,
        path: str,
        limits: tuple[asyncio.Semaphore, ...],
        depth: int,
    ) -> GraphResult:
        if depth > self.context.prepared.plan.entry.spec.limits.max_depth:
            raise InvalidArgumentError("Workflow execution exceeds max_depth")
        steps: dict[str, object] = {}
        scope: dict[str, object] = {"input": value, "steps": steps}
        tasks: dict[str, asyncio.Task[object]] = {}

        async def execute(node: WorkflowNode) -> object:
            for dependency in node.depends_on:
                await tasks[dependency]
            self.steps += 1
            if self.steps > self.context.prepared.plan.entry.spec.limits.max_steps:
                raise InvalidArgumentError("Workflow execution exceeds max_steps")
            node_path = f"{path}/{node.key}"
            arguments = node_arguments(node, scope) if should_run(node, scope) else {}
            await self.context.runtime.begin_node(node_path, node.kind, arguments)
            if not should_run(node, scope):
                steps[node.key] = {"status": "skipped"}
                await self.context.runtime.finish_node(node_path, "skipped")
                return None
            try:
                output = await self.nodes.get(node.kind)(
                    NodeRequest(node, arguments, scope, node_path, limits, depth, self)
                )
                validate_value(output, node.output_schema)
                await self.context.runtime.finish_node(node_path, "completed", output)
                steps[node.key] = {"status": "completed", "output": output}
                return output
            except BaseException as error:
                await asyncio.shield(
                    self.context.runtime.finish_node(
                        node_path,
                        "cancelled"
                        if self.context.runtime.is_cancelled(error)
                        else "needs_attention"
                        if isinstance(error, UncertainEffects)
                        else "failed",
                    )
                )
                raise

        for node in ordered_nodes(graph):
            tasks[node.key] = asyncio.create_task(execute(node))
        await self.all(list(tasks.values()))
        return GraphResult(resolve_mapping(graph.output, scope), scope)

    async def leaf(
        self, limits: tuple[asyncio.Semaphore, ...], operation: Callable[[], Awaitable[object]]
    ) -> object:
        async with AsyncExitStack() as stack:
            for limit in limits:
                await stack.enter_async_context(limit)
            return await operation()

    async def drain(
        self, tasks: list[asyncio.Task[Any]], *, confirmed_failures: bool = False
    ) -> None:
        requested = {task for task in tasks if not task.done()}
        for task in requested:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, UncertainEffects):
                raise result
        for task, result in zip(tasks, results, strict=True):
            if not isinstance(result, BaseException):
                continue
            if task in requested and self.context.runtime.is_cancelled(result):
                continue
            if (
                confirmed_failures
                and isinstance(result, NodeFailure)
                and not isinstance(result, UncertainEffects)
            ):
                continue
            raise result

    async def all(self, tasks: list[asyncio.Task[object]]) -> list[object]:
        try:
            return list(await asyncio.gather(*tasks))
        except (Exception, asyncio.CancelledError):
            await self.drain(tasks)
            raise

    async def quorum(self, tasks: list[asyncio.Task[object]], count: int) -> object:
        successful: dict[int, object] = {}
        pending = list(tasks)
        try:
            while pending and len(successful) < count:
                await self.context.runtime.wait_tasks(pending)
                for task in list(pending):
                    if task.done():
                        pending.remove(task)
                        if task.cancelled():
                            raise asyncio.CancelledError()
                        error = task.exception()
                        if error is None and len(successful) < count:
                            successful[tasks.index(task)] = task.result()
                        elif error is not None and (
                            not isinstance(error, NodeFailure)
                            or isinstance(error, UncertainEffects)
                        ):
                            raise error
                if len(successful) + len(pending) < count:
                    raise NodeFailure("Parallel branches cannot satisfy quorum")
            result = [successful[index] for index in sorted(successful)[:count]]
        except (Exception, asyncio.CancelledError):
            await self.drain(tasks, confirmed_failures=True)
            raise
        await self.drain(tasks, confirmed_failures=True)
        return result
