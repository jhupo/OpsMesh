"""Native SDK execution is coordinated durably by the official Temporal plugin."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporalio.contrib.workflow_streams import WorkflowStream
    from temporalio.exceptions import (
        ActivityError,
        ApplicationError,
        CancelledError,
        is_cancelled_exception,
    )
    from temporalio.openai_agents.workflow import temporal_sandbox_client

    from opsmesh.agents.execution.contracts import (
        AgentLifecycleEvent,
        AgentModelUsageRecord,
        AgentResumeState,
        AgentRunRequest,
        AgentRunResult,
        AgentSessionScope,
        AgentStreamEvent,
    )
    from opsmesh.agents.execution.errors import AgentExecutionPolicyError, normalize_agent_error
    from opsmesh.agents.execution.providers.openai.activity_policy import (
        AGENT_ACTIVITY_RETRY_POLICY,
    )
    from opsmesh.agents.execution.providers.openai.contracts import AgentExecutionDefinition
    from opsmesh.agents.execution.providers.openai.executor import OpenAIAgentExecutor
    from opsmesh.agents.execution.providers.openai.sandbox_activities import AgentSandboxBinding
    from opsmesh.agents.execution.providers.openai.stream_events import (
        AGENT_EVENT_TOPIC,
        AgentStreamRelayRequest,
        allows_live_text,
    )
    from opsmesh.agents.execution.providers.openai.tools_temporal import TemporalToolExecutor
    from opsmesh.agents.sessions.temporal import TemporalSession
    from opsmesh.runtime.temporal.contracts import (
        ExecutionKind,
        ExecutionRequest,
        WorkflowSignal,
    )


class AgentExecutionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ExecutionRequest
    definition: AgentExecutionDefinition
    result: AgentRunResult


class AgentLifecycleNotification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ExecutionRequest
    event_id: UUID
    event: AgentLifecycleEvent


class AgentApprovalResume(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ExecutionRequest
    approval_id: UUID
    state: AgentResumeState


class AgentExecutionFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ExecutionRequest
    code: str
    message: str
    event_type: str = "agent.execution.failed"
    metadata: dict[str, object]
    retryable: bool = False
    definition: AgentExecutionDefinition | None = None
    model_usages: tuple[AgentModelUsageRecord, ...] = ()

    @classmethod
    def from_exception(
        cls,
        request: ExecutionRequest,
        exception: Exception,
        *,
        definition: AgentExecutionDefinition | None = None,
        model_usages: tuple[AgentModelUsageRecord, ...] = (),
    ) -> AgentExecutionFailure:
        cause: BaseException = exception
        while cause.__cause__ is not None:
            cause = cause.__cause__
        normalized = normalize_agent_error(exception)
        metadata: dict[str, object] = {"error_type": type(cause).__name__}
        if isinstance(exception, AgentExecutionPolicyError):
            metadata.update(exception.metadata)
            event_type = exception.event_type
        else:
            event_type = "agent.execution.failed"
            # ApplicationError contains the safe provider diagnostic generated
            # at the model Activity boundary. Do not serialize traceback/client.
            from temporalio.exceptions import ApplicationError

            if isinstance(cause, ApplicationError):
                normalized = normalize_agent_error(RuntimeError(cause.message))
                metadata["activity_error_type"] = cause.type
                for detail in cause.details:
                    if isinstance(detail, dict):
                        metadata.update(detail)
        return cls(
            request=request,
            code=exception.code
            if isinstance(exception, AgentExecutionPolicyError)
            else (getattr(cause, "type", None) or normalized.code),
            message=normalized.message,
            event_type=event_type,
            metadata=metadata,
            retryable=normalized.retryable,
            definition=definition,
            model_usages=model_usages,
        )


@workflow.defn(name="opsmesh.agent")
class AgentWorkflow:
    @workflow.init
    def __init__(self, request: ExecutionRequest) -> None:
        self._stream = WorkflowStream()
        self._stream_offset = 0
        self._approvals: list[UUID] = []
        self._seen_approvals: set[UUID] = set()
        self._lifecycle_offset = 0
        self._sandbox_binding: AgentSandboxBinding | None = None
        self._sandbox_checkpointed = True
        self._sandbox_tasks: dict[asyncio.Task[object], None] = {}

    @workflow.signal(name="approval")
    def approval(self, signal: WorkflowSignal) -> None:
        if signal.resource_id not in self._seen_approvals:
            self._seen_approvals.add(signal.resource_id)
            self._approvals.append(signal.resource_id)

    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.AGENT:
            raise ApplicationError(
                "AgentWorkflow only accepts Agent execution references",
                type="InvalidExecutionKind",
                non_retryable=True,
            )
        runner = OpenAIAgentExecutor()
        definition = None
        try:
            definition = await self._prepare(request)
            while definition is not None:
                relay = (
                    workflow.start_activity(
                        "opsmesh.agent.relay_stream",
                        AgentStreamRelayRequest(
                            request=request,
                            from_offset=self._stream_offset,
                            allow_live_text=allows_live_text(definition),
                        ),
                        start_to_close_timeout=timedelta(hours=8),
                        retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                        heartbeat_timeout=timedelta(seconds=30),
                        result_type=int,
                        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    )
                    if definition.stream
                    else None
                )
                try:
                    sdk_request = await self._sdk_request(request, definition)
                    self._sandbox_checkpointed = False
                    result = await runner.run(
                        sdk_request,
                        session=TemporalSession(definition.session)
                        if definition.session is not None
                        else None,
                    )
                    self._sandbox_checkpointed = True
                    if relay is not None:
                        self._stream_offset = await relay
                except BaseException:
                    if relay is not None:
                        relay.cancel()
                        # A cancelled relay must not replace the original SDK
                        # failure with its Activity cancellation wrapper.
                        with suppress(asyncio.CancelledError, CancelledError, ActivityError):
                            await relay
                    raise
                await workflow.execute_activity(
                    "opsmesh.agent.complete",
                    AgentExecutionOutcome(request=request, definition=definition, result=result),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                )
                if result.resume_state is None or not result.interruptions:
                    return
                await workflow.wait_condition(lambda: bool(self._approvals))
                approval_id = self._approvals.pop(0)
                definition = cast(
                    AgentExecutionDefinition | None,
                    await workflow.execute_activity(
                        "opsmesh.agent.resume",
                        AgentApprovalResume(
                            request=request,
                            approval_id=approval_id,
                            state=result.resume_state,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                        result_type=AgentExecutionDefinition,
                    ),
                )
        except asyncio.CancelledError:
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.agent.cancel",
                    request,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                )
            )
            raise
        except Exception as exc:
            cause: BaseException | None = exc
            while cause is not None:
                if is_cancelled_exception(cause):
                    await asyncio.shield(
                        workflow.execute_activity(
                            "opsmesh.agent.cancel",
                            request,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                        )
                    )
                    raise CancelledError("Agent execution was cancelled") from exc
                cause = cause.__cause__
            failure = AgentExecutionFailure.from_exception(
                request, exc, definition=definition, model_usages=runner.model_usages
            )
            await workflow.execute_activity(
                "opsmesh.agent.fail",
                failure,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
            )
            raise ApplicationError(failure.message, type=failure.code, non_retryable=True) from exc
        finally:
            # Native cache eviction can close this coroutine outside the Workflow loop.
            # That is not product cancellation and cannot schedule cleanup Activities.
            if workflow.in_workflow():
                # The SDK cancels function callbacks without awaiting them on parent
                # cancellation. Await our native callbacks so their Temporal Activity
                # cancellation and side-effect receipts finish before Workflow close.
                if self._sandbox_tasks:

                    async def drain() -> None:
                        tasks = list(self._sandbox_tasks)
                        for task in tasks:
                            if not task.cancelling():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)

                    await asyncio.shield(drain())
                if self._sandbox_binding is not None and not self._sandbox_checkpointed:
                    await asyncio.shield(self._checkpoint_sandbox())
                self._stream.detach_pollers()
                await workflow.wait_condition(workflow.all_handlers_finished)
                await asyncio.shield(
                    workflow.execute_activity(
                        "opsmesh.execution.release",
                        request,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    )
                )

    @staticmethod
    async def _prepare(request: ExecutionRequest) -> AgentExecutionDefinition | None:
        return cast(
            AgentExecutionDefinition | None,
            await workflow.execute_activity(
                "opsmesh.agent.prepare",
                request,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
                result_type=AgentExecutionDefinition,
            ),
        )

    async def _sdk_request(
        self, request: ExecutionRequest, definition: AgentExecutionDefinition
    ) -> AgentRunRequest:
        async def lifecycle(event: AgentLifecycleEvent) -> None:
            event_id = uuid5(
                NAMESPACE_URL, f"{request.workflow_id}:lifecycle:{self._lifecycle_offset}"
            )
            self._lifecycle_offset += 1
            await workflow.execute_activity(
                "opsmesh.agent.lifecycle",
                AgentLifecycleNotification(request=request, event_id=event_id, event=event),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
            )

        def stream_event(event: AgentStreamEvent) -> None:
            self._stream.topic(AGENT_EVENT_TOPIC).publish(event)

        sdk_request = AgentRunRequest(
            agent_profile=definition.profile,
            context=definition.context,
            input_text=definition.input_text,
            max_turns=definition.max_turns,
            provider=definition.provider,
            model=definition.model_reference.model,
            model_api=definition.model_reference.model_api,
            model_provider_credential_id=definition.model_reference.credential_id,
            session=AgentSessionScope(
                workspace_id=definition.session.workspace_id,
                session_id=definition.session.session_id,
            )
            if definition.session is not None
            else None,
            tool_executor=TemporalToolExecutor(),
            handoffs=tuple(item.to_target() for item in definition.handoffs),
            agent_tools=tuple(item.to_target() for item in definition.agent_tools),
            require_handoff=definition.require_handoff,
            tracing=definition.tracing,
            resume_state=definition.resume_state,
            approval_decisions=definition.approval_decisions,
            output_schema=definition.output_schema,
            guardrails=definition.guardrails,
            attachments=definition.attachments,
            lifecycle_sink=lifecycle,
            event_sink=stream_event,
            stream=definition.stream,
        )
        if definition.sandbox_backend is not None:
            self._sandbox_binding = await workflow.execute_activity(
                "opsmesh.agent.sandbox",
                request,
                result_type=AgentSandboxBinding,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            client = temporal_sandbox_client(
                definition.sandbox_backend,
                config={
                    "start_to_close_timeout": timedelta(hours=1),
                    "heartbeat_timeout": timedelta(seconds=30),
                    "retry_policy": RetryPolicy(maximum_attempts=1),
                    "cancellation_type": (
                        workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
                    ),
                },
            )
            sdk_request = replace(
                sdk_request,
                sdk_sandbox_client=client,
                sdk_sandbox_state=self._sandbox_binding.state,
                sdk_sandbox_tasks=self._sandbox_tasks,
            )
        return sdk_request

    async def _checkpoint_sandbox(self) -> None:
        if self._sandbox_binding is None:
            return
        await workflow.execute_activity(
            "opsmesh.agent.sandbox_checkpoint",
            self._sandbox_binding,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        self._sandbox_checkpointed = True


WORKFLOWS = (AgentWorkflow,)
