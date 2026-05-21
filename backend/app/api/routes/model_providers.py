from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.model_providers import (
    ModelProviderCredentialCreateRequest,
    ModelProviderCredentialResponse,
    ModelProviderCredentialRotateKeyRequest,
    ModelProviderCredentialUpdateRequest,
    ModelProviderUsageAuditResponse,
)
from backend.app.audit.models import AuditEvent
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


@router.get("/usage-audit", response_model=PageResponse[ModelProviderUsageAuditResponse])
async def list_model_provider_usage_audit(
    page: PageParams = Depends(pagination_params),
    action: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[ModelProviderUsageAuditResponse]:
    items, total = _service(session, settings).list_usage_audit(
        context.workspace.id,
        page,
        action=action,
    )
    return PageResponse(
        items=[_usage_audit_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


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


def _usage_audit_response(event: AuditEvent) -> ModelProviderUsageAuditResponse:
    metadata = event.audit_metadata
    metadata = metadata if isinstance(metadata, dict) else {}
    failed_provider = metadata.get("failed_provider")
    reason = metadata.get("reason")
    return ModelProviderUsageAuditResponse(
        id=event.id,
        action=event.action,
        run_id=str(event.target_id) or None,
        task_id=_string_or_none(metadata.get("task_id")),
        task_step_id=_string_or_none(metadata.get("task_step_id")),
        agent_profile_id=_string_or_none(metadata.get("agent_profile_id")),
        model=_string_or_none(metadata.get("model")),
        credential_id=_string_or_none(metadata.get("credential_id")),
        fallback_selected=metadata.get("fallback_selected")
        if isinstance(metadata.get("fallback_selected"), bool)
        else None,
        reason=_sanitized_metadata(reason),
        failed_provider=_sanitized_provider_ref(failed_provider),
        created_at=event.created_at,
    )


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


_SENSITIVE_METADATA_KEYS = {
    "api_key",
    "authorization",
    "base_url",
    "encrypted_api_key",
    "external_ref",
    "secret",
    "token",
}


def _sanitized_metadata(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    sanitized: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text.lower() in _SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(item, dict):
            nested = _sanitized_metadata(item)
            sanitized[key_text] = nested if nested is not None else {}
        elif isinstance(item, list):
            sanitized[key_text] = [
                _sanitized_metadata(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            sanitized[key_text] = item
    return sanitized


def _sanitized_provider_ref(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    sanitized: dict[str, object] = {}
    for key in ("model", "credential_id"):
        item = value.get(key)
        if isinstance(item, str):
            sanitized[key] = item
    return sanitized
