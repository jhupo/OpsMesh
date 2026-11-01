"""Notification subscriptions and inbox management."""

from fastapi import APIRouter, Request, Response
from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.notifications.api import NotificationRecord, NotificationSubscription

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationRevision(OpsMeshContract):
    expected_revision: int = Field(ge=1)


@router.get("")
async def notifications(
    request: Request, user: CurrentUser, unread: bool = False, limit: int = 100
) -> list[NotificationRecord]:
    return await get_application(request).services.notification_management.list_records(
        actor_id=user.id, unread=unread, limit=limit
    )


@router.get("/subscriptions")
async def subscriptions(request: Request, user: CurrentUser) -> list[NotificationSubscription]:
    return await get_application(request).services.notification_management.list_subscriptions(
        actor_id=user.id
    )


@router.post("/subscriptions", status_code=201)
async def create_subscription(
    body: NotificationSubscription, request: Request, user: CurrentUser
) -> NotificationSubscription:
    return await get_application(request).services.notification_management.save_subscription(
        body, actor_id=user.id
    )


@router.put("/subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: str,
    body: NotificationSubscription,
    expected_revision: int,
    request: Request,
    user: CurrentUser,
) -> NotificationSubscription:
    from opsmesh.framework.errors import OpsMeshError

    if subscription_id != body.id:
        raise OpsMeshError("INVALID_REQUEST", "Subscription identifier cannot change")
    return await get_application(request).services.notification_management.save_subscription(
        body, actor_id=user.id, expected_revision=expected_revision
    )


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: str, expected_revision: int, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.notification_management.delete_subscription(
        subscription_id, actor_id=user.id, expected_revision=expected_revision
    )
    return Response(status_code=204)


@router.post("/{identity}/read")
async def mark_read(
    identity: str, body: NotificationRevision, request: Request, user: CurrentUser
) -> NotificationRecord:
    return await get_application(request).services.notification_management.mark_read(
        identity, actor_id=user.id, expected_revision=body.expected_revision
    )


@router.post("/{identity}/retry")
async def retry(
    identity: str, body: NotificationRevision, request: Request, user: CurrentUser
) -> NotificationRecord:
    return await get_application(request).services.notification_management.retry(
        identity, actor_id=user.id, expected_revision=body.expected_revision
    )
