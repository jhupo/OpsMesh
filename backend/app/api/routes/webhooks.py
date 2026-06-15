from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.schemas.webhooks import (
    WebhookDeliveryAttemptResponse,
    WebhookSigningSecretRotateRequest,
    WebhookSubscriptionCreateRequest,
    WebhookSubscriptionResponse,
    WebhookSubscriptionUpdateRequest,
)
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.config import Settings, get_settings
from backend.app.db.pagination import page_scalars
from backend.app.db.session import get_db_session
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import EgressUrlValidationError
from backend.app.webhooks.models import WebhookDeliveryAttempt
from backend.app.webhooks.service import (
    WebhookDeliveryReplayError,
    WebhookDeliveryReplayRateLimitError,
    WebhookDeliveryService,
    WebhookSubscriptionService,
)
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue

router = APIRouter(
    prefix="/workspaces/{workspace_id}/webhook-subscriptions",
    tags=["webhooks"],
)


@router.get("", response_model=PageResponse[WebhookSubscriptionResponse])
async def list_webhook_subscriptions(
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[WebhookSubscriptionResponse]:
    items, total = _service(session, settings).list(
        workspace_id=context.workspace.id,
        page=page,
        status=status_filter,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "",
    response_model=WebhookSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook_subscription(
    request: WebhookSubscriptionCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WebhookSubscriptionResponse:
    try:
        subscription = _service(session, settings).create(
            workspace_id=context.workspace.id,
            created_by_user_id=context.user.user_id,
            name=request.name,
            target_url=str(request.target_url),
            event_types=request.event_types,
            signing_secret=request.signing_secret,
        )
    except EgressUrlValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WebhookSubscriptionResponse.model_validate(subscription)


@router.patch("/{subscription_id}", response_model=WebhookSubscriptionResponse)
async def update_webhook_subscription(
    subscription_id: UUID,
    request: WebhookSubscriptionUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WebhookSubscriptionResponse:
    try:
        subscription = _service(session, settings).update(
            workspace_id=context.workspace.id,
            subscription_id=subscription_id,
            name=request.name,
            target_url=str(request.target_url) if request.target_url is not None else None,
            event_types=request.event_types,
        )
    except EgressUrlValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WebhookSubscriptionResponse.model_validate(subscription)


@router.post("/{subscription_id}/rotate-signing-secret", response_model=WebhookSubscriptionResponse)
async def rotate_webhook_signing_secret(
    subscription_id: UUID,
    request: WebhookSigningSecretRotateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WebhookSubscriptionResponse:
    try:
        subscription = _service(session, settings).rotate_signing_secret(
            workspace_id=context.workspace.id,
            subscription_id=subscription_id,
            signing_secret=request.signing_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WebhookSubscriptionResponse.model_validate(subscription)


@router.post("/{subscription_id}/disable", response_model=WebhookSubscriptionResponse)
async def disable_webhook_subscription(
    subscription_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WebhookSubscriptionResponse:
    try:
        subscription = _service(session, settings).disable(
            workspace_id=context.workspace.id,
            subscription_id=subscription_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WebhookSubscriptionResponse.model_validate(subscription)


@router.get(
    "/{subscription_id}/delivery-attempts",
    response_model=PageResponse[WebhookDeliveryAttemptResponse],
)
async def list_webhook_delivery_attempts(
    subscription_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WebhookDeliveryAttemptResponse]:
    from sqlalchemy import select

    statement = (
        select(WebhookDeliveryAttempt)
        .where(
            WebhookDeliveryAttempt.workspace_id == context.workspace.id,
            WebhookDeliveryAttempt.subscription_id == subscription_id,
        )
        .order_by(
            WebhookDeliveryAttempt.created_at.desc(),
            WebhookDeliveryAttempt.id.desc(),
        )
    )
    rows, total = page_scalars(session, statement, page)
    return PageResponse(
        items=rows,
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/{subscription_id}/delivery-attempts/{delivery_attempt_id}/replay",
    response_model=WebhookDeliveryAttemptResponse,
)
async def replay_webhook_delivery_attempt(
    subscription_id: UUID,
    delivery_attempt_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.WRITE)),
    session: Session = Depends(get_db_session),
    queue: RedisQueue = Depends(get_worker_queue),
) -> WebhookDeliveryAttemptResponse:
    try:
        attempt = WebhookDeliveryService(session).replay_attempt(
            workspace_id=context.workspace.id,
            subscription_id=subscription_id,
            delivery_attempt_id=delivery_attempt_id,
            queue=queue,
        )
    except WebhookDeliveryReplayRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except WebhookDeliveryReplayError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WebhookDeliveryAttemptResponse.model_validate(attempt)


def _service(session: Session, settings: Settings) -> WebhookSubscriptionService:
    return WebhookSubscriptionService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        ),
    )
