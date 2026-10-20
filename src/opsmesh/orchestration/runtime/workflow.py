"""One explicit runtime operation; external effects are never retried blindly."""

import asyncio
from datetime import timedelta
from typing import cast

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.orchestration.runtime.contracts import MCPCommand, RuntimeCommand, RuntimeResult


@workflow.defn(name="ExecutionRuntimeWorkflow")
class ExecutionRuntimeWorkflow:
    def __init__(self) -> None:
        self.operation: asyncio.Task[RuntimeResult] | None = None
        self.cancelled = False
        self.commands: set[str] = set()

    @workflow.signal
    def cancel(self, command_id: str) -> None:
        if command_id in self.commands:
            return
        self.commands.add(command_id)
        self.cancelled = True
        if self.operation is not None:
            self.operation.cancel()

    @workflow.run
    async def run(self, command: RuntimeCommand) -> RuntimeResult:
        self.operation = asyncio.create_task(self.execute(command))
        if self.cancelled:
            self.operation.cancel()
        return await self.operation

    async def execute(self, command: RuntimeCommand) -> RuntimeResult:
        return cast(
            RuntimeResult,
            await workflow.execute_activity(
                "orchestration.runtime",
                command,
                result_type=RuntimeResult,
                start_to_close_timeout=timedelta(minutes=65),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            ),
        )


@workflow.defn(name="ExecutionMCPWorkflow")
class ExecutionMCPWorkflow:
    def __init__(self) -> None:
        self.operation: asyncio.Task[RuntimeResult] | None = None
        self.cancelled = False
        self.commands: set[str] = set()

    @workflow.signal
    def cancel(self, command_id: str) -> None:
        if command_id in self.commands:
            return
        self.commands.add(command_id)
        self.cancelled = True
        if self.operation is not None:
            self.operation.cancel()

    @workflow.run
    async def run(self, command: MCPCommand) -> RuntimeResult:
        self.operation = asyncio.create_task(self.execute(command))
        if self.cancelled:
            self.operation.cancel()
        return await self.operation

    async def execute(self, command: MCPCommand) -> RuntimeResult:
        return cast(
            RuntimeResult,
            await workflow.execute_activity(
                "orchestration.mcp.lifecycle",
                command,
                result_type=RuntimeResult,
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            ),
        )
