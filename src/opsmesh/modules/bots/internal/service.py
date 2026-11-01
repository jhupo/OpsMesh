"""Bot platform orchestration over independently registered protocol implementations."""

from collections.abc import Callable

from opsmesh.framework.adapters import (
    BotAdapterRegistry,
    BotConnectionConfiguration,
    BotInboundEvent,
    BotWebhookRequest,
    OpsMeshBot,
    OpsMeshMessage,
    ReplyTarget,
)
from opsmesh.framework.drivers import DriverState
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.bots.api import (
    AuthorizeBotAction,
    BotAccount,
    BotAgentResolver,
    BotEventDispatcher,
    BotPrincipalResolver,
    BotWebhookReceipt,
    DeliveryReceipt,
    FileDeliveryReceipt,
)

from .delivery import BotMessageDelivery
from .ingress import BotEventIngress
from .management import BotAccountManagement


class BotService(BotAccountManagement):
    def __init__(
        self,
        store: VersionedRecordStore,
        secret_resolver: Callable[[str], str],
        authorize: AuthorizeBotAction,
        dispatcher: BotEventDispatcher,
        *,
        adapters: BotAdapterRegistry,
        agent_resolver: BotAgentResolver | None = None,
        principal_resolver: BotPrincipalResolver | None = None,
    ) -> None:
        self.store, self.secret_resolver = store, secret_resolver
        self.authorize = authorize
        self.agent_resolver, self.principal_resolver = agent_resolver, principal_resolver
        self.driver = adapters.driver
        self.adapters = adapters
        self.ingress = BotEventIngress(store, authorize, dispatcher, self._bot)
        self.delivery = BotMessageDelivery(store, authorize, self._account, self._bot)
        self.adapters.bind_receiver(self.receive_adapter_event)
        if self.driver.state == DriverState.NEW:
            self.driver.on_startup(self.start)
            self.driver.on_shutdown(self.close)

    async def start(self) -> None:
        reenabled = await self.adapters.synchronize()
        for account in await self._all_accounts():
            try:
                adapter = self.adapters.get(account.adapter)
            except OpsMeshError as error:
                if error.code == "PLUGIN_DISABLED":
                    continue
                raise
            adapter.validate_configuration(account)
            adapter.configure(account)
        self.adapters.acknowledge_reconfigured(reenabled)

    async def synchronize_adapters(self) -> None:
        reenabled = await self.adapters.synchronize()
        if reenabled:
            accounts = await self._all_accounts()
            for name in reenabled:
                adapter = self.adapters.get(name)
                for account in accounts:
                    if account.adapter == name:
                        adapter.ensure_enabled()
                        adapter.validate_configuration(account)
                        adapter.configure(account)
                self.adapters.acknowledge_reconfigured((name,))

    async def close(self) -> None:
        await self.adapters.close()

    def _bot(self, account: BotAccount) -> OpsMeshBot:
        return self.adapters.get(account.adapter).get_bot(account)

    async def receive_webhook(self, bot_id: str, request: BotWebhookRequest) -> BotWebhookReceipt:
        if len(request.body) > 1024 * 1024:
            raise OpsMeshError("INVALID_REQUEST", "Bot event exceeds the 1 MiB input limit")
        account = await self._account(bot_id)
        result = await self.adapters.get(account.adapter).receive_http(account, request)
        receipt = None
        if result.incoming is not None:
            current = await self._current_connection(account)
            receipt = await self.ingress.receive(current, result.incoming)
        return BotWebhookReceipt(result.response, receipt)

    async def _current_connection(self, configuration: BotConnectionConfiguration) -> BotAccount:
        current = await self._account(configuration.id)
        if (current.adapter, current.self_id, current.revision) != (
            configuration.adapter,
            configuration.self_id,
            configuration.revision,
        ):
            raise OpsMeshError("CONFLICT", "Bot configuration changed while receiving this event")
        return current

    async def receive_adapter_event(
        self, configuration: BotConnectionConfiguration, incoming: BotInboundEvent
    ) -> None:
        account = await self._current_connection(configuration)
        await self.ingress.receive(account, incoming)

    async def send(
        self,
        target: ReplyTarget,
        message: OpsMeshMessage,
        *,
        idempotency_key: str,
        actor_id: str,
    ) -> DeliveryReceipt:
        return await self.delivery.send(
            target, message, idempotency_key=idempotency_key, actor_id=actor_id
        )

    async def upload_file(
        self,
        target: ReplyTarget,
        name: str,
        content: bytes,
        *,
        idempotency_key: str,
        actor_id: str,
    ) -> FileDeliveryReceipt:
        return await self.delivery.upload_file(
            target, name, content, idempotency_key=idempotency_key, actor_id=actor_id
        )
