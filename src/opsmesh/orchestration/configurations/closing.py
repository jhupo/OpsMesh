"""Composition-owned state and explicit execution operations."""

import asyncio
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.configurations.close_workflow import ConfigurationCloseWork
from opsmesh.orchestration.configurations.models import ConfigurationClosure, UseConfiguration
from opsmesh.orchestration.configurations.service import authorize_configuration
from opsmesh.orchestration.contracts import (
    CleanupScopeAdmission,
    ExecutionScope,
    ExecutionSubject,
    ScopeCleanupAuthorization,
)
from opsmesh.orchestration.definitions.contracts import TERMINAL_FLOW_STATUSES
from opsmesh.orchestration.runs.models import FlowRun, Run
from opsmesh.orchestration.runs.schemas import CancelRequest
from opsmesh.orchestration.runs.service import cancel, record_command
from opsmesh.orchestration.runtime import mcp_service
from opsmesh.orchestration.runtime.models import (
    Environment,
    EnvironmentOperationReceipt,
    MCPConnection,
    MCPOperationReceipt,
)
from opsmesh.orchestration.runtime.service import stage_operation
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution


class ConfigurationCleanup(Protocol):
    def drain(
        self,
        session: Session,
        *,
        configuration_id: UUID,
        operation_id: UUID,
    ) -> tuple[int, int]:
        """Cancel only owned execution/runtime effects; return pending and uncertain counts."""
        ...


def close_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    expected_revision: int,
) -> ConfigurationClosure:
    configuration = authorize_configuration(
        session,
        actor=actor,
        configuration_id=configuration_id,
        permission="manage",
        lock=True,
        allow_disabled=True,
    )
    existing = session.scalar(
        select(ConfigurationClosure).where(
            ConfigurationClosure.configuration_id == configuration_id
        )
    )
    if existing:
        return existing
    if configuration.revision != expected_revision:
        raise ConflictError("The configuration revision changed")
    configuration.status = "draining"
    configuration.revision += 1
    record = ConfigurationClosure(configuration_id=configuration_id, requested_by_id=actor.user_id)
    session.add(record)
    session.flush()
    stage_execution(
        session,
        ConfigurationCloseWork(
            execution_id=record.id,
            workflow_id=f"configuration-close:{record.id}",
            closure_id=record.id,
        ),
    )
    return record


class ConfigurationActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        cleanup: ConfigurationCleanup,
    ) -> None:
        self.sessions, self.cipher, self.cleanup = sessions, cipher, cleanup

    @activity.defn(name="configurations.close")
    async def close(self, identifier: str) -> dict[str, object]:
        return await asyncio.to_thread(self.pass_once, UUID(identifier))

    def pass_once(self, identifier: UUID) -> dict[str, object]:
        with self.sessions() as session, session.begin():
            prior = session.get(ConfigurationClosure, identifier)
            if prior is None:
                raise NotFoundError("The configuration closure is unavailable")
            configuration = session.get(
                UseConfiguration,
                prior.configuration_id,
                with_for_update=True,
                populate_existing=True,
            )
            record = session.get(ConfigurationClosure, identifier, with_for_update=True)
            if configuration is None or record is None:
                raise NotFoundError("The configuration closure is unavailable")
            if record.status == "completed":
                return {"status": record.status, **record.detail}
            pending = uncertain = 0
            for run in session.scalars(
                select(FlowRun).where(
                    FlowRun.configuration_id == configuration.id,
                    FlowRun.status.not_in(TERMINAL_FLOW_STATUSES - {"needs_attention"}),
                )
            ):
                if run.status == "needs_attention":
                    uncertain += 1
                    continue
                # A configuration closure is explicit authority to stop use, not to read
                # another participant's history or impersonate that participant.
                run.control_actor_id = run.principal_id
                command_id = uuid5(NAMESPACE_URL, f"configuration-close:{record.id}:{run.id}")
                record_command(
                    session,
                    run=run,
                    command_id=command_id,
                    kind="cancel",
                    payload={"action": "cancel", "closure_id": str(record.id)},
                    expected_revision=None,
                    cipher=self.cipher,
                )
                pending += 1
            child_pending, child_uncertain = self.cleanup.drain(
                session, configuration_id=configuration.id, operation_id=record.id
            )
            pending += child_pending
            uncertain += child_uncertain
            record.detail = {"pending": pending, "uncertain": uncertain}
            record.status = (
                "needs_attention" if uncertain else "draining" if pending else "completed"
            )
            if record.status == "completed":
                configuration.status = "closed"
            return {"status": record.status, **record.detail}


