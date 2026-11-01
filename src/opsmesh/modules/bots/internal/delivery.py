"""Authorized outbound delivery and durable, conservative receipt handling."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from opsmesh.framework.adapters import OpsMeshBot, OpsMeshMessage, ReplyTarget
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.bots.api import (
    AuthorizeBotAction,
    BotAccount,
    DeliveryReceipt,
    FileDeliveryReceipt,
)

from .delivery_outcomes import BotDeliveryOutcomes
from .delivery_records import BotDeliveryRecords
from .record_keys import bot_record_key as _key


def _fingerprint(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class BotMessageDelivery:
    def __init__(
        self,
        store: VersionedRecordStore,
        authorize: AuthorizeBotAction,
        account_loader: Callable[[str], Awaitable[BotAccount]],
        bot_factory: Callable[[BotAccount], OpsMeshBot],
    ) -> None:
        self.store, self.authorize = store, authorize
        self._account, self._bot = account_loader, bot_factory
        self.records = BotDeliveryRecords(store)
        self.outcomes = BotDeliveryOutcomes(store)

    async def send(
        self,
        target: ReplyTarget,
        message: OpsMeshMessage,
        *,
        idempotency_key: str,
        actor_id: str,
    ) -> DeliveryReceipt:
        if not idempotency_key:
            raise OpsMeshError("INVALID_REQUEST", "Message delivery needs an idempotency key")
        await self.authorize(actor_id, "bots.send", target.bot.bot_id)
        account = await self._account(target.bot.bot_id)
        if target.bot.adapter_id != account.adapter:
            raise OpsMeshError("INVALID_REQUEST", "Target adapter differs from account adapter")
        delivery_id = _key("delivery", account.id, idempotency_key)
        data = {
            "kind": "delivery",
            "state": "sending",
            "bot_id": account.id,
            "fingerprint": _fingerprint(
                {
                    "target": target.model_dump(mode="json"),
                    "message": message.model_dump(mode="json"),
                    "actor_id": actor_id,
                }
            ),
        }
        record, claimed = await self.records.claim(delivery_id, data)
        if not claimed:
            if record.data.get("state") == "sent":
                return DeliveryReceipt(
                    delivery_id=delivery_id,
                    message_id=str(record.data["message_id"]),
                    duplicate=True,
                )
            self.outcomes.raise_previous_failure(record)
        try:
            message_id = await self._bot(account).send(target, message)
        except BaseException as exc:
            await asyncio.shield(self.outcomes.record_failure(record, exc))
            self.outcomes.raise_current_failure(exc)
        await self.store.update(
            record.id, {**record.data, "state": "sent", "message_id": message_id}, record.revision
        )
        return DeliveryReceipt(delivery_id=delivery_id, message_id=message_id)

    async def upload_file(
        self,
        target: ReplyTarget,
        name: str,
        content: bytes,
        *,
        idempotency_key: str,
        actor_id: str,
    ) -> FileDeliveryReceipt:
        if (
            not idempotency_key
            or not name
            or name in {".", ".."}
            or len(name) > 255
            or any(character in name for character in "/\\\x00")
            or any(ord(character) < 32 for character in name)
            or len(content) > 16 * 1024 * 1024
        ):
            raise OpsMeshError(
                "INVALID_REQUEST", "File upload needs a safe name, key and at most 16 MiB"
            )
        await self.authorize(actor_id, "bots.send", target.bot.bot_id)
        account = await self._account(target.bot.bot_id)
        if target.bot.adapter_id != account.adapter or target.thread_id:
            raise OpsMeshError("INVALID_REQUEST", "File target is incompatible with the bot")
        delivery_id = _key("file_delivery", account.id, idempotency_key)
        record, claimed = await self.records.claim(
            delivery_id,
            {
                "kind": "file_delivery",
                "state": "sending",
                "bot_id": account.id,
                "fingerprint": _fingerprint(
                    {
                        "target": target.model_dump(),
                        "name": name,
                        "digest": hashlib.sha256(content).hexdigest(),
                        "actor": actor_id,
                    }
                ),
            },
        )
        if not claimed:
            if record.data.get("state") == "sent":
                return FileDeliveryReceipt(
                    delivery_id=delivery_id, file_id=record.data.get("file_id"), duplicate=True
                )
            self.outcomes.raise_previous_failure(record)
        try:
            file_id = await self._bot(account).upload_file(target, name, content)
        except BaseException as error:
            await asyncio.shield(self.outcomes.record_failure(record, error))
            self.outcomes.raise_current_failure(error)
        await self.store.update(
            record.id, {**record.data, "state": "sent", "file_id": file_id}, record.revision
        )
        return FileDeliveryReceipt(delivery_id=delivery_id, file_id=file_id)
