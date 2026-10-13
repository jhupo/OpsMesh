import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from opsmesh.agents.providers.audit import ModelProviderUsageAuditResponse, usage_audit_response
from opsmesh.agents.providers.contracts import provider_health_probes
from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.agents.providers.health import ModelProviderHealthService, ModelProviderHealthSnapshot
from opsmesh.agents.providers.probes import ModelProviderHealthCheckResult, probe_model_provider
from opsmesh.agents.providers.queries import ModelProviderCredentialQueryService
from opsmesh.agents.providers.schemas import (
    ModelProviderCredentialCreateRequest,
    ModelProviderCredentialResponse,
    ModelProviderCredentialRotateKeyRequest,
    ModelProviderCredentialUpdateRequest,
    ModelProviderHealthCheckRequest,
    ModelProviderHealthCheckResponse,
)
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.egress import EgressURLValidationError
from opsmesh.shared.security.secrets import SecretEncryptionService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/model-provider-credentials",
    tags=["model-providers"],
)


@router.get("", response_model=PageResponse[ModelProviderCredentialResponse])
def list_model_provider_credentials(
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
def list_model_provider_usage_audit(
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
def create_model_provider_credential(
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
            model_capabilities=[item.model_dump() for item in request.model_capabilities]
            if request.model_capabilities is not None
            else None,
        )
    except EgressURLValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise _model_provider_http_error(exc) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


@router.patch("/{credential_id}", response_model=ModelProviderCredentialResponse)
def update_model_provider_credential(
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
            model_capabilities=[item.model_dump() for item in request.model_capabilities]
            if request.model_capabilities is not None
            else None,
        )
    except EgressURLValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise _model_provider_http_error(exc) from exc
    return _credential_response(
        _queries(session, settings),
        workspace_id=context.workspace.id,
        credential=credential,
    )


@router.post("/{credential_id}/rotate-key", response_model=ModelProviderCredentialResponse)
def rotate_model_provider_credential_key(
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
    workspace_id = context.workspace.id
    actor_user_id = context.user.user_id
    try:
        snapshot = await asyncio.to_thread(
            _prepare_health_check, session, settings, workspace_id, credential_id
        )
        result = await probe_model_provider(
            snapshot.target,
            probes=probes,
            timeout_seconds=request.timeout_seconds,
        )
        return await asyncio.to_thread(
            _complete_health_check,
            session,
            settings,
            workspace_id,
            credential_id,
            actor_user_id,
            snapshot,
            result,
        )
    except ValueError as exc:
        raise _model_provider_http_error(exc) from exc


def _prepare_health_check(
    session: Session, settings: Settings, workspace_id: UUID, credential_id: UUID
) -> ModelProviderHealthSnapshot:
    try:
        return _health(session, settings).prepare_health_check(
            workspace_id=workspace_id, credential_id=credential_id
        )
    finally:
        # Release the request's read transaction before awaiting the remote provider.
        session.rollback()


def _complete_health_check(
    session: Session,
    settings: Settings,
    workspace_id: UUID,
    credential_id: UUID,
    actor_user_id: UUID,
    snapshot: ModelProviderHealthSnapshot,
    result: ModelProviderHealthCheckResult,
) -> ModelProviderHealthCheckResponse:
    WorkspaceAuthorizationService(session).require_workspace(
        user_id=actor_user_id, workspace_id=workspace_id, action=WorkspaceAction.WRITE
    )
    credential = _health(session, settings).complete_health_check(
        workspace_id=workspace_id,
        credential_id=credential_id,
        actor_user_id=actor_user_id,
        snapshot=snapshot,
        result=result,
    )
    response = ModelProviderHealthCheckResponse(
        credential=_credential_response(
            _queries(session, settings), workspace_id=workspace_id, credential=credential
        ),
        status=result.status,
        checks=[check.as_dict() for check in result.checks],
    )
    session.commit()
    return response


@router.post("/{credential_id}/set-default", response_model=ModelProviderCredentialResponse)
def set_default_model_provider_credential(
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
def disable_model_provider_credential(
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
        status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_400_BAD_REQUEST
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