def drain_configuration(
    session: Session,
    *,
    configuration_id: UUID,
    cleanup_id: UUID,
    authorization: ScopeCleanupAuthorization,
    cipher: SecretCipher,
) -> tuple[int, int]:
    admission = CleanupScopeAdmission(authorization, cleanup_id)
    scope = ExecutionScope(configuration_id=configuration_id)
    owner_ref = f"configuration:{configuration_id}"
    pending = uncertain = 0
    for run in session.scalars(
        select(Run)
        .where(
            Run.scope_id == configuration_id,
            Run.owner_ref == owner_ref,
            Run.status.not_in(("succeeded", "failed", "cancelled", "timed_out")),
        )
        .order_by(Run.id)
    ):
        if run.status == "needs_attention":
            uncertain += 1
            continue
        if run.status == "cancelling":
            pending += 1
            continue
        subject = ExecutionSubject(run.principal_id)
        admission.admit(session, subject=subject, scope=scope)
        cancel(
            session,
            subject=subject,
            admission=admission,
            run_id=run.id,
            cipher=cipher,
            request=CancelRequest(
                command_id=uuid5(cleanup_id, str(run.id)), expected_revision=run.event_sequence
            ),
        )
        pending += 1
    active_runs = pending + uncertain
    for item in session.scalars(
        select(MCPConnection)
        .where(
            MCPConnection.scope_id == configuration_id,
            MCPConnection.owner_ref == owner_ref,
            MCPConnection.status != "closed",
        )
        .order_by(MCPConnection.id)
        .with_for_update()
    ):
        admission.admit(session, subject=ExecutionSubject(item.principal_id), scope=scope)
        receipts = list(
            session.scalars(
                select(MCPOperationReceipt).where(
                    MCPOperationReceipt.connection_id == item.id,
                    MCPOperationReceipt.status.in_(("pending", "started", "uncertain")),
                )
            )
        )
        if any(receipt.status == "uncertain" for receipt in receipts):
            uncertain += 1
        elif receipts or active_runs:
            pending += 1
        else:
            mcp_service.stage(
                session,
                item=item,
                operation_id=f"close:{cleanup_id}:{item.id}",
                action="close",
                cleanup_ref=cleanup_id,
            )
            pending += 1
    for environment in session.scalars(
        select(Environment)
        .where(
            Environment.scope_id == configuration_id,
            Environment.owner_ref == owner_ref,
            Environment.status != "closed",
        )
        .order_by(Environment.id)
        .with_for_update()
    ):
        subject = ExecutionSubject(environment.principal_id)
        admission.admit(session, subject=subject, scope=scope)
        receipts = list(
            session.scalars(
                select(EnvironmentOperationReceipt).where(
                    EnvironmentOperationReceipt.environment_id == environment.id,
                    EnvironmentOperationReceipt.status.in_(("pending", "started", "uncertain")),
                )
            )
        )
        if environment.status == "needs_attention" or any(
            r.status == "uncertain" for r in receipts
        ):
            uncertain += 1
        elif active_runs or receipts:
            pending += 1
        else:
            stage_operation(
                session,
                record=environment,
                subject=subject,
                operation_id=f"close:{cleanup_id}:{environment.id}",
                action="close",
                arguments={},
                fingerprint=payload_digest({"action": "close"}),
                cleanup_ref=cleanup_id,
            )
            pending += 1
    return pending, uncertain
