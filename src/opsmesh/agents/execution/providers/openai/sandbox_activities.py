"""Product admission and persisted references for the SDK's native sandbox session."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid5

from agents.sandbox import Manifest
from agents.sandbox.session import SandboxSessionState
from agents.sandbox.snapshot import RemoteSnapshotSpec
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.agents.execution.contracts import AgentExecutionContext
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.sessions.models import AgentSessionMetadata
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.approvals.pending_tools import PendingToolInvocationService
from opsmesh.orchestration.approvals.policy import ApprovalPolicyEngine
from opsmesh.orchestration.runs.authorization.scopes import agent_execution_scope
from opsmesh.orchestration.runs.authorization.validation import (
    RunAuthorizationService,
    authorized_profile_for_run,
)
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.registry import SandboxBackendRegistry, native_resource_identity
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.runtime.sandboxes.snapshots import SNAPSHOT_DEPENDENCY
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import uuid_or_none

SANDBOX_TOOL_NAMES = frozenset({"exec_command", "write_stdin", "apply_patch", "view_image"})


class AgentSandboxBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: UUID
    run_id: UUID
    reference_id: UUID
    owner_id: UUID
    user_id: UUID
    backend_id: str
    state: dict[str, Any]


class AgentSandboxToolOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: AgentExecutionContext
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    tool_call_id: str
    approval_granted: bool = False
    result: object | None = None
    failed: bool = False


class AgentSandboxActivities:
    def __init__(
        self,
        sessions: Callable[[], Session],
        settings: Settings,
        backends: SandboxBackendRegistry,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._backends = backends
        self._secrets = SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )
        self._sandbox = SandboxService(self._transaction, backends, self._secrets)

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self._sessions() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def _authorized_run(self, session: Session, workspace_id: UUID, run_id: UUID) -> AgentRun:
        run = session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id == run_id,
                AgentRun.status == "running",
            )
        )
        if run is None:
            raise ResourceAccessDeniedError()
        ExecutionIdentityService(session).for_run(workspace_id, run_id)
        task = session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == run.task_id)
        )
        RunAuthorizationService(session).validate_authorization_snapshot(
            run, task, authorized_profile_for_run(session, run), authorization_snapshot_for_run(run)
        )
        if run.runtime_id is None or run.session_key is None:
            raise ResourceAccessDeniedError()
        return run

    def _owner(self, session: Session, run: AgentRun) -> tuple[UUID, UUID]:
        identity = ExecutionIdentityService(session).for_run(run.workspace_id, run.id)
        persistent = session.scalar(
            select(AgentSessionMetadata).where(
                AgentSessionMetadata.workspace_id == run.workspace_id,
                AgentSessionMetadata.session_key == run.session_key,
                AgentSessionMetadata.status == "active",
            )
        )
        if persistent is None or run.runtime_id is None:
            raise ResourceAccessDeniedError()
        owner = self._scope_owner(run, persistent, identity.user_id)
        return owner, identity.user_id

    @staticmethod
    def _scope_owner(run: AgentRun, persistent: AgentSessionMetadata, user_id: UUID) -> UUID:
        assert run.runtime_id is not None
        # Conversation turns are serialized by their Workflow. Team transcripts
        # can span independent tasks; task steps execute concurrently. Their native
        # environments need the stable task/step scope, excluding agent and run IDs.
        if run.task_step_id is None and persistent.scope_type in {
            "conversation_agent",
            "automation_agent",
        }:
            scope = f"{persistent.scope_type}:{persistent.scope_id.rsplit(':', 1)[0]}"
        else:
            scope = f"task:{run.task_id}:step:{run.task_step_id or 'root'}"
        return uuid5(run.runtime_id, f"{user_id}:{scope}")

    async def checkpoint_native(self, workflow_id: str, state: SandboxSessionState) -> None:
        def binding() -> AgentSandboxBinding:
            parts = workflow_id.split(":")
            if len(parts) != 4 or parts[0] != "opsmesh" or parts[2] != "agent":
                raise ResourceAccessDeniedError()
            workspace_id, run_id = UUID(parts[1]), UUID(parts[3])
            with self._sessions() as session:
                run = session.scalar(
                    select(AgentRun).where(
                        AgentRun.workspace_id == workspace_id,
                        AgentRun.id == run_id,
                    )
                )
                if run is None:
                    raise ResourceAccessDeniedError()
                persistent = session.scalar(
                    select(AgentSessionMetadata).where(
                        AgentSessionMetadata.workspace_id == workspace_id,
                        AgentSessionMetadata.session_key == run.session_key,
                    )
                )
                task = session.scalar(
                    select(Task).where(
                        Task.workspace_id == workspace_id,
                        Task.id == run.task_id,
                    )
                )
                if persistent is None or task is None or run.runtime_id is None:
                    raise ResourceAccessDeniedError()
                user = uuid_or_none((task.execution_identity or {}).get("user_id"))
                if user is None:
                    raise ResourceAccessDeniedError()
                owner = self._scope_owner(run, persistent, user)
                reference = session.scalar(
                    select(SandboxReference).where(
                        SandboxReference.workspace_id == workspace_id,
                        SandboxReference.workspace_runtime_id == run.runtime_id,
                        SandboxReference.owner_kind == "environment",
                        SandboxReference.owner_id == owner,
                        SandboxReference.scope_id == user,
                        SandboxReference.native_session_id == str(state.session_id),
                        SandboxReference.backend_id == state.type,
                    )
                )
                if reference is None:
                    raise ResourceAccessDeniedError()
                return AgentSandboxBinding(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    reference_id=reference.id,
                    owner_id=owner,
                    user_id=user,
                    backend_id=reference.backend_id,
                    state=state.model_dump(mode="json"),
                )

        await self.checkpoint(await asyncio.to_thread(binding))

    @activity.defn(name="opsmesh.agent.sandbox")
    async def prepare(self, request: ExecutionRequest) -> AgentSandboxBinding:
        def authorize() -> tuple[UUID, UUID, UUID]:
            with self._sessions() as session:
                run = self._authorized_run(session, request.workspace_id, request.resource_id)
                owner, user = self._owner(session, run)
                assert run.runtime_id is not None
                return run.runtime_id, owner, user

        runtime_id, owner, user = await asyncio.to_thread(authorize)
        reference = await self._sandbox.create(
            workspace_id=request.workspace_id,
            runtime_id=runtime_id,
            owner_kind="environment",
            owner_id=owner,
            scope_id=user,
            manifest=Manifest(root="/workspace"),
            snapshot=RemoteSnapshotSpec(client_dependency_key=SNAPSHOT_DEPENDENCY),
        )
        native = await self._sandbox.resume(request.workspace_id, reference.id)
        await asyncio.to_thread(authorize)
        return AgentSandboxBinding(
            workspace_id=request.workspace_id,
            run_id=request.resource_id,
            reference_id=reference.id,
            owner_id=owner,
            user_id=user,
            backend_id=reference.backend_id,
            state=self._backends.require(reference.backend_id).client.serialize_session_state(
                native.state
            ),
        )

    def authorize_native(self, workflow_id: str, state: SandboxSessionState) -> SandboxSessionState:
        parts = workflow_id.split(":")
        if len(parts) != 4 or parts[0] != "opsmesh" or parts[2] != "agent":
            raise ResourceAccessDeniedError()
        workspace_id, run_id = UUID(parts[1]), UUID(parts[3])
        with self._sessions() as session:
            run = self._authorized_run(session, workspace_id, run_id)
            owner, user = self._owner(session, run)
            reference = session.scalar(
                select(SandboxReference).where(
                    SandboxReference.workspace_id == workspace_id,
                    SandboxReference.workspace_runtime_id == run.runtime_id,
                    SandboxReference.owner_kind == "environment",
                    SandboxReference.owner_id == owner,
                    SandboxReference.scope_id == user,
                    SandboxReference.native_session_id == str(getattr(state, "session_id", "")),
                    SandboxReference.backend_id == getattr(state, "type", None),
                    SandboxReference.status.in_({"running", "stopped"}),
                )
            )
            if reference is None:
                raise ResourceAccessDeniedError()
            _, authoritative = self._sandbox._native_state(self._sandbox._reference(reference))
            # SDK RunState can contain an older resource ID after a native
            # replacement. Resume the current product resource while retaining
            # the SDK's capability manifest for this agent.
            return authoritative.model_copy(update={"manifest": state.manifest})

    def record_native_resume(
        self, workflow_id: str, original: SandboxSessionState, resumed: SandboxSessionState
    ) -> None:
        parts = workflow_id.split(":")
        workspace_id, run_id = UUID(parts[1]), UUID(parts[3])
        with self._sessions() as session:
            run = self._authorized_run(session, workspace_id, run_id)
            owner, user = self._owner(session, run)
            reference = session.scalar(
                select(SandboxReference).where(
                    SandboxReference.workspace_id == workspace_id,
                    SandboxReference.workspace_runtime_id == run.runtime_id,
                    SandboxReference.owner_kind == "environment",
                    SandboxReference.owner_id == owner,
                    SandboxReference.scope_id == user,
                    SandboxReference.native_session_id == str(original.session_id),
                    SandboxReference.backend_id == original.type,
                )
            )
            if reference is None or resumed.type != original.type:
                raise ResourceAccessDeniedError()
            reference_id = reference.id
        self._sandbox.save_session_state(workspace_id, reference_id, resumed)

    async def release_replacement(self, state: SandboxSessionState) -> None:
        client = self._backends.require(state.type).client
        native = await client.resume(state)
        await client.delete(native)

    def _authorize_tool(self, session: Session, operation: AgentSandboxToolOperation) -> None:
        run = self._authorized_run(
            session, operation.context.workspace_id, operation.context.run_id
        )
        identity = ExecutionIdentityService(session).for_run(run.workspace_id, run.id)
        if (
            operation.context.user_id != identity.user_id
            or operation.tool_name not in SANDBOX_TOOL_NAMES
        ):
            raise ResourceAccessDeniedError()
        profile_id = uuid_or_none(operation.context.metadata.get("agent_profile_id"))
        if (
            profile_id is None
            or session.scalar(
                select(AgentProfile.id).where(
                    AgentProfile.workspace_id == run.workspace_id,
                    AgentProfile.id == profile_id,
                    AgentProfile.status == "active",
                    AgentProfile.archived_at.is_(None),
                )
            )
            is None
        ):
            raise ResourceAccessDeniedError()
        fingerprint = operation.context.metadata.get("capability_catalog_fingerprint")
        RunAuthorizationService(session).validate_capability_scope(
            run.workspace_id,
            agent_execution_scope(
                authorization_snapshot_for_run(run),
                profile_id,
                fingerprint if isinstance(fingerprint, str) else None,
            ),
        )

    def _review(self, session: Session, operation: AgentSandboxToolOperation) -> dict[str, object]:
        self._authorize_tool(session, operation)
        if operation.tool_name == "exec_command":
            value = operation.arguments.get("cmd")
            if not isinstance(value, str) or not value:
                raise ValueError("Native command is missing")
            command = ["sh", "-lc", value]
        else:
            # Stdin and patches can also mutate user files or trigger external writes.
            command = [operation.tool_name, str(operation.arguments)]
        return (
            ApprovalPolicyEngine(session, self._settings)
            .evaluate_runtime_command(
                workspace_id=operation.context.workspace_id,
                command=command,
                context={
                    "agent_run_id": str(operation.context.run_id),
                    "tool_name": operation.tool_name,
                },
            )
            .approval_payload()
        )

    @activity.defn(name="opsmesh.agent.sandbox_review")
    async def review(self, operation: AgentSandboxToolOperation) -> dict[str, object]:
        def execute() -> dict[str, object]:
            with self._transaction() as session:
                return self._review(session, operation)

        return await asyncio.to_thread(execute)

    @activity.defn(name="opsmesh.agent.sandbox_admit")
    async def admit(self, operation: AgentSandboxToolOperation) -> dict[str, object] | None:
        def execute() -> dict[str, object] | None:
            with self._transaction() as session:
                decision = self._review(session, operation).get("decision")
                if decision == "deny" or (
                    decision == "require_approval" and not operation.approval_granted
                ):
                    raise PolicyDeniedError("Native sandbox tool has no current approval")
                if operation.approval_granted:
                    _, cached = PendingToolInvocationService(
                        session, self._secrets
                    ).claim_execution(
                        workspace_id=operation.context.workspace_id,
                        run_id=operation.context.run_id,
                        tool_call_id=operation.tool_call_id,
                        tool_name=operation.tool_name,
                        arguments=operation.arguments,
                    )
                    if cached is not None:
                        return cached
                run = session.get(AgentRun, operation.context.run_id)
                assert run is not None
                RunEventRecorder(session).append_event(
                    run,
                    "sandbox.tool.admitted",
                    "Native sandbox tool admitted",
                    {"tool_name": operation.tool_name, "tool_call_id": operation.tool_call_id},
                )
                return None

        return await asyncio.to_thread(execute)

    @activity.defn(name="opsmesh.agent.sandbox_finish")
    async def finish(self, operation: AgentSandboxToolOperation) -> None:
        def execute() -> None:
            with self._transaction() as session:
                pending = PendingToolInvocationService(session, self._secrets)
                invocation = pending.by_run_call(
                    workspace_id=operation.context.workspace_id,
                    run_id=operation.context.run_id,
                    tool_call_id=operation.tool_call_id,
                )
                if invocation is not None and invocation.status == "executing":
                    pending.complete_execution(
                        invocation,
                        {
                            "status": "failed" if operation.failed else "completed",
                            "output": operation.result,
                        },
                    )

        await asyncio.to_thread(execute)

    @activity.defn(name="opsmesh.agent.sandbox_checkpoint")
    async def checkpoint(self, binding: AgentSandboxBinding) -> None:
        def load() -> SandboxSessionState | None:
            with self._sessions() as session:
                run = session.scalar(
                    select(AgentRun).where(
                        AgentRun.workspace_id == binding.workspace_id,
                        AgentRun.id == binding.run_id,
                    )
                )
                reference = session.scalar(
                    select(SandboxReference).where(
                        SandboxReference.workspace_id == binding.workspace_id,
                        SandboxReference.id == binding.reference_id,
                        SandboxReference.workspace_runtime_id == (run.runtime_id if run else None),
                        SandboxReference.backend_id == binding.backend_id,
                        SandboxReference.owner_kind == "environment",
                        SandboxReference.owner_id == binding.owner_id,
                        SandboxReference.scope_id == binding.user_id,
                    )
                )
                if reference is None:
                    raise ResourceAccessDeniedError()
                if reference.status in {"deleted", "deleting"}:
                    return None
                if reference.status not in {"running", "stopped"}:
                    raise ResourceAccessDeniedError()
                state = self._backends.require(binding.backend_id).client.deserialize_session_state(
                    binding.state
                )
                if str(state.session_id) != reference.native_session_id:
                    raise ResourceAccessDeniedError()
                _, authoritative = self._sandbox._native_state(self._sandbox._reference(reference))
                return authoritative.model_copy(update={"manifest": state.manifest})

        state = await asyncio.to_thread(load)
        if state is None:
            return
        client = self._backends.require(binding.backend_id).client
        original_resource = native_resource_identity(state)
        native = await client.resume(state)
        try:
            await native.stop()
            await asyncio.to_thread(
                self._sandbox._save_state,
                binding.workspace_id,
                binding.reference_id,
                native,
                "stopped",
            )
        except BaseException:
            # A concurrent Runtime deletion can invalidate the checkpoint while
            # native resume replaces a missing container. Release that replacement.
            if native_resource_identity(native.state) != original_resource:
                await asyncio.shield(client.delete(native))
            raise
