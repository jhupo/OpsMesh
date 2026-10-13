"""Temporal Activities bridging product authorization and native SDK execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.agents.execution.contracts import (
    AgentExecutionContext,
    AgentRunRequest,
    AgentRunResult,
    AgentSessionScope,
    ToolExecutionResult,
)
from opsmesh.agents.execution.errors import AgentExecutionCancelledError, AgentExecutionPolicyError
from opsmesh.agents.execution.providers.openai.contracts import AgentExecutionDefinition
from opsmesh.agents.execution.providers.openai.stream_events import AgentStreamActivities
from opsmesh.agents.execution.providers.openai.tools_temporal import AgentToolOperation
from opsmesh.agents.execution.providers.openai.workflow import (
    AgentApprovalResume,
    AgentExecutionFailure,
    AgentExecutionOutcome,
    AgentLifecycleNotification,
)
from opsmesh.agents.execution.tools.cancellation import record_tool_cancellation
from opsmesh.agents.execution.tools.executor import AgentToolExecutionService
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.providers.policy import credential_is_selectable
from opsmesh.governance.costs.service import CostAccountingService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.runs.authorization.scopes import agent_execution_scope
from opsmesh.orchestration.runs.authorization.validation import RunAuthorizationService
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import uuid_or_none

if TYPE_CHECKING:
    from opsmesh.orchestration.runs.execution import RunExecutionService


class AgentExecutionActivities:
    """Keep credentials, ORM sessions, and product writes inside Activities."""

    def __init__(self, sessions: Callable[[], Session], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings

    def _service(self, session: Session) -> RunExecutionService:
        from opsmesh.orchestration.runs.execution import (
            RunExecutionService,
        )
        from opsmesh.orchestration.runs.service import RunOrchestrationService

        orchestration = RunOrchestrationService(session)
        return RunExecutionService(
            session=session,
            lifecycle=orchestration._run_lifecycle(),
            settings=self._settings,
        )

    def _run(self, session: Session, request: ExecutionRequest) -> AgentRun:
        from opsmesh.orchestration.runs.tree_lock import lock_run_execution_tree

        lock_run_execution_tree(session, request.workspace_id, request.resource_id)
        run = session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == request.workspace_id,
                AgentRun.id == request.resource_id,
            )
            .with_for_update()
        )
        if run is None:
            raise ValueError("Agent run does not belong to the execution workspace")
        return run

    def _tool_executor(self, session: Session) -> AgentToolExecutionService:
        return AgentToolExecutionService(
            session,
            settings=self._settings,
            secret_service=SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
                previous_secrets=self._settings.credential_encryption_previous_secrets,
            ),
        )

    @activity.defn(name="opsmesh.agent.prepare")
    async def prepare(self, request: ExecutionRequest) -> AgentExecutionDefinition | None:
        if request.kind != ExecutionKind.AGENT:
            raise ValueError("Agent preparation requires an Agent execution")

        def operation() -> AgentExecutionDefinition | None:
            with self._sessions() as session:
                run = self._run(session, request)
                service = self._service(session)
                job = request
                if run.status in {"queued", "completed", "failed", "cancelled"}:
                    built = service.prepare_run(run, job)
                else:
                    from opsmesh.orchestration.requests.builder import RunRequestBuilder

                    built = RunRequestBuilder(
                        session,
                        self._settings,
                    ).build_agent_request(run, job)
                session.commit()
                return AgentExecutionDefinition.from_request(built) if built else None

        return await asyncio.to_thread(operation)

    @activity.defn(name="opsmesh.agent.complete")
    async def complete(self, outcome: AgentExecutionOutcome) -> None:
        def operation() -> None:
            with self._sessions() as session:
                request = outcome.request
                run = self._run(session, request)
                service = self._service(session)
                if run.status in {"completed", "failed", "cancelled"}:
                    return
                try:
                    ExecutionIdentityService(session).for_run(run.workspace_id, run.id)
                except ResourceAccessDeniedError as exc:
                    service.reject_authorization(run, exc)
                    return
                job = request
                execution = _request_from_definition(outcome.definition, self._settings)
                accounting = CostAccountingService(session)
                accounting.record_attempt(
                    run=run,
                    request=execution,
                    result=outcome.result,
                    job_attempt=1,
                    request_sequence=1,
                    attempt_outcome="succeeded",
                    budget_decision=accounting.evaluate_budget(
                        run.workspace_id,
                        provider=execution.provider or "openai",
                        model=execution.model or execution.agent_profile.model,
                    ),
                )
                RunEventRecorder(session).append_model_provider_used_event(run, execution)
                service.persist_model_result(run, execution, job, outcome.result)

        await asyncio.to_thread(operation)

    @activity.defn(name="opsmesh.agent.resume")
    async def resume(self, approval: AgentApprovalResume) -> AgentExecutionDefinition | None:
        def operation() -> AgentExecutionDefinition | None:
            with self._sessions() as session:
                run = self._run(session, approval.request)
                if run.status in {"completed", "failed", "cancelled"}:
                    return None
                from opsmesh.orchestration.approvals.models import PendingToolInvocation
                from opsmesh.orchestration.runs.state import RunStatus, transition_run

                invocation = session.scalar(
                    select(PendingToolInvocation).where(
                        PendingToolInvocation.workspace_id == run.workspace_id,
                        PendingToolInvocation.agent_run_id == run.id,
                        PendingToolInvocation.approval_id == approval.approval_id,
                        PendingToolInvocation.status.in_({"approved", "rejected"}),
                    )
                )
                if invocation is None:
                    raise ValueError("Approval does not belong to the paused run")
                job = approval.request
                from opsmesh.orchestration.requests.builder import RunRequestBuilder

                built = RunRequestBuilder(session, self._settings).build_agent_request(run, job)
                definition = AgentExecutionDefinition.from_request(
                    replace(built, resume_state=approval.state)
                )
                transition_run(run, RunStatus.RUNNING)
                if run.task_id is not None:
                    from opsmesh.orchestration.tasks.models import Task
                    from opsmesh.orchestration.tasks.state import (
                        TaskStatus,
                        transition_task,
                    )

                    task = session.scalar(
                        select(Task)
                        .where(Task.workspace_id == run.workspace_id, Task.id == run.task_id)
                        .with_for_update()
                    )
                    if task is not None and task.status == TaskStatus.WAITING_APPROVAL.value:
                        transition_task(task, TaskStatus.RUNNING)
                session.commit()
                return definition

        return await asyncio.to_thread(operation)

    @activity.defn(name="opsmesh.agent.lifecycle")
    async def lifecycle(self, notification: AgentLifecycleNotification) -> None:
        def operation() -> None:
            with self._sessions() as session:
                run = self._run(session, notification.request)
                from opsmesh.orchestration.runs.lifecycle_messages import (
                    RunLifecycleMessageWriter,
                )

                RunLifecycleMessageWriter(session).map_event(
                    run, notification.event, event_id=notification.event_id
                )
                session.commit()

        await asyncio.to_thread(operation)

    @activity.defn(name="opsmesh.agent.cancel")
    async def cancel(self, request: ExecutionRequest) -> None:
        def operation() -> None:
            with self._sessions() as session:
                run = self._run(session, request)
                self._service(session).complete_cancellation(run, provider="openai_agents")

        await asyncio.to_thread(operation)

    @activity.defn(name="opsmesh.agent.fail")
    async def fail(self, failure: AgentExecutionFailure) -> None:
        def operation() -> None:
            with self._sessions() as session:
                run = self._run(session, failure.request)
                service = self._service(session)
                if run.status in {"completed", "failed", "cancelled"}:
                    return
                if failure.definition is not None and failure.model_usages:
                    execution = _request_from_definition(failure.definition, self._settings)
                    accounting = CostAccountingService(session)
                    accounting.record_attempt(
                        run=run,
                        request=execution,
                        result=AgentRunResult("", model_usages=failure.model_usages),
                        job_attempt=1,
                        request_sequence=1,
                        attempt_outcome="failed",
                        budget_decision=accounting.evaluate_budget(
                            run.workspace_id,
                            provider=execution.provider or "openai",
                            model=execution.model or execution.agent_profile.model,
                        ),
                    )
                service.lifecycle.mark_run_failed(
                    run,
                    AgentExecutionPolicyError(
                        code=failure.code,
                        message=failure.message,
                        event_type=failure.event_type,
                        metadata=failure.metadata,
                        retryable=failure.retryable,
                    ),
                )
                service.commit_and_refresh(run)

        await asyncio.to_thread(operation)

    @activity.defn(name="opsmesh.agent.authorize")
    async def authorize(self, operation: AgentToolOperation) -> None:
        def execute() -> None:
            with self._sessions() as session:
                context = operation.context
                run = self._run(session, _request_from_context(context))
                if run.status != "running":
                    raise AgentExecutionCancelledError
                identity = ExecutionIdentityService(session).for_run(
                    context.workspace_id, context.run_id
                )
                if context.user_id != identity.user_id:
                    raise ResourceAccessDeniedError()
                profile_id = uuid_or_none(context.metadata.get("agent_profile_id"))
                if (
                    profile_id is not None
                    and session.scalar(
                        select(AgentProfile.id).where(
                            AgentProfile.workspace_id == context.workspace_id,
                            AgentProfile.id == profile_id,
                            AgentProfile.status == "active",
                            AgentProfile.archived_at.is_(None),
                        )
                    )
                    is None
                ):
                    raise ValueError("Execution agent is no longer active")
                fingerprint = context.metadata.get("capability_catalog_fingerprint")
                scope = agent_execution_scope(
                    authorization_snapshot_for_run(run),
                    profile_id,
                    fingerprint if isinstance(fingerprint, str) else None,
                )
                RunAuthorizationService(session).validate_capability_scope(
                    context.workspace_id, scope
                )
                # Authored tool/MCP nodes have no model request or provider budget.
                # Derive this from the frozen product run, never a caller flag.
                from opsmesh.orchestration.runs.temporal import run_execution_reference

                if run_execution_reference(session, run).kind == ExecutionKind.AGENT:
                    provider = context.metadata.get("model_provider_provider")
                    model = context.metadata.get("run_model")
                    if not isinstance(provider, str) or not isinstance(model, str):
                        raise ValueError("Execution model identity is missing")
                    CostAccountingService(session).assert_budget_available(
                        context.workspace_id, provider=provider, model=model
                    )
                    credential_id = uuid_or_none(
                        context.metadata.get("model_provider_credential_id")
                    )
                    if credential_id is not None:
                        credential = session.scalar(
                            select(ModelProviderCredential).where(
                                ModelProviderCredential.workspace_id == context.workspace_id,
                                ModelProviderCredential.id == credential_id,
                            )
                        )
                        if not credential_is_selectable(credential):
                            raise ValueError("Execution model credential is no longer selectable")

        await asyncio.to_thread(execute)

    @activity.defn(name="opsmesh.agent.review_tool")
    async def review_tool(self, operation: AgentToolOperation) -> dict[str, object]:
        if operation.tool_name is None:
            raise ValueError("Tool review requires a named tool")

        def execute() -> dict[str, object]:
            with self._sessions() as session:
                context = operation.context
                result = self._tool_executor(session).review_tool_call(
                    context=context,
                    tool_name=operation.tool_name or "",
                    arguments=operation.arguments,
                )
                session.commit()
                return result

        return await asyncio.to_thread(execute)

    @activity.defn(name="opsmesh.agent.execute_tool")
    async def execute_tool(self, operation: AgentToolOperation) -> ToolExecutionResult:
        if operation.tool_name is None:
            raise ValueError("Tool execution requires a named tool")
        await self.authorize(operation)

        def execute() -> ToolExecutionResult:
            with self._sessions() as session:
                run = self._run(session, _request_from_context(operation.context))
                if run.status != "running":
                    raise AgentExecutionCancelledError
                ExecutionIdentityService(session).for_run(
                    operation.context.workspace_id, operation.context.run_id
                )
                invocation = None
                pending = None
                if operation.approval_granted:
                    from opsmesh.orchestration.approvals.pending_tools import (
                        PendingToolInvocationService,
                    )

                    if operation.tool_call_id is None:
                        raise ValueError("Approved execution requires its native SDK call ID")
                    pending = PendingToolInvocationService(
                        session,
                        SecretEncryptionService(
                            secret=self._settings.credential_encryption_secret,
                            key_id=self._settings.credential_encryption_key_id,
                            previous_secrets=self._settings.credential_encryption_previous_secrets,
                        ),
                    )
                    invocation, cached = pending.claim_execution(
                        workspace_id=operation.context.workspace_id,
                        run_id=operation.context.run_id,
                        tool_call_id=operation.tool_call_id,
                        tool_name=operation.tool_name or "",
                        arguments=operation.arguments,
                    )
                    if cached is not None:
                        return TypeAdapter(ToolExecutionResult).validate_python(cached)
                result = self._tool_executor(session).execute_tool(
                    context=operation.context,
                    tool_name=operation.tool_name or "",
                    arguments=operation.arguments,
                    tool_call_id=operation.tool_call_id,
                    approval_granted=operation.approval_granted,
                )
                if invocation is not None and pending is not None:
                    serialized = TypeAdapter(ToolExecutionResult).dump_python(result, mode="json")
                    pending.complete_execution(invocation, serialized)
                session.commit()
                return result

        pending = asyncio.create_task(asyncio.to_thread(execute))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Wait for the product transaction to settle before acknowledging
            # cancellation; a Python thread cannot be interrupted safely.
            try:
                await pending
            finally:
                raise

    @activity.defn(name="opsmesh.agent.cancel_tools")
    async def cancel_tools(self, operation: AgentToolOperation) -> None:
        def execute() -> None:
            with self._sessions() as session:
                record_tool_cancellation(session, operation.context)
                session.commit()

        await asyncio.to_thread(execute)


def build_agent_activity_objects(
    sessions: Callable[[], Session],
    settings: Settings,
) -> tuple[AgentExecutionActivities, AgentStreamActivities]:
    """Build the explicit activity object used by the Temporal Worker."""
    return (
        AgentExecutionActivities(sessions, settings),
        AgentStreamActivities(sessions),
    )


def _request_from_context(context: AgentExecutionContext) -> ExecutionRequest:
    return ExecutionRequest(
        workspace_id=context.workspace_id,
        resource_id=context.run_id,
        kind=ExecutionKind.AGENT,
    )


def _request_from_definition(
    definition: AgentExecutionDefinition,
    settings: Settings,
) -> AgentRunRequest:
    return AgentRunRequest(
        agent_profile=definition.profile,
        input_text=definition.input_text,
        context=definition.context,
        max_turns=definition.max_turns,
        model=definition.model_reference.model,
        provider=definition.provider,
        model_api=definition.model_reference.model_api,
        model_provider_credential_id=definition.model_reference.credential_id,
        session=(
            AgentSessionScope(
                session_id=definition.session.session_id,
                workspace_id=definition.session.workspace_id,
            )
            if definition.session is not None
            else None
        ),
        resume_state=definition.resume_state,
        approval_decisions=definition.approval_decisions,
        tracing=definition.tracing,
        handoffs=tuple(item.to_target() for item in definition.handoffs),
        agent_tools=tuple(item.to_target() for item in definition.agent_tools),
        require_handoff=definition.require_handoff,
        output_schema=definition.output_schema,
        guardrails=definition.guardrails,
        attachments=definition.attachments,
        stream=definition.stream,
    )
