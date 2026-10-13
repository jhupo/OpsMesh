from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.catalog.contracts import (
    MCPCredentialReferenceCreateRequest,
    MCPCredentialReferenceResponse,
    MCPCredentialReferenceRotateRequest,
    MCPCredentialReferenceUpdateRequest,
)
from opsmesh.capabilities.mcp.catalog.credentials import MCPCredentialService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.errors import DatabaseConflictError
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretEncryptionService

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.post(
    "/mcp-credentials",
    response_model=MCPCredentialReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_mcp_credential_reference(
    request: MCPCredentialReferenceCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MCPCredentialReferenceResponse:
    try:
        credential = MCPCredentialService(
            session,
            SecretEncryptionService(
                secret=settings.credential_encryption_secret,
                key_id=settings.credential_encryption_key_id,
                previous_secrets=settings.credential_encryption_previous_secrets,
            ),
            settings,
        ).create_credential_reference(
            context.workspace.id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MCPCredentialReferenceResponse.model_validate(credential)


@router.patch(
    "/mcp-credentials/{credential_id}",
    response_model=MCPCredentialReferenceResponse,
)
def update_mcp_credential_reference(
    credential_id: UUID,
    request: MCPCredentialReferenceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MCPCredentialReferenceResponse:
    try:
        credential = MCPCredentialService(session, settings=settings).update_credential_reference(
            context.workspace.id,
            credential_id,
            request,
            context.user.user_id,
        )
    except DatabaseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except ValueError as exc:
        detail = str(exc)
        error_status = (
            status.HTTP_400_BAD_REQUEST
            if detail != "MCP credential reference not found"
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=error_status, detail=detail) from exc
    return MCPCredentialReferenceResponse.model_validate(credential)


@router.post(
    "/mcp-credentials/{credential_id}/rotate",
    response_model=MCPCredentialReferenceResponse,
)
def rotate_mcp_credential_reference(
    credential_id: UUID,
    request: MCPCredentialReferenceRotateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MCPCredentialReferenceResponse:
    try:
        credential = MCPCredentialService(
            session,
            SecretEncryptionService(
                secret=settings.credential_encryption_secret,
                key_id=settings.credential_encryption_key_id,
                previous_secrets=settings.credential_encryption_previous_secrets,
            ),
            settings,
        ).rotate_credential_reference(
            context.workspace.id,
            credential_id,
            request,
            context.user.user_id,
        )
    except ValueError as exc:
        detail = str(exc)
        error_status = (
            status.HTTP_404_NOT_FOUND
            if detail == "MCP credential reference not found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=error_status, detail=detail) from exc
    return MCPCredentialReferenceResponse.model_validate(credential)


@router.get("/mcp-credentials", response_model=PageResponse[MCPCredentialReferenceResponse])
def list_mcp_credential_references(
    page: PageParams = Depends(pagination_params),
    mcp_server_id: UUID | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[MCPCredentialReferenceResponse]:
    try:
        items, total = MCPCredentialService(session).list_credential_references(
            context.workspace.id,
            page,
            mcp_server_id=mcp_server_id,
            include_disabled=include_disabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/mcp-credentials/{credential_id}/disable",
    response_model=MCPCredentialReferenceResponse,
)
def disable_mcp_credential_reference(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> MCPCredentialReferenceResponse:
    try:
        credential = MCPCredentialService(session).disable_credential_reference(
            context.workspace.id,
            credential_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MCPCredentialReferenceResponse.model_validate(credential)
