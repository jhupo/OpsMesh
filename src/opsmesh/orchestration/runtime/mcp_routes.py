"""Independent MCP connection and lifecycle receipt APIs."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from opsmesh.identity.auth.dependencies import CurrentUserDep
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.dependencies import execution_cipher, scope_admission
from opsmesh.orchestration.runtime import control, mcp_service
from opsmesh.orchestration.runtime.mcp import load_connection
from opsmesh.orchestration.runtime.models import MCPConnection, MCPOperationReceipt
from opsmesh.orchestration.runtime.schemas import (
    MCPCreateRequest,
    MCPToolsView,
    MCPView,
    RuntimeOperationView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.shared.serialization import payload_digest

router = APIRouter(prefix="/orchestration/mcp-connections", tags=["orchestration"])
DbSession = Annotated[Session, Depends(get_db_session)]
Key = Annotated[str, Query(min_length=1, max_length=200)]


def connection_view(item: MCPConnection) -> MCPView:
    return MCPView(
        connection_id=item.id,
        name=item.name,
        transport=item.transport,
        status=item.status,
        tools=item.tools,
        tools_digest=payload_digest(
            {"tools": sorted(item.tools, key=lambda tool: str(tool["name"]))}
        ),
        error_code=item.error_code,
    )


@router.post(
    "", response_model=MCPView, status_code=202, operation_id="orchestration.mcp_connections.create"
)
def create(
    payload: MCPCreateRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> MCPView:
    with session_transaction(session):
        item = mcp_service.create(
            session,
            subject=ExecutionSubject(user.user_id),
            admission=scope_admission(request),
            request=payload,
            operation_id=idempotency_key,
        )
    return connection_view(item)


@router.get("", response_model=list[MCPView], operation_id="orchestration.mcp_connections.list")
def list_connections(
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    configuration_id: UUID | None = None,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MCPView]:
    records = mcp_service.list_connections(
        session,
        subject=ExecutionSubject(user.user_id),
        admission=scope_admission(request),
        scope=ExecutionScope(
            configuration_id=configuration_id,
        ),
        after=after,
        limit=limit,
    )
    return [connection_view(item) for item in records]


@router.get(
    "/{connection_id}", response_model=MCPView, operation_id="orchestration.mcp_connections.get"
)
def get(connection_id: UUID, user: CurrentUserDep, session: DbSession, request: Request) -> MCPView:
    item = load_connection(
        session,
        connection_id=connection_id,
        principal_id=user.user_id,
        admission=scope_admission(request),
    )
    return connection_view(item)


@router.get(
    "/{connection_id}/tools",
    response_model=MCPToolsView,
    operation_id="orchestration.mcp_connections.tools.list",
)
def list_tools(
    connection_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> MCPToolsView:
    item = load_connection(
        session,
        connection_id=connection_id,
        principal_id=user.user_id,
        admission=scope_admission(request),
    )
    view = connection_view(item)
    return MCPToolsView(
        connection_id=item.id,
        tools=view.tools,
        tools_digest=view.tools_digest,
    )


@router.post(
    "/{connection_id}/connect",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.mcp_connections.connect",
)
def connect(
    connection_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return operate(connection_id, "connect", user, session, request, idempotency_key)


@router.post(
    "/{connection_id}/close",
    response_model=RuntimeOperationView,
    status_code=202,
    operation_id="orchestration.mcp_connections.close",
)
def close(
    connection_id: UUID,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    return operate(connection_id, "close", user, session, request, idempotency_key)


def operate(
    connection_id: UUID,
    action: Literal["connect", "discover", "close"],
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
    idempotency_key: Key,
) -> RuntimeOperationView:
    with session_transaction(session):
        item = load_connection(
            session,
            connection_id=connection_id,
            principal_id=user.user_id,
            admission=scope_admission(request),
        )
        receipt = mcp_service.stage(session, item=item, operation_id=idempotency_key, action=action)
    return RuntimeOperationView(
        operation_id=receipt.operation_id,
        status=receipt.status,
        result=receipt.result,
        revision=receipt.revision,
    )


@router.post(
    "/{connection_id}/operations/{operation_id}/cancel",
    response_model=RuntimeOperationView,
    operation_id="orchestration.mcp_connections.operations.cancel",
)
def cancel_operation(
    connection_id: UUID,
    operation_id: str,
    payload: control.RuntimeControlRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RuntimeOperationView:
    with session_transaction(session):
        owner = load_connection(
            session,
            connection_id=connection_id,
            principal_id=user.user_id,
            admission=scope_admission(request),
        )
        session.refresh(owner, with_for_update=True)
        receipt = session.get(MCPOperationReceipt, (owner.scope_id, operation_id))
        if receipt is None or receipt.connection_id != owner.id:
            raise NotFoundError("The MCP operation does not exist")
        control.cancel(session, receipt=receipt, actor_id=user.user_id, request=payload)
    return RuntimeOperationView(
        operation_id=receipt.operation_id, status=receipt.status, revision=receipt.revision
    )


@router.post(
    "/{connection_id}/operations/{operation_id}/reconcile",
    response_model=RuntimeOperationView,
    operation_id="orchestration.mcp_connections.operations.reconcile",
)
def reconcile_operation(
    connection_id: UUID,
    operation_id: str,
    payload: control.RuntimeReconcileRequest,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RuntimeOperationView:
    with session_transaction(session):
        receipt = control.reconcile(
            session,
            kind="mcp",
            owner_id=connection_id,
            operation_id=operation_id,
            actor=user,
            request=payload,
            cipher=execution_cipher(request),
            bindings=request.app.state.execution_bindings,
            storage=request.app.state.execution_storage,
        )
    return RuntimeOperationView(
        operation_id=receipt.operation_id, status=receipt.status, revision=receipt.revision
    )


@router.get(
    "/{connection_id}/operations/{operation_id}",
    response_model=RuntimeOperationView,
    operation_id="orchestration.mcp_connections.operations.get",
)
def operation(
    connection_id: UUID,
    operation_id: str,
    user: CurrentUserDep,
    session: DbSession,
    request: Request,
) -> RuntimeOperationView:
    item = load_connection(
        session,
        connection_id=connection_id,
        principal_id=user.user_id,
        admission=scope_admission(request),
    )
    receipt = session.get(MCPOperationReceipt, (item.scope_id, operation_id))
    if receipt is None or receipt.connection_id != item.id:
        raise NotFoundError("The MCP operation does not exist")
    return RuntimeOperationView(
        operation_id=receipt.operation_id,
        status=receipt.status,
        result=receipt.result,
        revision=receipt.revision,
    )
