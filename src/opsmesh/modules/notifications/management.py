"""User-facing notifications exclude trusted event ingestion and delivery worker controls."""

from opsmesh.modules.notifications.internal.service import NotificationService

from .api import NotificationRecord, NotificationSubscription


class NotificationManagement:
    def __init__(self, service: NotificationService) -> None:
        self._service = service

    async def save_subscription(
        self,
        subscription: NotificationSubscription,
        *,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> NotificationSubscription:
        return await self._service.save_subscription(
            subscription, actor_id=actor_id, expected_revision=expected_revision
        )

    async def list_subscriptions(
        self,
        *,
        actor_id: str,
        owner_id: str | None = None,
    ) -> list[NotificationSubscription]:
        return await self._service.list_subscriptions(actor_id=actor_id, owner_id=owner_id)

    async def delete_subscription(
        self,
        subscription_id: str,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> None:
        await self._service.delete_subscription(
            subscription_id, actor_id=actor_id, expected_revision=expected_revision
        )

    async def list_records(
        self,
        *,
        actor_id: str,
        owner_id: str | None = None,
        unread: bool = False,
        limit: int = 100,
    ) -> list[NotificationRecord]:
        return await self._service.list_records(
            actor_id=actor_id, owner_id=owner_id, unread=unread, limit=limit
        )

    async def mark_read(
        self,
        identity: str,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> NotificationRecord:
        return await self._service.mark_read(
            identity, actor_id=actor_id, expected_revision=expected_revision
        )

    async def retry(
        self,
        identity: str,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> NotificationRecord:
        return await self._service.retry(
            identity, actor_id=actor_id, expected_revision=expected_revision
        )
