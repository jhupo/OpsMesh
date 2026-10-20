"""Explicit MCP lifecycle intents, independent of model Run ownership."""

from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.contracts import MCPCommand
from opsmesh.orchestration.runtime.mcp import load_connection
from opsmesh.orchestration.runtime.models import MCPConnection, MCPOperationReceipt
from opsmesh.orchestration.runtime.schemas import MCPCreateRequest
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.security.redaction import redact_sensitive_payload, redaction_rules_for
from opsmesh.shared.security.url_validation import validate_url_shape
from opsmesh.shared.serialization import payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution


def list_connections(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    scope: ExecutionScope,
    after: UUID | None,
    limit: int,
) -> list[MCPConnection]:
    admission.admit(session, subject=subject, scope=scope)
    owner_ref = (
        f"configuration:{scope.configuration_id}"
        if scope.configuration_id
        else f"user:{subject.user_id}"
    )
    query = select(MCPConnection).where(
        MCPConnection.principal_id == subject.user_id,
        MCPConnection.scope_id == (scope.configuration_id or subject.user_id),
        MCPConnection.owner_ref == owner_ref,
    )
    if after is not None:
        query = query.where(MCPConnection.id > after)
    return list(session.scalars(query.order_by(MCPConnection.id).limit(limit)))


def create(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    request: MCPCreateRequest,
    operation_id: str,
) -> MCPConnection:
    scope = ExecutionScope(
        configuration_id=request.configuration_id,
    )
    admission.admit(session, subject=subject, scope=scope)
    owner_ref = (
        f"configuration:{scope.configuration_id}"
        if scope.configuration_id
        else f"user:{subject.user_id}"
    )
    if (
        redact_sensitive_payload(dict(request.environment), rules=redaction_rules_for(session))
        != request.environment
    ):
        raise InvalidArgumentError("MCP environment declarations cannot contain secrets")
    if request.transport == "stdio":
        if request.environment_id is None or not request.command:
            raise InvalidArgumentError("Stdio MCP requires an explicit sandbox and command")
        environment = load_environment(
            session, environment_id=request.environment_id, subject=subject, admission=admission
        )
        if (
            environment.scope_id != (scope.configuration_id or subject.user_id)
            or environment.owner_ref != owner_ref
        ):
            raise InvalidArgumentError("The MCP environment is outside this scope")
    else:
        if request.endpoint is None or request.command or request.environment_id is not None:
            raise InvalidArgumentError("Remote MCP requires an HTTPS endpoint")
        validate_url_shape(request.endpoint, allowed_schemes=frozenset({"https"}))
        parts = urlsplit(request.endpoint)
        if parts.query or parts.fragment:
            raise InvalidArgumentError("MCP endpoint queries and fragments are not allowed")

    def record() -> MCPConnection:
        item = MCPConnection(
            scope_id=scope.configuration_id or subject.user_id,
            principal_id=subject.user_id,
            owner_ref=owner_ref,
            name=request.name,
            transport=request.transport,
            endpoint=request.endpoint,
            environment_id=request.environment_id,
            status="pending",
            configuration={
                "command": request.command,
                "environment": request.environment,
            },
        )
        session.add(item)
        session.flush()
        stage(session, item=item, operation_id=operation_id, action="connect")
        return item

    return run_idempotent_create(
        session=session,
        scope_id=scope.configuration_id or subject.user_id,
        actor_id=subject.user_id,
        operation="orchestration.mcp",
        idempotency_key=operation_id,
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda ref: load_connection(
            session, connection_id=ref, principal_id=subject.user_id, admission=admission
        ),
        create=record,
        resource_id=lambda item: item.id,
    )


def stage(
    session: Session,
    *,
    item: MCPConnection,
    operation_id: str,
    action: str,
    cleanup_ref: UUID | None = None,
) -> MCPOperationReceipt:
    fingerprint = payload_digest({"connection_id": str(item.id), "action": action})
    session.refresh(item, with_for_update=True)
    previous = session.get(MCPOperationReceipt, (item.scope_id, operation_id))
    if previous is not None:
        if previous.connection_id != item.id or previous.fingerprint != fingerprint:
            raise ConflictError("The MCP operation identity was reused")
        return previous
    if session.scalar(
        select(MCPOperationReceipt.operation_id)
        .where(
            MCPOperationReceipt.connection_id == item.id,
            MCPOperationReceipt.status.in_(("pending", "started", "uncertain")),
        )
        .limit(1)
    ):
        raise ConflictError("The MCP connection has an unfinished operation")
    execution_id = uuid4()
    receipt = MCPOperationReceipt(
        scope_id=item.scope_id,
        operation_id=operation_id,
        connection_id=item.id,
        action=action,
        fingerprint=fingerprint,
        status="pending",
        workflow_id=f"mcp:{execution_id}",
    )
    session.add(receipt)
    stage_execution(
        session,
        MCPCommand(
            execution_id=execution_id,
            workflow_id=f"mcp:{execution_id}",
            connection_id=item.id,
            principal_id=item.principal_id,
            operation_id=operation_id,
            action=action,
            cleanup_ref=cleanup_ref,
        ),
    )
    return receipt
