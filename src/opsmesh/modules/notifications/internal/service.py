"""Durable notification rules and inbox, independent of optional command plugins."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from string import Template
from typing import Any

from opsmesh.framework.adapters import OpsMeshMessage, ReplyTarget
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.bots.api import MessageDelivery
from opsmesh.modules.notifications.api import NotificationRecord, NotificationSubscription
from opsmesh.modules.notifications.internal.delivery_state import NotificationDeliveryState

Authorize = Callable[[str, str, str], Awaitable[None]]


class NotificationService:
    def __init__(
        self,
        database: AsyncDatabase,
        accounts: AccountDirectory,
        audit: AuditJournal,
        delivery: MessageDelivery,
        authorize: Authorize,
    ) -> None:
        self.subscriptions = VersionedRecordStore(database, "notification_subscriptions")
        self.records = VersionedRecordStore(database, "notification_records")
        self._delivery_state = NotificationDeliveryState(self.records)
        self.accounts, self.audit, self.delivery, self.authorize = (
            accounts,
            audit,
            delivery,
            authorize,
        )

    async def _owner(self, actor_id: str, owner_id: str, *, manage: bool = False) -> None:
        principal = await self.accounts.get_principal(actor_id)
        if principal.active and principal.id == owner_id:
            return
        await self.accounts.authorize(
            principal,
            "notifications:manage" if manage else "notifications:read",
            owner_id,
            owner_id=owner_id,
        )

    async def save_subscription(
        self,
        subscription: NotificationSubscription,
        *,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> NotificationSubscription:
        await self._owner(actor_id, subscription.owner_id, manage=True)
        subscription = NotificationSubscription.model_validate(subscription.model_dump())
        await self.accounts.get_principal(subscription.owner_id)
        current = await self.subscriptions.get(subscription.id)
        if current and current.data["owner_id"] != subscription.owner_id:
            raise OpsMeshError("CONFLICT", "Subscription ownership is immutable")
        if subscription.target:
            await self.authorize(actor_id, "bots.send", subscription.target.bot.bot_id)
            await self.authorize(subscription.owner_id, "bots.send", subscription.target.bot.bot_id)
        async with audited_operation(
            self.audit, "notifications.subscription.save", subscription.id, actor_id
        ):
            if expected_revision is None:
                record = await self.subscriptions.create(
                    subscription.id, subscription.model_dump(mode="json")
                )
            else:
                record = await self.subscriptions.update(
                    subscription.id, subscription.model_dump(mode="json"), expected_revision
                )
        return subscription.model_copy(update={"revision": record.revision})

    async def list_subscriptions(
        self, *, actor_id: str, owner_id: str | None = None
    ) -> list[NotificationSubscription]:
        selected = owner_id or actor_id
        await self._owner(actor_id, selected)
        return [
            NotificationSubscription.model_validate({**item.data, "revision": item.revision})
            for item in await self.subscriptions.list()
            if item.data["owner_id"] == selected
        ]

    async def delete_subscription(
        self, subscription_id: str, *, actor_id: str, expected_revision: int
    ) -> None:
        current = await self.subscriptions.get(subscription_id)
        if current is None:
            raise OpsMeshError("NOT_FOUND", "Subscription not found")
        await self._owner(actor_id, current.data["owner_id"], manage=True)
        async with audited_operation(
            self.audit, "notifications.subscription.delete", subscription_id, actor_id
        ):
            await self.subscriptions.delete(subscription_id, expected_revision)

    async def observe(
        self,
        *,
        event_id: str,
        event_type: str,
        owner_id: str,
        resource_id: str,
        status: str,
        summary: str = "",
        default_target: ReplyTarget | None = None,
    ) -> None:
        """Trusted coordinator entry point; content is pre-redacted, never raw event payload."""
        fields = {
            "event_type": event_type,
            "resource_id": resource_id,
            "status": status,
            "summary": summary[:2000],
        }
        candidates = [
            NotificationSubscription.model_validate(item.data)
            for item in await self.subscriptions.list()
            if item.data["owner_id"] == owner_id
            and item.data.get("enabled")
            and event_type in item.data["event_types"]
        ]
        # Every runtime event has an inbox record, even without a Bot destination or subscription.
        await self._enqueue(
            "inbox",
            event_id,
            event_type,
            owner_id,
            resource_id,
            Template("${event_type}: ${resource_id} (${status})").substitute(fields),
            default_target,
        )
        for subscription in candidates:
            await self._enqueue(
                f"subscription:{subscription.id}",
                event_id,
                event_type,
                owner_id,
                resource_id,
                Template(subscription.template).substitute(fields),
                subscription.target,
            )

    async def _enqueue(
        self,
        rule_id: str,
        event_id: str,
        event_type: str,
        owner_id: str,
        resource_id: str,
        content: str,
        target: ReplyTarget | None,
    ) -> None:
        identity = sha256(json.dumps([rule_id, event_id, owner_id]).encode()).hexdigest()
        notification = NotificationRecord(
            id=identity,
            event_id=event_id,
            event_type=event_type,
            owner_id=owner_id,
            resource_id=resource_id,
            content=content,
            target=target,
            created_at=datetime.now(UTC),
            status="pending" if target else "sent",
        )
        try:
            await self.records.create(identity, notification.model_dump(mode="json"))
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise

    async def list_records(
        self, *, actor_id: str, owner_id: str | None = None, unread: bool = False, limit: int = 100
    ) -> list[NotificationRecord]:
        selected = owner_id or actor_id
        await self._owner(actor_id, selected)
        if not 1 <= limit <= 1000:
            raise OpsMeshError("INVALID_REQUEST", "Limit must be 1 to 1000")
        return [
            NotificationRecord.model_validate({**item.data, "revision": item.revision})
            for item in reversed(await self.records.list())
            if item.data["owner_id"] == selected and (not unread or not item.data["read"])
        ][:limit]

    async def mark_read(
        self, identity: str, *, actor_id: str, expected_revision: int
    ) -> NotificationRecord:
        item = await self._get(identity)
        await self._owner(actor_id, item.owner_id)
        if expected_revision != item.revision:
            raise OpsMeshError("CONFLICT", "Notification revision changed")
        saved = await self.records.update(
            identity, {**item.model_dump(mode="json"), "read": True}, item.revision
        )
        return NotificationRecord.model_validate({**saved.data, "revision": saved.revision})

    async def retry(
        self, identity: str, *, actor_id: str, expected_revision: int
    ) -> NotificationRecord:
        item = await self._get(identity)
        await self._owner(actor_id, item.owner_id, manage=True)
        if expected_revision != item.revision:
            raise OpsMeshError("CONFLICT", "Notification revision changed")
        if item.status != "failed":
            raise OpsMeshError("CONFLICT", "Only a confirmed failed notification can be retried")
        async with audited_operation(self.audit, "notifications.retry", identity, actor_id):
            saved = await self.records.update(
                identity,
                {**item.model_dump(mode="json"), "status": "pending", "error_code": None},
                item.revision,
            )
        return NotificationRecord.model_validate({**saved.data, "revision": saved.revision})

    async def _get(self, identity: str) -> NotificationRecord:
        item = await self.records.get(identity)
        if item is None:
            raise OpsMeshError("NOT_FOUND", "Notification not found")
        return NotificationRecord.model_validate({**item.data, "revision": item.revision})

    async def tick(self) -> None:
        for stored in await self.records.list():
            item = NotificationRecord.model_validate({**stored.data, "revision": stored.revision})
            if (
                item.status == "delivering"
                and item.lease_until
                and item.lease_until < datetime.now(UTC)
            ):
                try:
                    await self.records.update(
                        item.id,
                        {**stored.data, "status": "unknown", "error_code": "DELIVERY_UNKNOWN"},
                        stored.revision,
                    )
                except OpsMeshError as error:
                    if error.code != "CONFLICT":
                        raise
            if item.status == "pending":
                await self._deliver(item)

    async def _deliver(self, item: NotificationRecord) -> None:
        if item.target is None:
            return
        try:
            claim = await self.records.update(
                item.id,
                {
                    **item.model_dump(mode="json"),
                    "status": "delivering",
                    "attempts": item.attempts + 1,
                    "lease_until": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                },
                item.revision,
            )
        except OpsMeshError as error:
            if error.code == "CONFLICT":
                return
            raise
        updates: dict[str, Any]
        try:
            receipt = await self.delivery.send(
                item.target,
                OpsMeshMessage(item.content),
                idempotency_key=f"notification:{item.id}",
                actor_id=item.owner_id,
            )
            updates = {
                "status": "sent",
                "delivery_id": receipt.delivery_id,
                "delivered_at": datetime.now(UTC).isoformat(),
            }
        except OpsMeshError as error:
            updates = {
                "status": "unknown" if error.code == "DELIVERY_UNKNOWN" else "failed",
                "error_code": error.code,
            }
        except Exception:
            updates = {"status": "unknown", "error_code": "DELIVERY_UNKNOWN"}
        await self._delivery_state.complete(claim, updates)
