"""Native Runner orchestration under the official Agents Temporal plugin."""

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.orchestration.execution.contracts import PreparedRun, RunReference
    from opsmesh.orchestration.execution.hooks import HookOccurrence, workflow_hooks
    from opsmesh.orchestration.execution.runner import AgentRunner
    from opsmesh.orchestration.runs.contracts import ApprovalCheckpoint, RunClaim, RunCompletion


@workflow.defn(name="ExecutionRunWorkflow")
class ExecutionRunWorkflow:
    def __init__(self) -> None:
        self.cancelled = False
        self.woken = False
        self.decisions: set[UUID] = set()
        self.commands: set[str] = set()
        self.pending_tools: set[asyncio.Task[Any]] = set()
        self.hook_entered: list[str] = []
        self.has_hooks = False

    @workflow.signal
    def cancel(self, command_id: str) -> None:
        if command_id in self.commands:
            return
        self.commands.add(command_id)
        self.cancelled = True

    @workflow.signal
    def wake(self, command_id: str) -> None:
        if command_id in self.commands:
            return
        self.commands.add(command_id)
        self.woken = True

    @workflow.signal
    def approval(self, command_id: str, approval_id: UUID) -> None:
        if command_id in self.commands:
            return
        self.commands.add(command_id)
        self.decisions.add(approval_id)

    async def prepare(self, reference: RunReference) -> PreparedRun | None:
        while not self.cancelled:
            self.woken = False
            claim = cast(
                RunClaim,
                await workflow.execute_activity(
                    "orchestration.prepare",
                    reference,
                    result_type=RunClaim,
                    start_to_close_timeout=timedelta(seconds=60),
                ),
            )
            if claim.prepared is not None:
                return claim.prepared
            await workflow.wait_condition(lambda: self.cancelled or self.woken)
        return None

    @workflow.run
    async def run(self, reference: RunReference) -> None:
        completion = RunCompletion(run_id=reference.run_id, status="failed")
        try:
            prepared = await self.prepare(reference)
            if prepared is None:
                completion = RunCompletion(run_id=reference.run_id, status="cancelled")
            else:
                task = asyncio.create_task(self.execute(prepared))
                cancellation = asyncio.create_task(workflow.wait_condition(lambda: self.cancelled))
                done, _ = await workflow.wait(
                    [task, cancellation], return_when=asyncio.FIRST_COMPLETED
                )
                if cancellation in done:
                    task.cancel()
                    with suppress(BaseException):
                        await task
                    completion = RunCompletion(run_id=reference.run_id, status="cancelled")
                else:
                    cancellation.cancel()
                    completion = await task
        except asyncio.CancelledError:
            completion = RunCompletion(run_id=reference.run_id, status="cancelled")
        except Exception as exc:
            frames = []
            traceback = exc.__traceback__
            while traceback is not None:
                frame = traceback.tb_frame.f_code
                frames.append(f"{frame.co_filename}:{traceback.tb_lineno}:{frame.co_name}")
                traceback = traceback.tb_next
            workflow.logger.error(
                "Agent execution failed",
                extra={"error_type": type(exc).__name__, "failure_frames": frames},
            )
            completion = RunCompletion(
                run_id=reference.run_id, status="failed", error_code="execution_failed"
            )
        finally:
            await asyncio.shield(self.finish(completion))

    async def finish(self, completion: RunCompletion) -> None:
        # Runner cancellation can return while a public FunctionTool callback is
        # still awaiting WAIT_CANCELLATION_COMPLETED. Drain those invocations before
        # finalizing receipts, capacity and Session ownership.
        while self.pending_tools:
            await asyncio.gather(*self.pending_tools, return_exceptions=True)
        if self.has_hooks:
            if completion.status != "succeeded":
                with suppress(Exception):
                    await workflow_hooks(
                        HookOccurrence(
                            run_id=completion.run_id,
                            occurrence="run",
                            stage="on_error",
                            payload={"error_code": completion.error_code or completion.status},
                            entered=self.hook_entered,
                        )
                    )
            try:
                await workflow_hooks(
                    HookOccurrence(
                        run_id=completion.run_id,
                        occurrence="run",
                        stage="finalize",
                        payload={"status": completion.status},
                        entered=self.hook_entered,
                    )
                )
            except Exception:
                if completion.status == "succeeded":
                    completion = completion.model_copy(
                        update={
                            "status": "failed",
                            "output": None,
                            "error_code": "required_finalize_failed",
                        }
                    )
        await workflow.execute_activity(
            "orchestration.finish",
            completion,
            result_type=str,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=0),
        )

    async def execute(self, prepared: PreparedRun) -> RunCompletion:
        self.has_hooks = bool(prepared.request.hooks)
        if self.has_hooks:
            context = await workflow_hooks(
                HookOccurrence(
                    run_id=prepared.reference.run_id,
                    occurrence="run",
                    stage="context.prepare",
                    payload={"context_blocks": []},
                )
            )
            self.hook_entered = list(context["entered"])
            blocks = context["payload"].get("context_blocks", [])
            if not isinstance(blocks, list) or len(blocks) > 64:
                raise ValueError("Invalid extension context block bound")
            extra = []
            for block in blocks:
                if not isinstance(block, dict) or set(block) != {"text", "source"}:
                    raise ValueError("Context blocks require text and a source")
                if not isinstance(block["text"], str) or not isinstance(block["source"], str):
                    raise ValueError("Invalid context block")
                if len(block["text"].encode()) > 16384 or len(block["source"].encode()) > 512:
                    raise ValueError("Extension context block exceeds its byte bound")
                extra.append(
                    {
                        "role": "user",
                        "content": "Untrusted extension context from "
                        + block["source"]
                        + ":\n"
                        + block["text"],
                    }
                )
            if extra:
                initial = prepared.request.input
                items = (
                    [{"role": "user", "content": initial}] if isinstance(initial, str) else initial
                )
                prepared = prepared.model_copy(
                    update={
                        "request": prepared.request.model_copy(update={"input": [*items, *extra]})
                    }
                )
        remaining = float(prepared.request.limits.execution_timeout_seconds)
        if prepared.request.assets:
            start = workflow.now()
            try:
                await asyncio.wait_for(
                    workflow.execute_activity(
                        "orchestration.assets",
                        prepared.reference,
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
            remaining -= (workflow.now() - start).total_seconds()
        runner = AgentRunner(prepared, self.pending_tools)
        current_input: Any = prepared.request.input
        checkpoint_number = 0
        while True:
            if remaining <= 0:
                return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
            start = workflow.now()
            try:
                result = await asyncio.wait_for(
                    runner.run(current_input),
                    timeout=remaining,
                )
            except TimeoutError:
                return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
            remaining -= (workflow.now() - start).total_seconds()
            if not result.interruptions:
                artifacts = []
                if prepared.request.outputs:
                    if remaining <= 0:
                        return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
                    try:
                        artifacts = await asyncio.wait_for(
                            workflow.execute_activity(
                                "orchestration.outputs",
                                prepared.reference,
                                result_type=list[UUID],
                                start_to_close_timeout=timedelta(minutes=10),
                                heartbeat_timeout=timedelta(seconds=10),
                                retry_policy=RetryPolicy(maximum_attempts=1),
                                cancellation_type=(
                                    workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
                                ),
                            ),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
                return RunCompletion(
                    run_id=prepared.reference.run_id,
                    status="succeeded",
                    output=result.final_output,
                    artifacts=artifacts,
                )
            checkpoint_number += 1
            state = result.to_state()
            approval_id = await workflow.execute_activity(
                "orchestration.checkpoint",
                ApprovalCheckpoint(
                    run_id=prepared.reference.run_id,
                    checkpoint_key=str(checkpoint_number),
                    state=state.to_string(),
                    interruptions=[
                        {
                            "name": getattr(item.raw_item, "name", "tool"),
                            "call_id": getattr(item.raw_item, "call_id", ""),
                        }
                        for item in result.interruptions
                    ],
                ),
                result_type=UUID,
                start_to_close_timeout=timedelta(seconds=60),
            )

            def approval_received(target: UUID = approval_id) -> bool:
                return target in self.decisions

            try:
                await workflow.wait_condition(
                    approval_received,
                    timeout=timedelta(seconds=prepared.request.limits.approval_timeout_seconds),
                )
            except TimeoutError:
                return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
            decision = await workflow.execute_activity(
                "orchestration.approval",
                args=[prepared.reference.run_id, approval_id],
                result_type=dict[str, Any],
                start_to_close_timeout=timedelta(seconds=60),
            )
            await self.prepare(prepared.reference)
            for interruption in state.get_interruptions():
                if decision["approved"]:
                    state.approve(interruption)
                else:
                    state.reject(interruption)
            current_input = state
            if remaining <= 0:
                return RunCompletion(run_id=prepared.reference.run_id, status="timed_out")
