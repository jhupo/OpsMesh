from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from agents.sandbox import Manifest
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.orchestration.approvals.policy import ApprovalPolicyEngine
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.statuses import ACTIVE_RUN_STATUS_VALUES
from opsmesh.orchestration.runs.temporal import run_execution_reference
from opsmesh.runtime.commands.approvals import command_approved
from opsmesh.runtime.instances.events import new_runtime_event
from opsmesh.runtime.instances.models import RuntimeCommand, WorkspaceRuntime
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_cancellation

_COMMAND_TIMEOUT_SECONDS = 1800
_OUTPUT_BYTES = 1_048_576


@dataclass(frozen=True)
class _Command:
    runtime_id: UUID
    command: tuple[str, ...]


@dataclass(frozen=True)
class _CommandResult:
    status: str
    error: str | None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


class SandboxControlActivities:
    """Authorize durable intent without retaining database sessions across SDK waits."""

    def __init__(self, services: ActivityDependencies, backends: SandboxBackendRegistry) -> None:
        self._services = services
        self._sandbox = SandboxService(services.transaction, backends, services.secrets())

    @activity.defn(name="opsmesh.sandbox.control")
    async def control(self, request: ExecutionRequest) -> None:
        if (
            request.kind != ExecutionKind.SANDBOX_CONTROL
            or request.parameters.get("action") != "execute"
        ):
            raise ValueError("Sandbox control requires a command execution reference")
        preparation = asyncio.create_task(asyncio.to_thread(self._prepare_command, request))
        try:
            command = await asyncio.shield(preparation)
        except asyncio.CancelledError:
            command = await preparation
            if command is not None:
                await asyncio.to_thread(
                    self._finish_command,
                    request,
                    _CommandResult("cancelled", "runtime_command_cancelled"),
                )
            raise
        except Exception:
            await asyncio.to_thread(self._reject_command, request)
            raise
        if command is None:
            return
        reference_id: UUID | None = None
        result = _CommandResult("failed", "runtime_command_execution_failed")
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            reference = await self._sandbox.create(
                workspace_id=request.workspace_id,
                runtime_id=command.runtime_id,
                owner_kind="command",
                owner_id=request.resource_id,
                manifest=Manifest(root="/workspace"),
            )
            reference_id = reference.id
            native = await self._sandbox.resume(request.workspace_id, reference.id)
            output = await native.exec(
                *command.command, shell=False, timeout=_COMMAND_TIMEOUT_SECONDS
            )
            result = _CommandResult(
                "succeeded" if output.ok() else "failed",
                None if output.ok() else "runtime_command_nonzero_exit",
                output.exit_code,
                _bounded_text(output.stdout.decode("utf-8", errors="replace"), _OUTPUT_BYTES),
                _bounded_text(output.stderr.decode("utf-8", errors="replace"), _OUTPUT_BYTES),
            )
        except asyncio.CancelledError:
            result = _CommandResult("cancelled", "runtime_command_cancelled")
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            try:
                if reference_id is not None:
                    await asyncio.shield(self._sandbox.delete(request.workspace_id, reference_id))
            except BaseException:
                result = _CommandResult(
                    "failed",
                    "runtime_command_cleanup_failed",
                    result.exit_code,
                    result.stdout,
                    result.stderr,
                )
                await asyncio.shield(asyncio.to_thread(self._finish_command, request, result))
                raise
            await asyncio.shield(asyncio.to_thread(self._finish_command, request, result))

    def _prepare_command(self, request: ExecutionRequest) -> _Command | None:
        with self._services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.MANAGE_RUNTIME)
            record = session.scalar(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == request.workspace_id,
                    RuntimeCommand.id == request.resource_id,
                )
                .with_for_update()
            )
            if record is None:
                raise ApplicationError(
                    "Sandbox command is unavailable",
                    type="SandboxCommandUnavailable",
                    non_retryable=True,
                )
            if record.status in {"succeeded", "failed", "cancelled", "blocked"}:
                return None
            if record.status != "queued":
                raise ApplicationError(
                    "Sandbox command outcome requires reconciliation",
                    type="SandboxCommandOutcomeUnknown",
                    non_retryable=True,
                )
            if record.requested_by_user_id != request.requested_by_user_id:
                raise ApplicationError(
                    "Sandbox command actor differs from the persisted request",
                    type="SandboxCommandActorMismatch",
                    non_retryable=True,
                )
            if record.requested_by_user_id is None:
                raise ApplicationError(
                    "Sandbox command actor is unavailable",
                    type="SandboxCommandActorMissing",
                    non_retryable=True,
                )
            user = AuthenticationService(session).authenticate_user(record.requested_by_user_id)
            ResourceAuthorizationService(session, user).require(
                request.workspace_id,
                ResourceKind.RUNTIME,
                record.workspace_runtime_id,
                ResourceAction.INVOKE,
            )
            runtime = session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == request.workspace_id,
                    WorkspaceRuntime.id == record.workspace_runtime_id,
                    WorkspaceRuntime.status == "active",
                )
            )
            if runtime is None:
                raise ApplicationError(
                    "Sandbox execution environment is unavailable",
                    type="SandboxEnvironmentUnavailable",
                    non_retryable=True,
                )
            decision = ApprovalPolicyEngine(
                session, self._services.settings
            ).evaluate_runtime_command(
                workspace_id=request.workspace_id,
                command=record.command,
                context={
                    "runtime_id": str(record.workspace_runtime_id),
                    "source": "runtime_control",
                },
            )
            if decision.blocked or (decision.required and not command_approved(session, record)):
                record.status = "blocked"
                record.error = "runtime_command_denied_at_execution"
                record.completed_at = datetime.now(UTC)
                return None
            record.status = "running"
            record.started_at = datetime.now(UTC)
            return _Command(record.workspace_runtime_id, tuple(record.command))

    def _finish_command(self, request: ExecutionRequest, result: _CommandResult) -> None:
        with self._services.transaction() as session:
            record = session.scalar(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == request.workspace_id,
                    RuntimeCommand.id == request.resource_id,
                )
                .with_for_update()
            )
            if record is None:
                raise ValueError("Executed sandbox command disappeared")
            record.status, record.error, record.exit_code = (
                result.status,
                result.error,
                result.exit_code,
            )
            record.stdout, record.stderr = result.stdout, result.stderr
            record.completed_at = datetime.now(UTC)
            session.add(
                new_runtime_event(
                    workspace_id=request.workspace_id,
                    workspace_runtime_id=record.workspace_runtime_id,
                    runtime_space_id=record.runtime_space_id,
                    event_type=f"runtime.command.{result.status}",
                    message="Sandbox command completed",
                    metadata={"command_id": str(record.id), "exit_code": record.exit_code},
                    created_at=record.completed_at,
                )
            )

    def _reject_command(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            record = session.scalar(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == request.workspace_id,
                    RuntimeCommand.id == request.resource_id,
                )
                .with_for_update()
            )
            if (
                record is None
                or record.status != "queued"
                or record.requested_by_user_id != request.requested_by_user_id
            ):
                return
            record.status = "blocked"
            record.error = "runtime_command_admission_denied"
            record.completed_at = datetime.now(UTC)

    @activity.defn(name="opsmesh.sandbox.cleanup")
    async def cleanup(self, request: ExecutionRequest) -> None:
        if request.kind != ExecutionKind.SANDBOX_CLEANUP:
            raise ValueError("Sandbox cleanup requires an environment reference")
        await asyncio.to_thread(self._cancel_executions, request)
        # Execution-owned finally blocks release their resources before environment cleanup.
        while await asyncio.to_thread(self._executions_active, request):
            activity.heartbeat()
            await asyncio.sleep(2)
        for reference_id in await asyncio.to_thread(self._references, request):
            activity.heartbeat(str(reference_id))
            await self._sandbox.delete(request.workspace_id, reference_id)

    def _cancel_executions(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            runtime = session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == request.workspace_id,
                    WorkspaceRuntime.id == request.resource_id,
                )
            )
            if runtime is None or runtime.status not in {"disabled", "deleted"}:
                raise ApplicationError(
                    "Environment does not require cleanup",
                    type="SandboxCleanupNotAuthorized",
                    non_retryable=True,
                )
            for run in session.scalars(
                select(AgentRun).where(
                    AgentRun.workspace_id == request.workspace_id,
                    AgentRun.runtime_id == runtime.id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
                )
            ):
                stage_cancellation(
                    session,
                    dispatch_id=uuid4(),
                    workspace_id=request.workspace_id,
                    workflow_id=run_execution_reference(session, run).workflow_id,
                )
            for command in session.scalars(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == request.workspace_id,
                    RuntimeCommand.workspace_runtime_id == runtime.id,
                    RuntimeCommand.status.in_({"queued", "running", "waiting_approval"}),
                )
                .with_for_update()
            ):
                stage_cancellation(
                    session,
                    dispatch_id=uuid4(),
                    workspace_id=request.workspace_id,
                    workflow_id=ExecutionRequest(
                        execution_id=command.id,
                        workspace_id=request.workspace_id,
                        resource_id=command.id,
                        kind=ExecutionKind.SANDBOX_CONTROL,
                        requested_by_user_id=command.requested_by_user_id,
                        parameters={"action": "execute"},
                    ).workflow_id,
                )
                if command.status != "running":
                    command.status = "cancelled"
                    command.error = "runtime_environment_disabled"
                    command.completed_at = datetime.now(UTC)

    def _executions_active(self, request: ExecutionRequest) -> bool:
        with self._services.transaction() as session:
            return (
                session.scalar(
                    select(RuntimeCommand.id)
                    .where(
                        RuntimeCommand.workspace_id == request.workspace_id,
                        RuntimeCommand.workspace_runtime_id == request.resource_id,
                        RuntimeCommand.status == "running",
                    )
                    .limit(1)
                )
                is not None
                or session.scalar(
                    select(AgentRun.id)
                    .where(
                        AgentRun.workspace_id == request.workspace_id,
                        AgentRun.runtime_id == request.resource_id,
                        AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
                    )
                    .limit(1)
                )
                is not None
            )

    def _references(self, request: ExecutionRequest) -> list[UUID]:
        with self._services.transaction() as session:
            return list(
                session.scalars(
                    select(SandboxReference.id).where(
                        SandboxReference.workspace_id == request.workspace_id,
                        SandboxReference.workspace_runtime_id == request.resource_id,
                        SandboxReference.status != "deleted",
                    )
                )
            )


def _bounded_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


async def _heartbeat() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(5)
