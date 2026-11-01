"""Public bot account, identity, routing and delivery contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator

from opsmesh.framework.adapters import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotConnectionConfiguration,
    BotWebhookResponse,
    OpsMeshMessage,
    ReplyTarget,
)
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.events import DispatchContext, OpsMeshEvent
from opsmesh.framework.message import DispatchResult
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.accounts.api import UserPrincipal

AuthorizeBotAction = Callable[[str, str, str], Awaitable[None]]
BotEventDispatcher = Callable[[DispatchContext[OpsMeshEvent]], Awaitable[DispatchResult]]
BotAgentResolver = Callable[[str], Awaitable[AgentSpecification]]
BotPrincipalResolver = Callable[[str], Awaitable[UserPrincipal]]


class BotAccount(BotConnectionConfiguration):
    """A persisted platform account using one independently registered bot adapter."""

    unlinked_principal_id: str | None = Field(default=None, min_length=1, max_length=256)
    unlinked_channels: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unlinked_policy(self) -> Self:
        """Unknown users may share one delegated identity in explicitly listed groups."""
        if bool(self.unlinked_principal_id) != bool(self.unlinked_channels):
            raise ValueError(
                "An unlinked identity and its allowed groups must be configured together"
            )
        if self.unlinked_principal_id is not None and any(
            character.isspace() or not character.isprintable()
            for character in self.unlinked_principal_id
        ):
            raise ValueError(
                "Unlinked principal ID must not contain whitespace or control characters"
            )
        if len(set(self.unlinked_channels)) != len(self.unlinked_channels):
            raise ValueError("Unlinked group channels must be unique")
        for channel in self.unlinked_channels:
            if (
                not channel.startswith("group:")
                or len(channel) <= len("group:")
                or len(channel) > 256
                or any(
                    character in "*?[]" or character.isspace() or not character.isprintable()
                    for character in channel
                )
            ):
                raise ValueError(
                    "Unlinked channels must be exact group:<id> values without wildcards"
                )
        return self


class BotIdentity(OpsMeshContract):
    bot_id: str = Field(min_length=1, max_length=100)
    external_user_id: str = Field(min_length=1, max_length=256)
    principal_id: str = Field(min_length=1, max_length=256)


class BotConnectionStatus(BotAdapterConnectionStatus):
    bot_id: str
    revision: int


class BotRoute(OpsMeshContract):
    """An exact conversation bound to a published Agent.

    Managed routes require an event handler to accept and stop propagation; they
    never fall back to direct runtime submission when that handler is absent.
    """

    bot_id: str = Field(min_length=1, max_length=100)
    channel_id: str = Field(min_length=1, max_length=256)
    agent_revision: str = Field(min_length=1, max_length=256)
    dispatch_mode: Literal["direct", "managed"] = "direct"

    @field_validator("channel_id")
    @classmethod
    def valid_channel_id(cls, value: str) -> str:
        if not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Bot route channel must contain printable characters")
        return value


class InboundReceipt(OpsMeshContract):
    event_id: str
    status: Literal["processed", "duplicate", "ignored"]
    matched: int = 0


@dataclass(frozen=True)
class BotWebhookReceipt:
    response: BotWebhookResponse
    receipt: InboundReceipt | None = None


class DeliveryReceipt(OpsMeshContract):
    delivery_id: str
    message_id: str
    duplicate: bool = False


class FileDeliveryReceipt(OpsMeshContract):
    delivery_id: str
    file_id: str | None = None
    duplicate: bool = False


class BotAccounts(Protocol):
    async def list_adapters(self, *, actor_id: str) -> tuple[BotAdapterDescriptor, ...]: ...
    async def resolve_delivery_target(
        self,
        bot_id: str,
        channel_id: str,
        *,
        actor_id: str,
        message_id: str | None = None,
        delivery_sequence: int = 1,
    ) -> ReplyTarget: ...
    async def create_account(self, account: BotAccount, *, actor_id: str) -> BotAccount: ...
    async def get_account(self, bot_id: str, *, actor_id: str) -> BotAccount: ...
    async def list_accounts(self, *, actor_id: str) -> list[BotAccount]: ...
    async def set_enabled(self, bot_id: str, enabled: bool, *, actor_id: str) -> BotAccount: ...
    async def update_account(
        self,
        account: BotAccount,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> BotAccount: ...
    async def connection_status(self, bot_id: str, *, actor_id: str) -> BotConnectionStatus: ...
    async def list_identities(self, bot_id: str, *, actor_id: str) -> list[BotIdentity]: ...
    async def get_identity(
        self,
        bot_id: str,
        external_user_id: str,
        *,
        actor_id: str,
    ) -> BotIdentity: ...
    async def unbind_identity(
        self,
        bot_id: str,
        external_user_id: str,
        *,
        actor_id: str,
    ) -> None: ...
    async def bind_identity(
        self,
        bot_id: str,
        external_user_id: str,
        principal_id: str,
        *,
        actor_id: str,
    ) -> BotIdentity: ...
    async def bind_route(
        self,
        bot_id: str,
        channel_id: str,
        agent_revision: str,
        *,
        actor_id: str,
        dispatch_mode: Literal["direct", "managed"] = "direct",
    ) -> BotRoute: ...
    async def get_route(self, bot_id: str, channel_id: str, *, actor_id: str) -> BotRoute: ...
    async def list_routes(self, bot_id: str, *, actor_id: str) -> list[BotRoute]: ...
    async def unbind_route(self, bot_id: str, channel_id: str, *, actor_id: str) -> None: ...


class MessageDelivery(Protocol):
    async def send(
        self,
        target: ReplyTarget,
        message: OpsMeshMessage,
        *,
        idempotency_key: str,
        actor_id: str,
    ) -> DeliveryReceipt: ...
    async def upload_file(
        self,
        target: ReplyTarget,
        name: str,
        content: bytes,
        *,
        idempotency_key: str,
        actor_id: str,
    ) -> FileDeliveryReceipt: ...
