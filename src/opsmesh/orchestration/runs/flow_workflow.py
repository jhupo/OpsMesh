"""The sole deterministic flow driver; all authorization and effects are Activities."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.orchestration.definitions.contracts import (
    FlowGraph,
    FlowNode,
    FlowProjection,
    FlowReference,
    StepCommand,
)
from opsmesh.orchestration.runs.steps import compare, map_value


class FlowStopped(Exception):
    pass


class StepUncertain(Exception):
    pass


@workflow.defn
class FlowWorkflow:
    def __init__(self) -> None:
        self.changed = True
        self.paused = False
        self.cancelled = False
        self.cursor = 0
        self.projection = 0
        self.revision = 0
        self.state: dict[str, Any] = {}
        self.reference: FlowReference
        self.control_lock = asyncio.Lock()

    @workflow.signal
    def wake(self, command_id: str | None = None) -> None:
        self.changed = True

    async def call(self, name: str, value: Any) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            name,
            value,
            result_type=dict[str, Any],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=self.state.get("policy", {}).get("activity_attempts", 3)
            ),
        )
        return result

    async def refresh(self) -> None:
        async with self.control_lock:
            if not self.changed:
                return
            self.changed = False
            self.state = await self.call("flows.load", str(self.reference.run_id))
            self.revision = self.state["revision"]
            for command in self.state["commands"]:
                if command["sequence"] <= self.cursor:
                    continue
                self.cursor = command["sequence"]
                if command["kind"] == "pause":
                    self.paused = True
                elif command["kind"] == "resume":
                    self.paused = False
                elif command["kind"] == "cancel":
                    self.cancelled = True

    async def project(self, status: str, output: Any = None, error: str | None = None) -> None:
        if self.state.get("status") == status and output is None and error is None:
            return
        while True:
            self.projection += 1
            result = await self.call(
                "flows.project",
                FlowProjection(
                    run_id=self.reference.run_id,
                    driver_generation=self.reference.generation,
                    expected_revision=self.revision,
                    projection_id=f"{self.reference.generation}:projection:{self.projection}",
                    status=status,
                    output=output,
                    error_code=error,
                ),
            )
            self.revision = result["revision"]
            if result["applied"]:
                self.state["status"] = result["status"]
                return

    async def tick(self) -> None:
        try:
            await workflow.wait_condition(lambda: self.changed, timeout=timedelta(seconds=2))
        except TimeoutError:
            self.changed = True

    async def gate(self) -> None:
        await self.refresh()
        while self.paused and not self.cancelled:
            await self.project("paused")
            await self.tick()
            await self.refresh()
        if self.cancelled:
            raise FlowStopped()
        if not self.paused and self.state.get("status") not in {
            "queued",
            "succeeded",
            "failed",
            "cancelled",
            "needs_attention",
        }:
            await self.project("waiting" if self.state.get("waiting") else "running")
        if workflow.now() >= datetime.fromisoformat(self.state["deadline"]):
            raise TimeoutError("The flow deadline passed")

    async def settle(self, prefix: str = "") -> bool:
        while True:
            result = await self.call(
                "flows.cancel_steps",
                {
                    "run_id": str(self.reference.run_id),
                    "prefix": prefix,
                    "driver_generation": self.reference.generation,
                },
            )
            if result["uncertain"]:
                return False
            if not result["pending"]:
                return True
            await workflow.sleep(timedelta(seconds=2))

    @workflow.run
    async def run(self, reference: FlowReference) -> dict[str, Any]:
        self.reference = reference
        self.cursor = reference.command_cursor
        try:
            await self.refresh()
            await self.gate()
            await self.project("running")
            graph = FlowGraph.model_validate(self.state["graph"])
            output = await self.run_graph(graph, {"input": self.state["input"]})
            await self.project("succeeded", output)
            return {"status": "succeeded", "output": output}
        except (Exception, asyncio.CancelledError) as captured:
            error = captured
            known = False
            try:
                known = await self.settle()
                self.changed = True
                await self.refresh()
            except (Exception, asyncio.CancelledError):
                known = False
            if self.cancelled:
                error = FlowStopped()
            status = (
                "cancelled"
                if known and isinstance(error, FlowStopped)
                else "failed"
                if known and not isinstance(error, StepUncertain)
                else "needs_attention"
            )
            code = (
                "flow_cancelled"
                if isinstance(error, FlowStopped)
                else "flow_timeout"
                if isinstance(error, TimeoutError)
                else "child_needs_attention"
                if isinstance(error, StepUncertain)
                else "flow_failed"
            )
            await self.project(status, error=code)
            return {"status": status, "error_code": code}

    async def run_graph(
        self,
        graph: FlowGraph,
        inherited: dict[str, Any],
        prefix: str = "",
        concurrency: int | None = None,
        join: str = "all",
        quorum: int = 1,
    ) -> Any:
        nodes = {node.key: node for node in graph.nodes}
        results: dict[str, Any] = {}
        errors: dict[str, BaseException] = {}
        pending = dict(nodes)
        tasks: dict[str, asyncio.Task[Any]] = {}
        limit = concurrency or self.state["limits"]["max_concurrency"]
        sinks = set(nodes) - {key for node in graph.nodes for key in node.depends_on}
        required = len(sinks) if join == "all" else 1 if join == "any" else quorum
        if required > len(sinks):
            raise ValueError("The join quorum exceeds its branch count")
        try:
            while pending or tasks:
                await self.gate()
                context = {**inherited, "steps": results}
                for key, node in list(pending.items()):
                    if len(tasks) >= limit:
                        break
                    dependencies = set(node.depends_on)
                    ready = dependencies <= results.keys()
                    if node.kind == "join" and node.join != "all":
                        required_dependencies = 1 if node.join == "any" else node.quorum
                        ready = len(dependencies & results.keys()) >= required_dependencies
                    if not ready:
                        continue
                    if node.kind == "join" and node.join != "all":
                        for dependency in sorted(dependencies - results.keys()):
                            pending.pop(dependency, None)
                            if dependency in tasks:
                                tasks[dependency].cancel()
                                await asyncio.gather(tasks.pop(dependency), return_exceptions=True)
                            if not await self.settle(prefix + dependency):
                                raise StepUncertain()
                            results[dependency] = None
                    task = asyncio.create_task(
                        self.run_node(node, context, prefix + key), name=prefix + key
                    )
                    tasks[key] = task
                    pending.pop(key)
                if not tasks:
                    if errors:
                        raise next(iter(errors.values()))
                    raise ValueError("The flow graph cannot make progress")
                finished, _ = await workflow.wait(
                    tasks.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for key in sorted(key for key, task in tasks.items() if task in finished):
                    task = tasks.pop(key)
                    try:
                        results[key] = task.result()
                    except (Exception, asyncio.CancelledError) as error:
                        errors[key] = error
                successes = sinks & results.keys()
                if join != "all" and len(successes) >= required:
                    for task in tasks.values():
                        task.cancel()
                    await asyncio.gather(*tasks.values(), return_exceptions=True)
                    tasks.clear()
                    for key in sorted(set(nodes) - results.keys()):
                        if not await self.settle(prefix + key):
                            raise StepUncertain()
                    return {key: results[key] for key in sorted(successes)}
                if errors and (join == "all" or len(sinks - errors.keys()) < required):
                    raise next(iter(errors.values()))
            if errors:
                raise next(iter(errors.values()))
            return map_value(graph.output, {**inherited, "steps": results})
        finally:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def run_node(self, node: FlowNode, context: dict[str, Any], path: str) -> Any:
        await self.gate()
        arguments = map_value(node.arguments, context)
        skipped = node.run_if is not None and not compare(node.run_if, context)
        while True:
            result = await self.call(
                "flows.begin_step",
                StepCommand(
                    run_id=self.reference.run_id,
                    path=path,
                    node=node,
                    arguments=arguments,
                    skip=skipped,
                    driver_generation=self.reference.generation,
                ),
            )
            if result["status"] not in {"capacity_wait", "paused", "cancelling"}:
                break
            await self.tick()
            await self.gate()
        if result["status"] == "completed":
            return result.get("result")
        if result["status"] == "needs_attention" and result.get("child_kind"):
            result = await self.call("flows.probe_step", self.step_payload(path))
            if result["status"] == "completed":
                return result.get("result")
        if result["status"] in {"failed", "cancelled", "needs_attention"}:
            if result["status"] == "needs_attention":
                raise StepUncertain()
            raise ValueError("The recorded step did not complete successfully")
        if skipped:
            if result.get("child_kind"):
                if not await self.settle(path):
                    raise StepUncertain()
                return None
            await self.call(
                "flows.finish_step",
                {
                    **self.step_payload(path),
                    "status": "completed",
                    "value": None,
                },
            )
            return None
        if node.kind == "condition":
            assert node.condition is not None
            value: Any = compare(node.condition, context)
        elif node.kind == "join":
            value = {
                key: context["steps"][key] for key in node.depends_on if key in context["steps"]
            }
        elif node.kind == "parallel":
            assert node.body is not None
            value = await self.run_graph(
                node.body,
                {"input": arguments},
                path + "/0/",
                concurrency=node.concurrency,
                join=node.join,
                quorum=node.quorum,
            )
        elif node.kind in {"foreach", "repeat"}:
            assert node.body is not None
            if node.kind == "foreach":
                items = arguments.get("items")
                if not isinstance(items, list) or len(items) > node.max_iterations:
                    raise ValueError("The foreach collection exceeds its frozen bound")
                semaphore = asyncio.Semaphore(node.concurrency)

                async def iteration(index: int, item: Any) -> Any:
                    async with semaphore:
                        assert node.body is not None
                        return await self.run_graph(
                            node.body,
                            {"input": arguments, "item": item, "index": index},
                            f"{path}/{index}/",
                        )

                children = [
                    asyncio.create_task(iteration(index, item)) for index, item in enumerate(items)
                ]
                try:
                    value = await asyncio.gather(*children)
                finally:
                    for child in children:
                        if not child.done():
                            child.cancel()
                    await asyncio.gather(*children, return_exceptions=True)
            else:
                value = []
                for index in range(node.max_iterations):
                    current = await self.run_graph(
                        node.body,
                        {"input": arguments, "index": index, "item": value[-1] if value else None},
                        f"{path}/{index}/",
                    )
                    value.append(current)
                    if node.condition and compare(
                        node.condition, {"input": current, "index": index}
                    ):
                        break
        elif node.kind == "expand":
            while "graph" not in result:
                await self.tick()
                await self.gate()
                result = await self.call("flows.probe_step", self.step_payload(path))
            value = await self.run_graph(
                FlowGraph.model_validate(result["graph"]), {"input": arguments}, path + "/0/"
            )
        else:
            while True:
                await self.gate()
                result = await self.call("flows.probe_step", self.step_payload(path))
                if result["status"] == "completed":
                    return result.get("result")
                if result["status"] == "needs_attention":
                    raise StepUncertain()
                if result["status"] in {"failed", "cancelled"}:
                    raise ValueError("The child execution did not complete successfully")
                await self.tick()
        result = await self.call(
            "flows.finish_step",
            {
                **self.step_payload(path),
                "status": "completed",
                "value": value,
            },
        )
        if result["status"] != "completed":
            raise StepUncertain()
        return value

    def step_payload(self, path: str) -> dict[str, Any]:
        return {
            "run_id": str(self.reference.run_id),
            "path": path,
            "driver_generation": self.reference.generation,
        }
