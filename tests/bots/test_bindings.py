"""Agent bindings are durable platform data, independent of command plugins."""

import asyncio

import httpx
import pytest

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult

from . import fixtures as helpers
from .fixtures import account, authorize, ignore, resolve_principal, secret, webhook
from .helpers import create_bot_service as BotService
from .helpers import receive_qq_event

store = helpers.store


async def resolve_agent(reference: str) -> AgentSpecification:
    if reference in {"assistant", "assistant@1"}:
        return AgentSpecification(id="assistant", name="Assistant", revision=1)
    if reference == "assistant@2":
        return AgentSpecification(id="assistant", name="Assistant", revision=2)
    raise OpsMeshError("NOT_FOUND", "Published agent revision does not exist")


async def test_binding_rebind_and_unbind_control_future_events_after_restart(store):
    selected = []

    async def dispatch(context):
        selected.append(context.values["agent_revision"])
        return DispatchResult(context.event.id)

    async with httpx.AsyncClient() as client:
        service = BotService(
            store,
            secret,
            client,
            authorize,
            dispatch,
            agent_resolver=resolve_agent,
            principal_resolver=resolve_principal,
        )
        await service.create_account(account(), actor_id="admin")
        await service.create_account(account().model_copy(update={"id": "other"}), actor_id="admin")
        await service.bind_identity("support", "200", "user", actor_id="admin")
        binding = await service.bind_route("support", "c2c:200", "assistant", actor_id="admin")
        assert binding.agent_revision == "assistant@1"
        restarted = BotService(
            store,
            secret,
            client,
            authorize,
            dispatch,
            agent_resolver=resolve_agent,
            principal_resolver=resolve_principal,
        )
        assert await restarted.get_route("support", "c2c:200", actor_id="user") == binding
        assert await restarted.list_routes("support", actor_id="user") == [binding]
        assert await restarted.list_routes("other", actor_id="user") == []

        body, signature = webhook(message_id=1)
        await receive_qq_event(restarted, "support", body, signature=signature)
        replacement = await restarted.bind_route(
            "support", "c2c:200", "assistant@2", actor_id="admin"
        )
        assert await service.get_route("support", "c2c:200", actor_id="user") == replacement
        assert await service.list_routes("support", actor_id="user") == [replacement]
        body, signature = webhook(message_id=2)
        await receive_qq_event(service, "support", body, signature=signature)

        await restarted.unbind_route("support", "c2c:200", actor_id="admin")
        await restarted.unbind_route("support", "c2c:200", actor_id="admin")
        assert await service.list_routes("support", actor_id="user") == []
        with pytest.raises(OpsMeshError) as missing:
            await service.get_route("support", "c2c:200", actor_id="user")
        assert missing.value.code == "NOT_FOUND"
        body, signature = webhook(message_id=3)
        await receive_qq_event(service, "support", body, signature=signature)
        assert selected == ["assistant@1", "assistant@2", None]


async def test_binding_service_enforces_permissions_and_agent_validation_before_mutation(store):
    resolutions = []

    async def resolve(reference):
        resolutions.append(reference)
        if reference == "disabled@1":
            raise OpsMeshError("FORBIDDEN", "Agent is disabled")
        return await resolve_agent(reference)

    async def check(actor, action, resource):
        if actor == "admin" or (actor == "bot-manager" and action == "bots.manage"):
            return
        raise OpsMeshError("FORBIDDEN", "Denied")

    async with httpx.AsyncClient() as client:
        service = BotService(store, secret, client, check, ignore, agent_resolver=resolve)
        await service.create_account(account(), actor_id="admin")
        original = await service.bind_route("support", "c2c:200", "assistant@1", actor_id="admin")
        resolutions.clear()
        for method, args in (
            (service.bind_route, ("support", "c2c:200", "assistant@2")),
            (service.get_route, ("support", "c2c:200")),
            (service.list_routes, ("support",)),
            (service.unbind_route, ("support", "c2c:200")),
        ):
            with pytest.raises(OpsMeshError) as denied:
                await method(*args, actor_id="stranger")
            assert denied.value.code == "FORBIDDEN"
        assert resolutions == []
        with pytest.raises(OpsMeshError) as agent_denied:
            await service.bind_route("support", "c2c:200", "assistant@2", actor_id="bot-manager")
        assert agent_denied.value.code == "FORBIDDEN"
        for reference, code in (("missing@1", "NOT_FOUND"), ("disabled@1", "FORBIDDEN")):
            with pytest.raises(OpsMeshError) as invalid:
                await service.bind_route("support", "c2c:200", reference, actor_id="admin")
            assert invalid.value.code == code
        assert await service.get_route("support", "c2c:200", actor_id="admin") == original
        await service.set_enabled("support", False, actor_id="admin")
        assert await service.list_routes("support", actor_id="admin") == [original]
        await service.unbind_route("support", "c2c:200", actor_id="admin")
        assert await service.list_routes("support", actor_id="admin") == []


async def test_binding_without_catalog_resolver_is_rejected(store):
    async with httpx.AsyncClient() as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(account(), actor_id="admin")
        with pytest.raises(OpsMeshError) as unavailable:
            await service.bind_route("support", "c2c:200", "assistant@1", actor_id="admin")
        assert unavailable.value.code == "CONFIG_INVALID"
        assert await service.list_routes("support", actor_id="admin") == []


async def test_unbind_does_not_remove_a_concurrent_replacement(store, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async with httpx.AsyncClient() as client:
        service = BotService(store, secret, client, authorize, ignore, agent_resolver=resolve_agent)
        await service.create_account(account(), actor_id="admin")
        await service.bind_route("support", "c2c:200", "assistant@1", actor_id="admin")
        original_snapshot = service._authorized_identity_snapshot

        async def paused_snapshot(bot_id, actor_id):
            snapshot = await original_snapshot(bot_id, actor_id)
            if not entered.is_set():
                entered.set()
                await release.wait()
            return snapshot

        monkeypatch.setattr(service, "_authorized_identity_snapshot", paused_snapshot)
        removing = asyncio.create_task(service.unbind_route("support", "c2c:200", actor_id="admin"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            replacement = await service.bind_route(
                "support", "c2c:200", "assistant@2", actor_id="admin"
            )
        finally:
            release.set()
        with pytest.raises(OpsMeshError) as conflict:
            await removing
        assert conflict.value.code == "CONFLICT"
        assert await service.get_route("support", "c2c:200", actor_id="admin") == replacement
