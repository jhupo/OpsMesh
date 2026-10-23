"""Process-local connections coordinated by durable per-channel leases."""

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

from opsmesh_sdk.channels import ChannelAdapter, DeliveryRejected, InboundMessage

from opsmesh.gateway.channels.bindings import Bindings
from opsmesh.gateway.channels.leases import ChannelLeases
from opsmesh.gateway.channels.registry import ChannelRegistry
from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.messaging.dispatcher import Dispatcher
from opsmesh.gateway.messaging.filters import MessageFilters
from opsmesh.gateway.messaging.inbox import Inbox
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.ports import ExecutionPort
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, DomainError, InvalidArgumentError

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(
        self,
        *,
        registry: ChannelRegistry,
        store: GatewayStore,
        media_root: Path,
        execution: ExecutionPort,
        filters: MessageFilters | None = None,
    ) -> None:
        self.registry, self.store, self.media_root, self.execution = (
            registry,
            store,
            media_root,
            execution,
        )
        self.leases = ChannelLeases(store)
        self.inbox, self.outbox = Inbox(store), Outbox(store)
        self.dispatcher = Dispatcher(self.inbox, self.outbox, execution, media_root, filters)
        self.tasks: dict[UUID, tuple[ChannelDefinition, asyncio.Task[None]]] = {}
        self.supervisor: asyncio.Task[None] | None = None

    async def save(self, value: ChannelDefinition, actor_id: UUID) -> ChannelDefinition:
        self.registry.validate(value)
        return await asyncio.to_thread(self.store.save, value, actor_id)

    async def delete(self, channel_id: UUID, actor_id: UUID) -> None:
        await asyncio.to_thread(self.store.delete, channel_id, actor_id)

    async def probe(self, channel_id: UUID) -> dict[str, object]:
        definition = await asyncio.to_thread(self.store.get, channel_id)
        info = self.registry.infos.get(definition.kind)
        if info is None or not info.capabilities.probe:
            raise InvalidArgumentError("The channel does not support a connection probe")

        async def reject(message: InboundMessage) -> None:
            raise RuntimeError("A connection probe cannot accept incoming messages")

        adapter = self.registry.create(
            definition, reject, GatewayMedia(self.media_root / str(channel_id))
        )
        try:
            async with asyncio.timeout(15):
                return await adapter.probe()
        except Exception as error:
            raise ConflictError("Channel authentication probe failed") from error
        finally:
            await adapter.close()

    async def replace_registry(self, registry: ChannelRegistry) -> None:
        previous = self.registry
        self.registry = registry
        changed = {
            kind
            for kind in previous.versions.keys() | registry.versions.keys()
            if previous.versions.get(kind) != registry.versions.get(kind)
        }
        for identifier, (definition, task) in list(self.tasks.items()):
            if definition.kind in changed:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.tasks.pop(identifier)

    async def start(self) -> None:
        if self.supervisor is None:
            self.supervisor = asyncio.create_task(self.run(), name="gateway-supervisor")

    async def stop(self) -> None:
        if self.supervisor:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
            self.supervisor = None
        for _, task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*(task for _, task in self.tasks.values()), return_exceptions=True)
        self.tasks.clear()

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(Bindings(self.store).expire)
                definitions = {
                    row.id: row
                    for row in await asyncio.to_thread(self.store.list_channels)
                    if row.enabled
                }
                for identifier, (definition, task) in list(self.tasks.items()):
                    if definitions.get(identifier) != definition or task.done():
                        if task.done() and not task.cancelled() and task.exception() is not None:
                            logger.warning(
                                "Gateway channel %s stopped: %s",
                                identifier,
                                type(task.exception()).__name__,
                            )
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        self.tasks.pop(identifier)
                for identifier, definition in definitions.items():
                    if identifier not in self.tasks:
                        task = asyncio.create_task(
                            self.channel(definition), name=f"gateway-{identifier}"
                        )
                        self.tasks[identifier] = definition, task
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Gateway reconciliation failed: %s", type(error).__name__)
            await asyncio.sleep(2)

    async def channel(self, definition: ChannelDefinition) -> None:
        owner = uuid4().hex
        if not await asyncio.to_thread(self.leases.acquire, definition.id, owner):
            return
        adapter: ChannelAdapter | None = None
        try:
            media = GatewayMedia(self.media_root / str(definition.id))
            dispatch_wakeup, delivery_wakeup = asyncio.Event(), asyncio.Event()

            async def receive(message: InboundMessage) -> None:
                await asyncio.to_thread(self.inbox.receive, definition.id, owner, message)
                dispatch_wakeup.set()

            adapter = self.registry.create(definition, receive, media)
            await asyncio.to_thread(self.outbox.recover, definition.id, owner)

            async def renew() -> None:
                while True:
                    await asyncio.sleep(10)
                    if not await asyncio.to_thread(self.leases.acquire, definition.id, owner):
                        raise RuntimeError("Gateway lease lost")

            async def dispatch() -> None:
                running: dict[UUID, asyncio.Task[None]] = {}

                async def process(inbox_id: UUID) -> None:
                    try:
                        await self.dispatcher.process(inbox_id, owner)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        logger.warning(
                            "Gateway event pending after error: %s", type(error).__name__
                        )
                    finally:
                        delivery_wakeup.set()

                try:
                    while True:
                        dispatch_wakeup.clear()
                        for identifier, task in list(running.items()):
                            if task.done():
                                task.result()
                                del running[identifier]
                        pending = await asyncio.to_thread(self.inbox.pending, definition.id)
                        for identifier in pending:
                            if len(running) >= definition.policy.workers:
                                break
                            if identifier not in running:
                                running[identifier] = asyncio.create_task(process(identifier))
                        with suppress(TimeoutError):
                            await asyncio.wait_for(
                                dispatch_wakeup.wait(), timeout=0.2 if pending or running else 1
                            )
                finally:
                    for task in running.values():
                        task.cancel()
                    await asyncio.gather(*running.values(), return_exceptions=True)

            async def connect() -> None:
                assert adapter is not None
                await adapter.run()
                raise RuntimeError("Channel connection stopped")

            async with asyncio.TaskGroup() as group:
                group.create_task(connect())
                group.create_task(renew())
                group.create_task(dispatch())
                group.create_task(self.deliver(definition, owner, adapter, delivery_wakeup))
        finally:
            if adapter is not None:
                with suppress(Exception):
                    await adapter.close()
            await asyncio.to_thread(self.leases.release, definition.id, owner)

    async def deliver(
        self,
        definition: ChannelDefinition,
        owner: str,
        adapter: ChannelAdapter,
        wakeup: asyncio.Event,
    ) -> None:
        channel_id = definition.id
        while True:
            wakeup.clear()
            message = await asyncio.to_thread(
                self.outbox.next,
                channel_id,
                owner,
                send_limit=definition.policy.send_limit,
                send_window_seconds=definition.policy.send_window_seconds,
            )
            if message is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(wakeup.wait(), timeout=1)
                continue
            try:
                turn = await asyncio.to_thread(
                    self.outbox.delivery_turn, message.delivery_id, owner
                )
                if turn:
                    await self.execution.authorize(turn.user_id, turn.route)
            except DomainError as error:
                await asyncio.to_thread(
                    self.outbox.finish,
                    channel_id,
                    owner,
                    message.delivery_id,
                    "blocked",
                    "",
                    type(error).__name__,
                )
                continue
            try:
                async with asyncio.timeout(definition.policy.send_timeout_seconds):
                    if message.event == "recall" and not adapter.capabilities.recall:
                        raise DeliveryRejected("This channel does not support message recall")
                    if message.event == "recall" and (
                        turn is None or not turn.policy.group_for(turn.message).allow_recall
                    ):
                        raise DeliveryRejected("Message recall permission was revoked")
                    receipt = await adapter.send(message)
            except asyncio.CancelledError:
                raise
            except DeliveryRejected as error:
                status = "pending" if error.retryable else "failed"
                await asyncio.to_thread(
                    self.outbox.finish,
                    channel_id,
                    owner,
                    message.delivery_id,
                    status,
                    "",
                    type(error).__name__,
                )
                if error.retryable:
                    await asyncio.sleep(10)
            except Exception as error:
                await asyncio.to_thread(
                    self.outbox.finish,
                    channel_id,
                    owner,
                    message.delivery_id,
                    "uncertain",
                    "",
                    type(error).__name__,
                )
            else:
                await asyncio.to_thread(
                    self.outbox.finish,
                    channel_id,
                    owner,
                    message.delivery_id,
                    "sent",
                    receipt.remote_id,
                )
