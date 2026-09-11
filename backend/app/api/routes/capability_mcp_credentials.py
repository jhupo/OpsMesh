from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.capabilities.mcp_credentials import (
    McpCredentialReferenceCreateRequest,
    McpCredentialReferenceResponse,
    McpCredentialReferenceRotateRequest,
    McpCredentialReferenceUpdateRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.capabilities.mcp.credentials import McpCredentialService
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.errors import DatabaseConflictError
from backend.app.db.session import get_db_session
from backend.app.secrets.service import SecretEncryptionService

router = APIRouter(prefix="/workspaces/{workspace_id}/capabilities", tags=["capabilities"])


@router.post(
    "/mcp-credentials",
    response_model=McpCredentialReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp_credential_reference(
    request: McpCredentialReferenceCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpCredentialReferenceResponse:
    try:
        credential = McpCredentialService(
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
    return McpCredentialReferenceResponse.model_validate(credential)


@router.patch(
    "/mcp-credentials/{credential_id}",
    response_model=McpCredentialReferenceResponse,
)
async def update_mcp_credential_reference(
    credential_id: UUID,
    request: McpCredentialReferenceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpCredentialReferenceResponse:
    try:
        credential = McpCredentialService(session, settings=settings).update_credential_reference(
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
    return McpCredentialReferenceResponse.model_validate(credential)


@router.post(
    "/mcp-credentials/{credential_id}/rotate",
    response_model=McpCredentialReferenceResponse,
)
async def rotate_mcp_credential_reference(
    credential_id: UUID,
    request: McpCredentialReferenceRotateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> McpCredentialReferenceResponse:
    try:
        credential = McpCredentialService(
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
    return McpCredentialReferenceResponse.model_validate(credential)


@router.get("/mcp-credentials", response_model=PageResponse[McpCredentialReferenceResponse])
async def list_mcp_credential_references(
    page: PageParams = Depends(pagination_params),
    mcp_server_id: UUID | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[McpCredentialReferenceResponse]:
    try:
        items, total = McpCredentialService(session).list_credential_references(
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
    response_model=McpCredentialReferenceResponse,
)
async def disable_mcp_credential_reference(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.MANAGE_CAPABILITY)),
    session: Session = Depends(get_db_session),
) -> McpCredentialReferenceResponse:
    try:
        credential = McpCredentialService(session).disable_credential_reference(
            context.workspace.id,
            credential_id,
            context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return McpCredentialReferenceResponse.model_validate(credential)
