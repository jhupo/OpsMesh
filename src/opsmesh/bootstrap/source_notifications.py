"""Bind authoritative source records to the sole orchestration event consumer."""

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session, object_session

from opsmesh.bootstrap.orchestration import source_flow
from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    load_installation,
)
from opsmesh.extensions.models import ExtensionEvent, Invocation
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.access import authorize_scope
from opsmesh.orchestration.runs.models import FlowRun, Run, RunEvent
from opsmesh.orchestration.triggers.contracts import SourceEvent
from opsmesh.orchestration.triggers.schemas import SourceEventWork
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.approvals import approvals_for
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.dispatch.contracts import (
    DispatchOperation,
    DispatchRequest,
    WorkflowStartOptions,
)
from opsmesh.temporal.dispatch.outbox import persist_dispatch


class AuthoritativeEvents:
    def __init__(self, cipher: SecretCipher, scopes: ScopeAuthorization) -> None:
        self.cipher, self.scopes = cipher, scopes

    def read(
        self, session: Session, *, actor: CurrentUser, reference: SourceEventWork
    ) -> SourceEvent:
        if reference.source_kind == "extension":
            source = session.get(ExtensionEvent, reference.source_id)
            invocation = session.get(Invocation, source.invocation_id) if source else None
            if source is None or invocation is None or invocation.principal_id != actor.user_id:
                raise ForbiddenError("The extension event source is unavailable")
            instance = authorize_instance(
                session, invocation.instance_id, user=actor, scopes=self.scopes
            )
            installation = load_installation(session, invocation.installation_id)
            authorize_installation(session, installation, user=actor)
            if (
                instance.status != "enabled"
                or installation.status != "enabled"
                or instance.revision != invocation.instance_revision
                or "host.events.publish"
                not in set(instance.permissions) & set(installation.permission_ceiling)
            ):
                raise ForbiddenError("The event source authorization was revoked")
            parent = source_flow(session, invocation.id, self.cipher)
            return SourceEvent(
                source.event_type,
                dict(source.payload),
                instance.configuration_id,
                instance.id,
                parent.id if parent else None,
            )
        if reference.source_kind == "run":
            progress = session.get(RunEvent, reference.source_id)
            run = session.get(Run, progress.run_id) if progress else None
            if progress is None or run is None:
                raise ForbiddenError("The execution event source is unavailable")
            configuration_id = None if run.owner_ref.startswith("user:") else run.scope_id
            authorize_scope(
                session,
                actor=actor,
                owner_id=run.principal_id,
                configuration_id=configuration_id,
            )
            parent = source_flow(session, run.id, self.cipher)
            return SourceEvent(
                f"execution.run.{progress.kind}",
                {"run_id": str(run.id), "sequence": progress.sequence, "kind": progress.kind},
                configuration_id,
                parent_flow_id=parent.id if parent else None,
            )
        occurrence = session.get(AuditEvent, reference.source_id)
        if occurrence is None or (
            occurrence.actor_id != str(actor.user_id) and not actor.has_platform_role("admin")
        ):
            raise ForbiddenError("The platform event source is unavailable")
        if occurrence.configuration_id is not None:
            authorize_scope(
                session,
                actor=actor,
                owner_id=actor.user_id,
                configuration_id=occurrence.configuration_id,
            )
        return SourceEvent(
            occurrence.action,
            {
                "event_id": str(occurrence.id),
                "category": occurrence.category,
                "schema_version": occurrence.schema_version,
                "severity": occurrence.severity,
                "occurred_at": occurrence.occurred_at.isoformat(),
                "summary": occurrence.summary,
                "details": dict(occurrence.details),
            },
            occurrence.configuration_id,
            parent_flow_id=self.platform_parent(session, occurrence),
        )

    def platform_parent(self, session: Session, occurrence: AuditEvent) -> UUID | None:
        """Preserve root/depth limits when an execution produces another source event."""
        if occurrence.invocation_id:
            parent = source_flow(session, occurrence.invocation_id, self.cipher)
            return parent.id if parent else None
        if occurrence.source != "platform":
            return None
        reference: UUID | str | None = None
        if occurrence.target_type == "approval_request":
            request = approvals_for(session).get(session, UUID(occurrence.target_id))
            if request and request.target_type in {"flow_plan", "flow_step", "orchestration.run"}:
                reference = UUID(request.target_id.split(":", 1)[0])
                if request.target_type in {"flow_plan", "flow_step"}:
                    parent = session.get(FlowRun, reference)
                    return parent.id if parent else None
        if isinstance(reference, str):
            try:
                reference = UUID(reference)
            except ValueError:
                return None
        parent = source_flow(session, reference, self.cipher) if reference else None
        return parent.id if parent else None


def publish_source(mapper: Mapper[Any], connection: Connection, target: Any) -> None:
    session = object_session(target)
    if session is None:
        return
    cipher = session.info.get("dispatch_cipher")
    if not isinstance(cipher, SecretCipher):
        raise RuntimeError("Durable source events require the application dispatch cipher")
    kind = (
        "extension"
        if isinstance(target, ExtensionEvent)
        else "run"
        if isinstance(target, RunEvent)
        else "platform"
    )
    version = ""
    identifier = uuid5(NAMESPACE_URL, f"source:{kind}:{target.id}:{version}")
    work = SourceEventWork(
        execution_id=identifier,
        workflow_id=f"source-event:{identifier}",
        source_kind=kind,
        source_id=target.id,
        source_version=version,
    )
    persist_dispatch(
        connection,
        DispatchRequest(
            dispatch_id=identifier,
            workflow_id=work.workflow_id,
            operation=DispatchOperation.START,
            workflow_name=work.workflow_name,
            start_options=WorkflowStartOptions(),
            execution=work.model_dump(mode="json"),
        ),
        cipher=cipher,
    )


def register_source_events() -> None:
    for model in (ExtensionEvent, RunEvent, AuditEvent):
        if not event.contains(model, "after_insert", publish_source):
            event.listen(model, "after_insert", publish_source)
