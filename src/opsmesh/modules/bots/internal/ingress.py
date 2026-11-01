"""Protocol-independent identity resolution, Agent routing and event deduplication."""

import asyncio
import hashlib
import json
from collections.abc import Callable

from opsmesh.framework.adapters import (
    BotInboundEvent,
    BotMessageEvent,
    BotRef,
    OpsMeshBot,
    ReplyTarget,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext, EventEnvelope, OpsMeshEvent
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.bots.api import (
    AuthorizeBotAction,
    BotAccount,
    BotEventDispatcher,
    BotIdentity,
    BotPrincipalResolver,
    BotRoute,
    InboundReceipt,
)

from .delivery_records import BotDeliveryRecords
from .record_keys import bot_record_key


class BotEventIngress:
    def __init__(
        self,
        store: VersionedRecordStore,
        authorize: AuthorizeBotAction,
        dispatcher: BotEventDispatcher,
        bot_factory: Callable[[BotAccount], OpsMeshBot],
        *,
        principal_resolver: BotPrincipalResolver | None = None,
    ) -> None:
        self.store, self.authorize, self.dispatcher = store, authorize, dispatcher
        self.bot_factory = bot_factory
        self.principal_resolver = principal_resolver
        self.records = BotDeliveryRecords(store)

    async def _message_principal(self, account: BotAccount, event: BotMessageEvent) -> str:
        record = await self.store.get(bot_record_key("identity", account.id, event.user_id))
        if record is not None:
            # Explicit mappings always win, including mappings whose permission was revoked.
            return BotIdentity.model_validate(record.data["identity"]).principal_id
        principal_id = account.unlinked_principal_id
        if principal_id is None or event.channel_id not in account.unlinked_channels:
            raise OpsMeshError("FORBIDDEN", "External bot user is not linked to a local account")
        if self.principal_resolver is None:
            raise OpsMeshError("CONFIG_INVALID", "Bot principal resolver is not configured")
        principal = await self.principal_resolver(principal_id)
        if principal.id != principal_id or not principal.active or principal.is_admin:
            raise OpsMeshError(
                "FORBIDDEN", "Shared group identity must be an active non-administrator"
            )
        return principal_id

    async def context(
        self, account: BotAccount, incoming: BotInboundEvent
    ) -> DispatchContext[OpsMeshEvent]:
        event = incoming.event
        actor_id: str | None = None
        route: BotRoute | None = None
        target: ReplyTarget | None = None
        if isinstance(event, BotMessageEvent) and event.type == "message" and not incoming.ignored:
            actor_id = await self._message_principal(account, event)
            await self.authorize(actor_id, "bots.receive", account.id)
            route_record = await self.store.get(
                bot_record_key("route", account.id, event.channel_id)
            )
            if route_record is not None:
                route = BotRoute.model_validate(route_record.data["route"])
            target = ReplyTarget(
                bot=BotRef(adapter_id=account.adapter, bot_id=account.id),
                channel_id=event.channel_id,
                message_id=event.message_id,
            )
        return DispatchContext(
            event=event,
            bot=self.bot_factory(account),
            reply_target=target,
            envelope=EventEnvelope.from_event(event, actor_ref=actor_id),
            values={
                "principal_id": actor_id,
                "bot_id": account.id,
                "agent_revision": route.agent_revision if route else None,
                "bot_dispatch_mode": route.dispatch_mode if route else "direct",
            },
        )

    async def receive(self, account: BotAccount, incoming: BotInboundEvent) -> InboundReceipt:
        context = await self.context(account, incoming)
        event = incoming.event
        fingerprint = hashlib.sha256(
            json.dumps(
                incoming.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        record, claimed = await self.records.claim(
            bot_record_key("inbound", event.id),
            {
                "kind": "inbound",
                "state": "processing",
                "event_id": event.id,
                "fingerprint": fingerprint,
                "bot_id": account.id,
            },
        )
        if not claimed:
            if record.data.get("state") == "processed":
                return InboundReceipt(event_id=event.id, status="duplicate")
            raise OpsMeshError(
                "EVENT_IN_PROGRESS", "Bot event is processing or requires reconciliation"
            )
        if incoming.ignored:
            await self.store.update(
                record.id, {**record.data, "state": "processed"}, record.revision
            )
            return InboundReceipt(event_id=event.id, status="ignored")
        try:
            result = await self.dispatcher(context)
            if result.errors:
                raise OpsMeshError("EVENT_PROCESSING_FAILED", "Bot event handler failed")
        except BaseException:
            await asyncio.shield(
                self.store.update(record.id, {**record.data, "state": "failed"}, record.revision)
            )
            raise
        await self.store.update(record.id, {**record.data, "state": "processed"}, record.revision)
        return InboundReceipt(event_id=event.id, status="processed", matched=result.matched)
