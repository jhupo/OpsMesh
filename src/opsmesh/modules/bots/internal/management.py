"""Persistent robot configuration, external identity and Agent routing administration."""

import asyncio
from collections.abc import Callable
from typing import Literal

from pydantic import ValidationError

from opsmesh.framework.adapters import BotAdapterDescriptor, BotAdapterRegistry, BotRef, ReplyTarget
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.accounts.api import UserPrincipal
from opsmesh.modules.bots.api import (
    AuthorizeBotAction,
    BotAccount,
    BotAgentResolver,
    BotConnectionStatus,
    BotIdentity,
    BotPrincipalResolver,
    BotRoute,
)

from .configuration_store import BotConfigurationStore, BotRelatedSnapshot
from .record_keys import bot_record_key as _key


class BotAccountManagement:
    store: VersionedRecordStore
    authorize: AuthorizeBotAction
    secret_resolver: Callable[[str], str]
    agent_resolver: BotAgentResolver | None
    principal_resolver: BotPrincipalResolver | None
    adapters: BotAdapterRegistry

    async def list_adapters(self, *, actor_id: str) -> tuple[BotAdapterDescriptor, ...]:
        await self.authorize(actor_id, "bots.read", "*")
        return self.adapters.describe()

    async def resolve_delivery_target(
        self,
        bot_id: str,
        channel_id: str,
        *,
        actor_id: str,
        message_id: str | None = None,
        delivery_sequence: int = 1,
    ) -> ReplyTarget:
        await self.authorize(actor_id, "bots.send", bot_id)
        account = await self._account(bot_id)
        return ReplyTarget(
            bot=BotRef(adapter_id=account.adapter, bot_id=bot_id),
            channel_id=channel_id,
            message_id=message_id,
            delivery_sequence=delivery_sequence,
        )

    async def _authorize_credentials(self, account: BotAccount, actor_id: str) -> None:
        references = tuple(dict.fromkeys(account.credentials.values()))
        # Check every reference before resolving any secret or changing a connection.
        for reference in references:
            await self.authorize(actor_id, "secret:use", reference)
        for reference in references:
            self.secret_resolver(reference)

    async def _authorize_identity_target(
        self, principal_id: str, actor_id: str, *, require_active: bool = False
    ) -> UserPrincipal:
        if self.principal_resolver is None:
            raise OpsMeshError("CONFIG_INVALID", "Bot principal resolver is not configured")
        await self.authorize(actor_id, "accounts:bot_identity:manage", principal_id)
        principal = await self.principal_resolver(principal_id)
        if require_active and not principal.active:
            raise OpsMeshError("FORBIDDEN", "Cannot bind a disabled local user")
        if principal.is_admin and not (await self.principal_resolver(actor_id)).is_admin:
            raise OpsMeshError(
                "FORBIDDEN", "Only an administrator can manage administrator identities"
            )
        return principal

    async def _authorize_unlinked_policy(
        self, account: BotAccount, actor_id: str, *, validate: bool = False
    ) -> None:
        principal_id = account.unlinked_principal_id
        if principal_id is None:
            return
        principal = await self._authorize_identity_target(
            principal_id, actor_id, require_active=validate and account.enabled
        )
        if validate:
            if principal.id != principal_id or principal.is_admin:
                raise OpsMeshError("FORBIDDEN", "Shared group identity cannot be an administrator")
            # Disabling a bot remains possible after its shared user's access is revoked.
            # Enabling it rechecks both availability and receive permission.
            if account.enabled:
                await self.authorize(principal_id, "bots.receive", account.id)

    async def _authorized_identity_snapshot(
        self, account: BotAccount, actor_id: str
    ) -> BotRelatedSnapshot:
        await self._authorize_unlinked_policy(account, actor_id)
        records = [
            record
            for record in await self.store.list()
            if record.data.get("kind") == "identity"
            and record.data["identity"]["bot_id"] == account.id
        ]
        for record in records:
            await self._authorize_identity_target(record.data["identity"]["principal_id"], actor_id)
        return {
            record.id: (record.revision, record.data["identity"]["principal_id"])
            for record in records
        }

    @staticmethod
    def _account_view(record: StoredRecord) -> BotAccount:
        data = record.data["account"]
        return BotAccount.model_validate({**data, "revision": record.revision})

    @staticmethod
    def _event(action: str, bot_id: str, actor_id: str) -> list[JsonData]:
        return [{"type": action, "source": "bots", "subject": bot_id, "actor_ref": actor_id}]

    async def _all_accounts(self) -> list[BotAccount]:
        return [
            self._account_view(record)
            for record in await self.store.list()
            if record.data.get("kind") == "account"
        ]

    async def _account(self, bot_id: str, *, require_enabled: bool = True) -> BotAccount:
        record = await self.store.get(_key("account", bot_id))
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Bot account does not exist")
        account = self._account_view(record)
        if require_enabled and not account.enabled:
            raise OpsMeshError("BOT_DISABLED", "Bot account is disabled")
        return account

    async def create_account(self, account: BotAccount, *, actor_id: str) -> BotAccount:
        await self.authorize(actor_id, "bots.manage", account.id)
        account = BotAccount.model_validate(account.model_dump())
        self.adapters.get(account.adapter).validate_configuration(account)
        await self._authorize_unlinked_policy(account, actor_id, validate=True)
        await self._authorize_credentials(account, actor_id)
        record = await self.store.create(
            _key("account", account.id),
            {"kind": "account", "account": account.model_dump(mode="json")},
            events=self._event("bots.account.created", account.id, actor_id),
        )
        account = self._account_view(record)
        self.adapters.get(account.adapter).configure(account)
        return account

    async def get_account(self, bot_id: str, *, actor_id: str) -> BotAccount:
        await self.authorize(actor_id, "bots.read", bot_id)
        return await self._account(bot_id, require_enabled=False)

    async def list_accounts(self, *, actor_id: str) -> list[BotAccount]:
        await self.authorize(actor_id, "bots.read", "*")
        return await self._all_accounts()

    async def update_account(
        self,
        account: BotAccount,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> BotAccount:
        await self.authorize(actor_id, "bots.manage", account.id)
        account = BotAccount.model_validate(account.model_dump())
        previous = await self._account(account.id, require_enabled=False)
        if (account.adapter, account.self_id) != (previous.adapter, previous.self_id):
            raise OpsMeshError(
                "INVALID_REQUEST", "Create a new account to change adapter or bot identity"
            )
        self.adapters.get(account.adapter).validate_configuration(account)
        identities = await self._authorized_identity_snapshot(previous, actor_id)
        await self._authorize_unlinked_policy(account, actor_id, validate=True)
        await self._authorize_credentials(account, actor_id)
        record = await BotConfigurationStore(self.store).replace_account(
            account,
            expected_revision,
            identities,
            self._event("bots.account.updated", account.id, actor_id),
        )
        updated = self._account_view(record)
        await asyncio.shield(self._reload_connection(updated))
        return updated

    async def _reload_connection(self, account: BotAccount) -> None:
        await self.adapters.get(account.adapter).disconnect_account(account.id)
        # Fetch again after draining the old connection: concurrent configuration edits
        # must not resurrect a connection using a stale token or endpoint.
        current = await self._account(account.id, require_enabled=False)
        self.adapters.get(current.adapter).configure(current)

    async def connection_status(self, bot_id: str, *, actor_id: str) -> BotConnectionStatus:
        await self.authorize(actor_id, "bots.read", bot_id)
        account = await self._account(bot_id, require_enabled=False)
        status = self.adapters.get(account.adapter).connection_status(account)
        return BotConnectionStatus(
            bot_id=bot_id,
            revision=account.revision,
            **status.model_dump(),
        )

    async def set_enabled(self, bot_id: str, enabled: bool, *, actor_id: str) -> BotAccount:
        await self.authorize(actor_id, "bots.manage", bot_id)
        record = await self.store.get(_key("account", bot_id))
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Bot account does not exist")
        account = self._account_view(record).model_copy(update={"enabled": enabled})
        return await self.update_account(
            account, expected_revision=record.revision, actor_id=actor_id
        )

    async def bind_identity(
        self,
        bot_id: str,
        external_user_id: str,
        principal_id: str,
        *,
        actor_id: str,
    ) -> BotIdentity:
        await self.authorize(actor_id, "bots.manage", bot_id)
        account = await self._account(bot_id, require_enabled=False)
        configuration = BotConfigurationStore(self.store)
        routes = await configuration.route_snapshot(bot_id)
        await self._authorize_unlinked_policy(account, actor_id)
        await self._authorize_identity_target(principal_id, actor_id, require_active=True)
        previous = await self.store.get(_key("identity", bot_id, external_user_id))
        if previous is not None:
            await self._authorize_identity_target(
                previous.data["identity"]["principal_id"], actor_id
            )
        identity = BotIdentity(
            bot_id=bot_id, external_user_id=external_user_id, principal_id=principal_id
        )
        await configuration.save_identity(
            identity,
            account.revision,
            previous,
            self._event("bots.identity.bound", bot_id, actor_id),
            routes,
        )
        return identity

    async def get_identity(
        self,
        bot_id: str,
        external_user_id: str,
        *,
        actor_id: str,
    ) -> BotIdentity:
        await self.authorize(actor_id, "bots.read", bot_id)
        await self._account(bot_id, require_enabled=False)
        record = await self.store.get(_key("identity", bot_id, external_user_id))
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Bot identity mapping does not exist")
        return BotIdentity.model_validate(record.data["identity"])

    async def list_identities(self, bot_id: str, *, actor_id: str) -> list[BotIdentity]:
        await self.authorize(actor_id, "bots.read", bot_id)
        await self._account(bot_id, require_enabled=False)
        return [
            identity
            for record in await self.store.list()
            if record.data.get("kind") == "identity"
            and (identity := BotIdentity.model_validate(record.data["identity"])).bot_id == bot_id
        ]

    async def unbind_identity(self, bot_id: str, external_user_id: str, *, actor_id: str) -> None:
        await self.authorize(actor_id, "bots.manage", bot_id)
        account = await self._account(bot_id, require_enabled=False)
        record = await self.store.get(_key("identity", bot_id, external_user_id))
        if record is not None:
            await self._authorize_unlinked_policy(account, actor_id)
            await self._authorize_identity_target(record.data["identity"]["principal_id"], actor_id)
            await BotConfigurationStore(self.store).delete_identity(
                bot_id,
                account.revision,
                record,
                self._event("bots.identity.unbound", bot_id, actor_id),
            )

    async def bind_route(
        self,
        bot_id: str,
        channel_id: str,
        agent_revision: str,
        *,
        actor_id: str,
        dispatch_mode: Literal["direct", "managed"] = "direct",
    ) -> BotRoute:
        await self.authorize(actor_id, "bots.manage", bot_id)
        account = await self._account(bot_id, require_enabled=False)
        try:
            BotRoute(
                bot_id=bot_id,
                channel_id=channel_id,
                agent_revision=agent_revision,
                dispatch_mode=dispatch_mode,
            )
        except ValidationError as error:
            raise OpsMeshError("INVALID_REQUEST", "Invalid Bot Agent binding") from error
        if self.agent_resolver is None:
            raise OpsMeshError("CONFIG_INVALID", "Bot Agent resolver is not configured")
        identities = await self._authorized_identity_snapshot(account, actor_id)
        previous = await self.store.get(_key("route", bot_id, channel_id))
        specification = await self.agent_resolver(agent_revision)
        await self.authorize(actor_id, "agent:run", specification.ref)
        route = BotRoute(
            bot_id=bot_id,
            channel_id=channel_id,
            agent_revision=specification.ref,
            dispatch_mode=dispatch_mode,
        )
        await BotConfigurationStore(self.store).save_route(
            route,
            account.revision,
            previous,
            identities,
            self._event("bots.route.bound", bot_id, actor_id),
        )
        return route

    async def get_route(self, bot_id: str, channel_id: str, *, actor_id: str) -> BotRoute:
        await self.authorize(actor_id, "bots.read", bot_id)
        await self._account(bot_id, require_enabled=False)
        record = await self.store.get(_key("route", bot_id, channel_id))
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Bot Agent binding does not exist")
        return BotRoute.model_validate(record.data["route"])

    async def list_routes(self, bot_id: str, *, actor_id: str) -> list[BotRoute]:
        await self.authorize(actor_id, "bots.read", bot_id)
        await self._account(bot_id, require_enabled=False)
        return [
            route
            for record in await self.store.list()
            if record.data.get("kind") == "route"
            and (route := BotRoute.model_validate(record.data["route"])).bot_id == bot_id
        ]

    async def unbind_route(self, bot_id: str, channel_id: str, *, actor_id: str) -> None:
        """Stop routing future events; existing Runs and accepted events remain unchanged."""
        await self.authorize(actor_id, "bots.manage", bot_id)
        account = await self._account(bot_id, require_enabled=False)
        record = await self.store.get(_key("route", bot_id, channel_id))
        if record is not None:
            identities = await self._authorized_identity_snapshot(account, actor_id)
            await BotConfigurationStore(self.store).delete_route(
                bot_id,
                account.revision,
                record,
                identities,
                self._event("bots.route.unbound", bot_id, actor_id),
            )
