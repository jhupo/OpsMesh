"""Deterministic extension commands; all package and database I/O is Activity-owned."""

import asyncio
from datetime import timedelta
from typing import Any, cast

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.extensions.schemas import ExtensionWork


@workflow.defn(name="ExtensionWorkflow")
class ExtensionWorkflow:
    def __init__(self) -> None:
        self.pending: asyncio.Task[dict[str, Any]] | None = None
        self.cancel_requested = False
        self.commands: set[str] = set()
        self.stopping_worker = False

    @workflow.signal
    def stop_worker(self) -> None:
        self.stopping_worker = True

    @workflow.signal
    def cancel(self, command_id: str) -> None:
        if command_id in self.commands:
            return
        self.commands.add(command_id)
        self.cancel_requested = True
        if self.pending is not None:
            self.pending.cancel()

    @workflow.run
    async def run(self, work: ExtensionWork) -> dict[str, Any]:
        cycles = 0
        while True:
            command = (
                work.model_copy(update={"action": "stop_worker"}) if self.stopping_worker else work
            )
            self.pending = asyncio.create_task(self.execute(command))
            if self.cancel_requested and work.action == "invoke":
                self.pending.cancel()
            result = await self.pending
            if result["status"] not in {"draining", "queued"} and not (
                work.action == "start_worker"
                and result["status"] in {"ready", "needs_attention", "stopping"}
            ):
                return result
            await workflow.sleep(timedelta(seconds=5))
            cycles += 1
            if work.action == "start_worker" and cycles >= 500 and not self.stopping_worker:
                workflow.continue_as_new(work)

    async def execute(self, work: ExtensionWork) -> dict[str, Any]:
        result = await workflow.execute_activity(
            "extensions.operate",
            work,
            result_type=dict[str, Any],
            start_to_close_timeout=timedelta(minutes=65),
            heartbeat_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=2),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        return cast(dict[str, Any], result)
