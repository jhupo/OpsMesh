from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.model_providers import (
    ModelProviderCredentialCreateRequest,
    ModelProviderCredentialResponse,
    ModelProviderCredentialRotateKeyRequest,
    ModelProviderCredentialUpdateRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.secrets.service import SecretEncryptionService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/model-provider-credentials",
    tags=["model-providers"],
)


@router.get("", response_model=PageResponse[ModelProviderCredentialResponse])
async def list_model_provider_credentials(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[ModelProviderCredentialResponse]:
    items, total = _service(session, settings).list(context.workspace.id, page)
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "",
    response_model=ModelProviderCredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_model_provider_credential(
    request: ModelProviderCredentialCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    credential = _service(session, settings).create(
        workspace_id=context.workspace.id,
        created_by_user_id=context.user.user_id,
        name=request.name,
        provider=request.provider,
        api_key=request.api_key,
        default_model=request.default_model,
        base_url=str(request.base_url) if request.base_url is not None else None,
        is_default=request.is_default,
    )
    return ModelProviderCredentialResponse.model_validate(credential)


@router.patch("/{credential_id}", response_model=ModelProviderCredentialResponse)
async def update_model_provider_credential(
    credential_id: UUID,
    request: ModelProviderCredentialUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _service(session, settings).update(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
            name=request.name,
            provider=request.provider,
            default_model=request.default_model,
            base_url=str(request.base_url) if request.base_url is not None else None,
            is_default=request.is_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ModelProviderCredentialResponse.model_validate(credential)


@router.post("/{credential_id}/rotate-key", response_model=ModelProviderCredentialResponse)
async def rotate_model_provider_credential_key(
    credential_id: UUID,
    request: ModelProviderCredentialRotateKeyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _service(session, settings).rotate_key(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
            api_key=request.api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ModelProviderCredentialResponse.model_validate(credential)


@router.post("/{credential_id}/set-default", response_model=ModelProviderCredentialResponse)
async def set_default_model_provider_credential(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _service(session, settings).set_default(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ModelProviderCredentialResponse.model_validate(credential)


@router.post("/{credential_id}/disable", response_model=ModelProviderCredentialResponse)
async def disable_model_provider_credential(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _service(session, settings).disable(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ModelProviderCredentialResponse.model_validate(credential)


def _service(session: Session, settings: Settings) -> ModelProviderCredentialService:
    return ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
