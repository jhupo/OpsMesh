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
    ) -> None:
        self.store, self.authorize, self.dispatcher = store, authorize, dispatcher
        self.bot_factory = bot_factory
        self.records = BotDeliveryRecords(store)

    async def context(
        self, account: BotAccount, incoming: BotInboundEvent
    ) -> DispatchContext[OpsMeshEvent]:
        event = incoming.event
        actor_id: str | None = None
        route: BotRoute | None = None
        target: ReplyTarget | None = None
        if isinstance(event, BotMessageEvent) and event.type == "message" and not incoming.ignored:
            record = await self.store.get(bot_record_key("identity", account.id, event.user_id))
            if record is None:
                raise OpsMeshError(
                    "FORBIDDEN", "External bot user is not linked to a local account"
                )
            identity = BotIdentity.model_validate(record.data["identity"])
            actor_id = identity.principal_id
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
