"""Bot administration preserves routing and invalidates obsolete live connections."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from opsmesh.framework.errors import OpsMeshError

from . import test_onebot as helpers
from .helpers import create_bot_service as BotService
from .helpers import onebot_adapter, receive_onebot
from .test_onebot import (
    account,
    authorize,
    ignore,
    resolve_agent,
    resolve_principal,
    secret,
    webhook,
)

store = helpers.store


async def test_configuration_cas_and_connection_cleanup_preserve_bindings(store, monkeypatch):
    async with httpx.AsyncClient() as client:
        service = BotService(
            store,
            secret,
            client,
            authorize,
            ignore,
            agent_resolver=resolve_agent,
            principal_resolver=resolve_principal,
        )
        original = await service.create_account(account(), actor_id="admin")
        await service.bind_identity(original.id, "200", "user", actor_id="admin")
        route = await service.bind_route(
            original.id, "private:200", "assistant@2", actor_id="admin"
        )
        connection = Mock(closed=False, pending={}, socket=Mock(close=AsyncMock()))
        onebot_adapter(service).connections.sessions[original.id] = connection
        connect = Mock()
        monkeypatch.setattr(onebot_adapter(service).connections, "connect", connect)
        updated = await service.update_account(
            original.model_copy(
                update={"settings": {**original.settings, "api_base_url": "http://new.test"}}
            ),
            expected_revision=1,
            actor_id="admin",
        )
        assert updated.revision == 2
        connection.socket.close.assert_awaited_once_with(1001)
        assert connection.closed
        assert original.id not in onebot_adapter(service).connections.sessions
        connect.assert_called_once_with(updated)
        assert await service.get_route(original.id, "private:200", actor_id="admin") == route
        assert len(await service.list_identities(original.id, actor_id="admin")) == 1
        with pytest.raises(OpsMeshError) as conflict:
            await service.update_account(original, expected_revision=1, actor_id="admin")
        assert conflict.value.code == "CONFLICT"
        assert (await service.get_account(original.id, actor_id="admin")).settings[
            "api_base_url"
        ] == "http://new.test"
        assert (
            await service.connection_status(original.id, actor_id="admin")
        ).state == "configured"
        await service.set_enabled(original.id, False, actor_id="admin")
        assert (await service.connection_status(original.id, actor_id="admin")).state == "disabled"


async def test_identity_validation_unbind_and_direct_service_permissions(store):
    async with httpx.AsyncClient() as client:
        service = BotService(
            store, secret, client, authorize, ignore, principal_resolver=resolve_principal
        )
        await service.create_account(account(), actor_id="admin")
        with pytest.raises(OpsMeshError) as missing:
            await service.bind_identity("support", "200", "nonexistent", actor_id="admin")
        assert missing.value.code == "NOT_FOUND"
        identity = await service.bind_identity("support", "200", "user", actor_id="admin")
        assert await service.get_identity("support", "200", actor_id="admin") == identity
        for call in (
            service.update_account(account(), expected_revision=1, actor_id="stranger"),
            service.connection_status("support", actor_id="stranger"),
            service.list_identities("support", actor_id="stranger"),
            service.get_identity("support", "200", actor_id="stranger"),
            service.unbind_identity("support", "200", actor_id="stranger"),
        ):
            with pytest.raises(OpsMeshError) as denied:
                await call
            assert denied.value.code == "FORBIDDEN"
        await service.unbind_identity("support", "200", actor_id="admin")
        assert await service.list_identities("support", actor_id="admin") == []
        body, signature = webhook()
        with pytest.raises(OpsMeshError) as unbound:
            await receive_onebot(service, "support", body, signature=signature)
        assert unbound.value.code == "FORBIDDEN"
        events = await store.database.pending_events()
        assert any(
            event.get("type") == "bots.identity.unbound" and event.get("actor_ref") == "admin"
            for event in events
        )


async def test_identity_binding_requires_account_directory(store):
    async with httpx.AsyncClient() as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(account(), actor_id="admin")
        with pytest.raises(OpsMeshError) as unavailable:
            await service.bind_identity("support", "200", "user", actor_id="admin")
        assert unavailable.value.code == "CONFIG_INVALID"
