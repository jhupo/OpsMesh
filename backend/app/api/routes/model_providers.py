from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.model_providers import (
    ModelProviderCredentialCreateRequest,
    ModelProviderCredentialResponse,
    ModelProviderCredentialRotateKeyRequest,
    ModelProviderCredentialUpdateRequest,
    ModelProviderHealthCheckRequest,
    ModelProviderHealthCheckResponse,
    ModelProviderUsageAuditResponse,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_db_session
from backend.app.model_providers.audit_responses import usage_audit_response
from backend.app.model_providers.credential_commands import (
    ModelProviderCredentialCommandService,
)
from backend.app.model_providers.credential_queries import (
    ModelProviderCredentialQueryService,
)
from backend.app.model_providers.health_probes import provider_health_probes
from backend.app.model_providers.health_service import ModelProviderHealthService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import EgressUrlValidationError

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
    queries = _queries(session, settings)
    items, total = queries.list(context.workspace.id, page)
    return PageResponse(
        items=[
            _credential_response(
                queries,
                workspace_id=context.workspace.id,
                credential=credential,
            )
            for credential in items
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/usage-audit", response_model=PageResponse[ModelProviderUsageAuditResponse])
async def list_model_provider_usage_audit(
    page: PageParams = Depends(pagination_params),
    action: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[ModelProviderUsageAuditResponse]:
    items, total = _queries(session, settings).list_usage_audit(
        context.workspace.id,
        page,
        action=action,
    )
    return PageResponse(
        items=[usage_audit_response(item) for item in items],
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
    try:
        credential = _commands(session, settings).create(
            workspace_id=context.workspace.id,
            created_by_user_id=context.user.user_id,
            name=request.name,
            provider=request.provider,
            api_key=request.api_key,
            default_model=request.default_model,
            base_url=str(request.base_url) if request.base_url is not None else None,
            model_api=request.model_api,
            is_default=request.is_default,
            budget_metadata=request.budget_metadata,
        )
    except EgressUrlValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise _model_provider_http_error(exc) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


@router.patch("/{credential_id}", response_model=ModelProviderCredentialResponse)
async def update_model_provider_credential(
    credential_id: UUID,
    request: ModelProviderCredentialUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _commands(session, settings).update(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
            name=request.name,
            provider=request.provider,
            default_model=request.default_model,
            base_url=str(request.base_url) if request.base_url is not None else None,
            model_api=request.model_api,
            model_api_provided="model_api" in request.model_fields_set,
            is_default=request.is_default,
            budget_metadata=request.budget_metadata,
        )
    except EgressUrlValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise _model_provider_http_error(exc) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


@router.post("/{credential_id}/rotate-key", response_model=ModelProviderCredentialResponse)
async def rotate_model_provider_credential_key(
    credential_id: UUID,
    request: ModelProviderCredentialRotateKeyRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _commands(session, settings).rotate_key(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
            api_key=request.api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


@router.post("/{credential_id}/health-check", response_model=ModelProviderHealthCheckResponse)
async def check_model_provider_credential_health(
    credential_id: UUID,
    request: ModelProviderHealthCheckRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderHealthCheckResponse:
    try:
        probes = provider_health_probes(request.probes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    health = _health(session, settings)
    queries = _queries(session, settings)
    try:
        result = await health.run_health_check(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
            probes=probes,
            timeout_seconds=request.timeout_seconds,
        )
        credential = queries.get(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model provider credential not found",
        )
    return ModelProviderHealthCheckResponse(
        credential=_credential_response(
            queries,
            workspace_id=context.workspace.id,
            credential=credential,
        ),
        status=result.status,
        checks=[check.as_dict() for check in result.checks],
    )


@router.post("/{credential_id}/set-default", response_model=ModelProviderCredentialResponse)
async def set_default_model_provider_credential(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _commands(session, settings).set_default(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


@router.post("/{credential_id}/disable", response_model=ModelProviderCredentialResponse)
async def disable_model_provider_credential(
    credential_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ModelProviderCredentialResponse:
    try:
        credential = _commands(session, settings).disable(
            workspace_id=context.workspace.id,
            credential_id=credential_id,
            actor_user_id=context.user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


def _commands(session: Session, settings: Settings) -> ModelProviderCredentialCommandService:
    return ModelProviderCredentialCommandService(session, _secret_service(settings))


def _queries(session: Session, settings: Settings) -> ModelProviderCredentialQueryService:
    return ModelProviderCredentialQueryService(session, _secret_service(settings))


def _health(session: Session, settings: Settings) -> ModelProviderHealthService:
    return ModelProviderHealthService(session, _secret_service(settings))


def _secret_service(settings: Settings) -> SecretEncryptionService:
    return SecretEncryptionService(
        secret=settings.credential_encryption_secret,
        key_id=settings.credential_encryption_key_id,
        previous_secrets=settings.credential_encryption_previous_secrets,
    )


def _model_provider_http_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    code = (
        status.HTTP_404_NOT_FOUND
        if "not found" in message.lower()
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=message)


def _credential_response(
    queries: ModelProviderCredentialQueryService,
    *,
    workspace_id: UUID,
    credential: object,
) -> ModelProviderCredentialResponse:
    response = ModelProviderCredentialResponse.model_validate(credential)
    return response.model_copy(
        update={
            "scheduled_health_check": queries.health_check_schedule_summary(
                workspace_id=workspace_id,
                credential_id=response.id,
            )
        }
    )
