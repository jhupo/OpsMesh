"""Bounded control flow over the shared deterministic graph executor."""

import asyncio

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.conditions import evaluate_condition
from opsmesh.workflows.contracts import (
    ForeachNode,
    ParallelNode,
    RepeatNode,
    SubworkflowNode,
    WaitNode,
    WorkflowGraph,
)
from opsmesh.workflows.nodes.base import NodeRequest
from opsmesh.workflows.values import foreach_inputs, local_output


async def execute(request: NodeRequest) -> object:
    node, runner = request.node, request.executor
    if isinstance(node, SubworkflowNode):
        compiled = runner.context.prepared.plan.workflows[node.revision_id]
        return await runner.compiled(
            compiled,
            request.arguments,
            request.path,
            (*request.limits, asyncio.Semaphore(compiled.spec.limits.max_concurrency)),
            request.depth + 1,
        )
    if isinstance(node, WaitNode):
        if node.wait_kind == "timer":
            assert node.seconds is not None
            await runner.context.runtime.sleep(node.seconds)
            return request.arguments
        assert node.signal_name is not None and node.timeout_seconds is not None
        return await runner.context.runtime.signal(
            request.path, node.signal_name, node.timeout_seconds
        )
    if isinstance(node, ParallelNode):
        semaphore = asyncio.Semaphore(node.concurrency)

        async def branch(index: int, graph: WorkflowGraph) -> object:
            async with semaphore:
                result = await runner.graph(
                    graph,
                    request.arguments,
                    f"{request.path}/branch_{index}",
                    request.limits,
                    request.depth + 1,
                )
                return result.output

        tasks = [
            asyncio.create_task(branch(index, graph)) for index, graph in enumerate(node.branches)
        ]
        if node.join == "all":
            return await runner.all(tasks)
        return await runner.quorum(tasks, node.quorum if node.quorum is not None else 1)
    if isinstance(node, ForeachNode):
        semaphore = asyncio.Semaphore(node.concurrency)

        async def iteration(index: int, value: object) -> object:
            async with semaphore:
                result = await runner.graph(
                    node.body,
                    value,
                    f"{request.path}/item_{index}",
                    request.limits,
                    request.depth + 1,
                )
                return result.output

        return await runner.all(
            [
                asyncio.create_task(iteration(index, value))
                for index, value in enumerate(foreach_inputs(node, request.scope))
            ]
        )
    if isinstance(node, RepeatNode):
        value: object = request.arguments
        for index in range(node.max_iterations):
            result = await runner.graph(
                node.body,
                {"value": value, "iteration": index},
                f"{request.path}/iteration_{index}",
                request.limits,
                request.depth + 1,
            )
            value = result.output
            if evaluate_condition(node.until, result.scope):
                return value
        raise InvalidArgumentError("Repeat exhausted its maximum iterations")
    return local_output(node, request.scope)
