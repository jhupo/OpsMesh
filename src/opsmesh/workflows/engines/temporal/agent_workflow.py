"""Durable execution of one persistent instance using the official Agents plugin."""

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import asyncio
    from datetime import timedelta
    from typing import Any
    from uuid import UUID

    from agents import RunState
    from agents.sandbox import SandboxRunConfig
    from pydantic import BaseModel, ConfigDict
    from temporalio.openai_agents.workflow import temporal_sandbox_client

    from opsmesh.execution.context import ExecutionContext, serialize_execution_context
    from opsmesh.execution.contracts import PreparedAgent
    from opsmesh.execution.runner import run_agent
    from opsmesh.execution.runtime.sandbox import AgentSandboxState
    from opsmesh.workflows.engines.temporal.agent_bindings import (
        ACTIVITY_OPTIONS,
        ResourceDescriptions,
        workflow_bindings,
    )
    from opsmesh.workflows.engines.temporal.session import TemporalSession


class InstanceAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    context: ExecutionContext
    prepared: PreparedAgent
    input: object
    resources: ResourceDescriptions
    state: dict[str, Any] | None = None
    decision: bool | None = None
    timeout_seconds: int


@workflow.defn
class AgentInstanceWorkflow:
    def __init__(self) -> None:
        self.decisions: set[str] = set()

    @workflow.signal
    async def approval_decided(self, dispatch_id: str, approval_id: str) -> None:
        if dispatch_id != approval_id:
            raise ValueError("The approval signal differs from its delivery identity")
        self.decisions.add(approval_id)

    async def wait_for_decision(self, approval_id: str) -> None:
        await workflow.wait_condition(lambda: approval_id in self.decisions)
        self.decisions.discard(approval_id)

    async def admit(
        self, command: dict[str, str], *, timeout_seconds: int | None = None
    ) -> InstanceAdmission | None:
        delay, attempts = 1, 0
        deadline = (
            workflow.now() + timedelta(seconds=timeout_seconds)
            if timeout_seconds is not None
            else None
        )
        while True:
            value = await workflow.execute_activity(
                "execution.instance_claim",
                command,
                result_type=dict[str, Any],
                **ACTIVITY_OPTIONS,
            )
            if value.get("cancelled"):
                return None
            if not value.get("waiting"):
                return InstanceAdmission.model_validate(value)
            attempts += 1
            if deadline is not None:
                remaining = (deadline - workflow.now()).total_seconds()
                if remaining <= 0:
                    raise TimeoutError("Agent admission timed out")
                await workflow.sleep(min(delay, remaining))
            else:
                if attempts >= 128 or workflow.info().is_continue_as_new_suggested():
                    workflow.continue_as_new(command)
                await workflow.sleep(delay)
            delay = min(delay * 2, 60)

    @workflow.run
    async def run(self, command: dict[str, str]) -> None:
        try:
            await self.execute(command)
        except workflow.ContinueAsNewError:
            raise
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                workflow.logger.exception("Agent Workflow failed before completion")
            await asyncio.shield(
                workflow.execute_activity(
                    "execution.instance_abandon",
                    {
                        "run_id": command["execution_id"],
                        "status": "cancelled"
                        if isinstance(error, asyncio.CancelledError)
                        else "failed",
                    },
                    **ACTIVITY_OPTIONS,
                )
            )
            if isinstance(error, asyncio.CancelledError):
                raise

    async def execute(self, command: dict[str, str]) -> None:
        run_id = UUID(command["execution_id"])
        if workflow.info().workflow_id != f"instance:{run_id}":
            raise ValueError("The Workflow identity does not match its dispatch")
        admission = await self.admit(command)
        if admission is None:
            return
        prepared = admission.prepared
        sandbox = None
        if prepared.environment_id is not None:
            sandbox = SandboxRunConfig(
                client=temporal_sandbox_client("opsmesh.instance", ACTIVITY_OPTIONS),
                session_state=AgentSandboxState(
                    session_id=run_id,
                    environment_id=prepared.environment_id,
                ),
                cwd=".",
            )
        state: RunState[Any] | None = None
        decision: bool | None = None
        completion: dict[str, Any] = {
            "run_id": str(run_id),
            "attempt_id": admission.context.attempt_id,
        }
        try:
            while True:
                outcome = await asyncio.wait_for(
                    run_agent(
                        prepared,
                        admission.input,
                        context=admission.context,
                        bindings=workflow_bindings(admission.context, admission.resources),
                        session=TemporalSession(
                            prepared.identity,
                            prepared.session,
                            activity_name="execution.instance_history",
                        )
                        if prepared.session
                        else None,
                        sandbox=sandbox,
                        state=state,
                        decision=decision,
                    ),
                    timeout=admission.timeout_seconds,
                )
                completion.update(
                    status=outcome.status,
                    output=outcome.output,
                    native_state=outcome.state.to_json(
                        context_serializer=serialize_execution_context
                    )
                    if outcome.state
                    else None,
                    pending=outcome.pending,
                )
                await workflow.execute_activity(
                    "execution.instance_finish",
                    completion,
                    **ACTIVITY_OPTIONS,
                )
                if outcome.status == "completed":
                    return
                # Keep the live SDK state (including official model proxies).
                # Worker recovery reconstructs it by replaying recorded Activities.
                state = outcome.state
                while True:
                    poll = await workflow.execute_activity(
                        "execution.instance_decision",
                        str(run_id),
                        result_type=dict[str, Any],
                        **ACTIVITY_OPTIONS,
                    )
                    if poll["status"] in {"cancelled", "failed", "needs_attention"}:
                        return
                    if poll["status"] == "queued" and poll["decision"] is not None:
                        self.decisions.discard(str(poll["approval_id"]))
                        break
                    approval_id = str(poll["approval_id"])
                    await self.wait_for_decision(approval_id)
                resumed = await self.admit(command, timeout_seconds=admission.timeout_seconds)
                if resumed is None:
                    return
                if resumed.context != admission.context:
                    raise ValueError("Approval cannot change the Workflow execution identity")
                if resumed.resources.callbacks != admission.resources.callbacks:
                    raise ValueError("Callback registrations changed after approval")
                decision = resumed.decision
                admission = resumed
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                workflow.logger.exception("Agent execution failed")
            completion.update(
                status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                error_code="execution_failed",
                native_state=None,
                pending=None,
            )
            await asyncio.shield(
                workflow.execute_activity(
                    "execution.instance_finish",
                    completion,
                    **ACTIVITY_OPTIONS,
                )
            )
            if isinstance(error, asyncio.CancelledError):
                raise
