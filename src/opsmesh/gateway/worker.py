import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from opsmesh.gateway.adapters.base import Adapter
from opsmesh.gateway.adapters.schemas import Event
from opsmesh.infra.database import new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import load
from opsmesh.plugins.registry import PluginRegistry

from .adapters.dingtalk.adapter import DingTalkAdapter
from .adapters.qq.adapter import QQAdapter
from .delivery import deliver
from .models import ChannelConfiguration, ChannelOperation, OutboundDelivery
from .operations import execute_pending
from .service import receive

logger = logging.getLogger(__name__)


class GatewayWorker:
    def __init__(
        self,
        sessions: sessionmaker,
        cipher: SecretCipher,
        registry: PluginRegistry,
        root: Path,
        lease_seconds: int = 90,
    ):
        self.sessions, self.cipher, self.registry, self.root = sessions, cipher, registry, root
        self.lease_seconds, self.worker_id = lease_seconds, new_id()
        self.connections: dict[str, tuple[Adapter, asyncio.Task[None], int]] = {}

    async def serve(self) -> None:
        try:
            while True:
                try:
                    await self.reconcile()
                except Exception:
                    logger.exception("gateway_reconcile_failed")
                await asyncio.sleep(2)
        finally:
            for channel_id in list(self.connections):
                await self.stop(channel_id)

    async def reconcile(self) -> None:
        with self.sessions.begin() as db:
            channels = list(
                db.scalars(
                    select(ChannelConfiguration).where(ChannelConfiguration.enabled.is_(True))
                )
            )
        enabled = {row.id for row in channels}
        for channel_id in list(self.connections):
            if channel_id not in enabled:
                await self.stop(channel_id)
        for row in channels:
            current = self.connections.get(row.id)
            if current and (current[1].done() or current[2] != row.revision):
                await self.stop(row.id)
                current = None
            with self.sessions.begin() as db:
                live = db.get(ChannelConfiguration, row.id, with_for_update=True)
                if live is None or not live.enabled:
                    continue
                if (
                    live.worker_id != self.worker_id
                    and live.lease_until is not None
                    and live.lease_until > now()
                ):
                    acquired = False
                else:
                    acquired = True
                    live.worker_id, live.lease_until = (
                        self.worker_id,
                        now() + timedelta(seconds=self.lease_seconds),
                    )
                    if current is None:
                        db.execute(
                            update(OutboundDelivery)
                            .where(
                                OutboundDelivery.channel_id == row.id,
                                OutboundDelivery.status == "sending",
                            )
                            .values(status="uncertain", error_code="delivery_worker_lost")
                        )
                        db.execute(
                            update(ChannelOperation)
                            .where(
                                ChannelOperation.channel_id == row.id,
                                ChannelOperation.status == "running",
                            )
                            .values(
                                status="uncertain",
                                error_code="operation_worker_lost",
                                finished_at=now(),
                            )
                        )
            if not acquired:
                if current:
                    await self.stop(row.id)
                continue
            if current is None:
                try:
                    adapter = self.adapter(row)
                    with self.sessions.begin() as db:
                        live = db.get(ChannelConfiguration, row.id, with_for_update=True)
                        if live is not None and live.worker_id == self.worker_id:
                            live.capabilities = adapter.capabilities.model_dump(mode="json")
                    task = asyncio.create_task(self.run_channel(row.id, adapter))
                    self.connections[row.id] = adapter, task, row.revision
                except Exception:
                    logger.exception("gateway_adapter_load_failed")

    def adapter(self, row: ChannelConfiguration) -> Adapter:
        config = self.cipher.decrypt(row.encrypted_config)
        if row.plugin_id:
            with self.sessions.begin() as db:
                plugin = load(db, self.root, self.registry, row.plugin_id)
                adapter = plugin.channels[row.adapter](config)
                if not isinstance(adapter, Adapter):
                    raise TypeError("Channel factory must return Adapter")
                return adapter
        factories: dict[str, Callable[..., Adapter]] = {
            "qq": QQAdapter,
            "dingtalk": DingTalkAdapter,
        }
        return factories[row.adapter](config)

    async def run_channel(self, channel_id: str, adapter: Adapter) -> None:
        async def sink(message: Event) -> None:
            with self.sessions.begin() as db:
                row = db.get(ChannelConfiguration, channel_id, with_for_update=True)
                if (
                    row is None
                    or row.worker_id != self.worker_id
                    or row.lease_until is None
                    or row.lease_until <= now()
                ):
                    raise RuntimeError("Channel lease lost")
                receive(db, self.cipher, channel_id, message)

        connection = asyncio.create_task(adapter.run(sink))
        sender = asyncio.create_task(
            deliver(self.sessions, self.cipher, channel_id, adapter, self.worker_id)
        )
        operations = asyncio.create_task(
            execute_pending(self.sessions, self.cipher, channel_id, adapter, self.worker_id)
        )
        try:
            done, _ = await asyncio.wait(
                {connection, sender, operations}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                await task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gateway_channel_failed")
        finally:
            sender.cancel()
            operations.cancel()
            await asyncio.gather(sender, operations, return_exceptions=True)
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)

    async def stop(self, channel_id: str) -> None:
        connection = self.connections.pop(channel_id, None)
        if connection:
            connection[1].cancel()
            await asyncio.gather(connection[1], return_exceptions=True)
        with self.sessions.begin() as db:
            row = db.get(ChannelConfiguration, channel_id, with_for_update=True)
            if row is not None and row.worker_id == self.worker_id:
                row.worker_id, row.lease_until = None, None
