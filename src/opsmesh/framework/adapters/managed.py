"""Bot protocol lifecycle without platform persistence, authorization or Agent routing."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

import httpx

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError

from .adapter import OpsMeshAdapter
from .bot import OpsMeshBot
from .configuration import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotConnectionConfiguration,
)
from .ingress import BotInboundEvent, BotWebhookRequest, BotWebhookResult

BotEventConsumer = Callable[[BotConnectionConfiguration, BotInboundEvent], Awaitable[None]]


class OpsMeshBotAdapter(OpsMeshAdapter, ABC):
    def __init__(
        self,
        driver: OpsMeshDriver,
        client: httpx.AsyncClient,
        resolve_secret: Callable[[str], str],
        *,
        autostart: bool = True,
    ) -> None:
        super().__init__(driver)
        self.client = client
        self.resolve_secret = resolve_secret
        self.autostart = autostart
        self.event_consumer: BotEventConsumer | None = None

    async def emit(
        self, configuration: BotConnectionConfiguration, incoming: BotInboundEvent
    ) -> None:
        self.ensure_enabled()
        if self.event_consumer is None:
            raise OpsMeshError("CONFIG_INVALID", "Bot event consumer has not been configured")
        await self.event_consumer(configuration, incoming)

    @abstractmethod
    def describe(self) -> BotAdapterDescriptor:
        """Advertise actual capabilities and adapter-owned configuration schema."""

    @abstractmethod
    def validate_configuration(self, configuration: BotConnectionConfiguration) -> None:
        """Validate settings and credential names without resolving credentials or doing I/O."""

    @abstractmethod
    def get_bot(self, configuration: BotConnectionConfiguration) -> OpsMeshBot:
        """Return the configured protocol client; never change platform ownership."""

    @abstractmethod
    def configure(self, configuration: BotConnectionConfiguration) -> None:
        """Apply one authorized configuration; background work belongs to the Driver."""

    @abstractmethod
    async def disconnect_account(self, account_id: str) -> None:
        """Drain a connection and remove all account-specific protocol state."""

    @abstractmethod
    def connection_status(
        self, configuration: BotConnectionConfiguration
    ) -> BotAdapterConnectionStatus:
        """Observe current connection state without opening a new connection."""

    async def receive_http(
        self, configuration: BotConnectionConfiguration, request: BotWebhookRequest
    ) -> BotWebhookResult:
        raise OpsMeshError("UNSUPPORTED_FEATURE", "This adapter does not accept HTTP callbacks")

    @abstractmethod
    async def close(self) -> None:
        """Release protocol resources; the injected HTTP client remains owned by its caller."""
