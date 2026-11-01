"""Bot management cannot impersonate local users or export unrelated credentials."""

import asyncio
from unittest.mock import Mock

import httpx
import pytest

from opsmesh.framework.adapters import OpsMeshMessage
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.accounts.internal.service import LocalAccountService

from .fixtures import account, webhook
from .fixtures import target as reply_target
from .helpers import create_bot_service as BotService
from .helpers import qq_adapter, receive_qq_event


@pytest.fixture
async def platform(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'permissions.db').as_posix()}")
    accounts = LocalAccountService(database)
    records = VersionedRecordStore(database, "bots_permissions")
    resolved = []
    dispatched = []
    values = {
        "env:BOT_SECRET": "test-secret",
        "env:BOT_TOKEN": "test-token",
        "env:UNRELATED_PRIVATE": "synthetic-private-value",
    }

    def secret(reference):
        resolved.append(reference)
        return values[reference]

    async def authorize(actor, action, resource):
        await accounts.authorize(await accounts.get_principal(actor), action, resource)

    async def dispatch(context):
        dispatched.append(await accounts.get_principal(context.values["principal_id"]))
        return DispatchResult(context.event.id)

    async def resolve_agent(reference):
        name, _, version = reference.partition("@")
        return AgentSpecification(id=name, name=name, revision=int(version or 1))

    await database.initialize()
    await accounts.create_role(
        AccessRole(
            name="bot_operator",
            grants=(
                PermissionGrant(action="bots.*", resource="support"),
                PermissionGrant(action="agent:run", resource="controlled@1"),
            ),
        )
    )
    users = {}
    for name in ("admin", "operator", "binder", "alice", "bob"):
        users[name] = await accounts.create_user(
            name, "local-test-password", roles=("bot_operator",), is_admin=name == "admin"
        )
    await accounts.create_role(
        AccessRole(
            name="scoped_binder",
            grants=(
                PermissionGrant(action="accounts:bot_identity:manage", resource=users["alice"].id),
                PermissionGrant(action="secret:use", resource="env:BOT_SECRET"),
                PermissionGrant(action="secret:use", resource="env:BOT_TOKEN"),
            ),
        )
    )
    await accounts.update_user(users["binder"].id, roles=("bot_operator", "scoped_binder"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    ) as client:
        service = BotService(
            records,
            secret,
            client,
            authorize,
            dispatch,
            principal_resolver=accounts.get_principal,
            agent_resolver=resolve_agent,
        )
        await service.create_account(account(), actor_id=users["admin"].id)
        resolved.clear()
        try:
            yield service, accounts, users, resolved, dispatched
        finally:
            await service.close()
            await database.close()


async def test_bot_management_does_not_grant_identity_delegation(platform):
    service, _accounts, users, _resolved, dispatched = platform
    for target in (users["operator"], users["alice"], users["admin"]):
        with pytest.raises(OpsMeshError) as denied:
            await service.bind_identity("support", "200", target.id, actor_id=users["operator"].id)
        assert denied.value.code == "FORBIDDEN"
    assert await service.list_identities("support", actor_id=users["admin"].id) == []
    body, signature = webhook()
    with pytest.raises(OpsMeshError) as unbound:
        await receive_qq_event(service, "support", body, signature=signature)
    assert unbound.value.code == "FORBIDDEN" and dispatched == []


async def test_scoped_binding_and_replacement_require_both_target_permissions(platform):
    service, _accounts, users, _resolved, dispatched = platform
    identity = await service.bind_identity(
        "support", "200", users["alice"].id, actor_id=users["binder"].id
    )
    route = await service.bind_route(
        "support", "c2c:200", "controlled@1", actor_id=users["binder"].id
    )
    assert route.agent_revision == "controlled@1"
    await service.unbind_route("support", "c2c:200", actor_id=users["binder"].id)
    body, signature = webhook()
    await receive_qq_event(service, "support", body, signature=signature)
    assert [principal.id for principal in dispatched] == [users["alice"].id]
    assert not dispatched[0].is_admin
    with pytest.raises(OpsMeshError) as denied:
        await service.bind_identity("support", "200", users["bob"].id, actor_id=users["binder"].id)
    assert denied.value.code == "FORBIDDEN"
    assert await service.get_identity("support", "200", actor_id=users["binder"].id) == identity
    await service.unbind_identity("support", "200", actor_id=users["binder"].id)
    assert await service.list_identities("support", actor_id=users["binder"].id) == []
    # A grant to the new target does not allow replacing the existing target.
    await service.bind_identity("support", "200", users["bob"].id, actor_id=users["admin"].id)
    for operation in (
        service.bind_identity("support", "200", users["alice"].id, actor_id=users["binder"].id),
        service.unbind_identity("support", "200", actor_id=users["binder"].id),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"


async def test_admin_binding_and_existing_admin_configuration_require_admin(platform):
    service, accounts, users, _resolved, _dispatched = platform
    await accounts.create_role(
        AccessRole(
            name="all_identities", grants=(PermissionGrant(action="accounts:bot_identity:manage"),)
        )
    )
    await accounts.update_user(
        users["binder"].id, roles=("bot_operator", "scoped_binder", "all_identities")
    )
    with pytest.raises(OpsMeshError) as denied:
        await service.bind_identity(
            "support", "200", users["admin"].id, actor_id=users["binder"].id
        )
    assert denied.value.code == "FORBIDDEN"
    await service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    for operation in (
        service.bind_identity("support", "200", users["alice"].id, actor_id=users["binder"].id),
        service.unbind_identity("support", "200", actor_id=users["binder"].id),
        service.update_account(
            account().model_copy(
                update={"settings": {**account().settings, "api_base_url": "http://127.0.0.3:5700"}}
            ),
            expected_revision=1,
            actor_id=users["binder"].id,
        ),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"
    assert (await service.get_account("support", actor_id=users["admin"].id)).revision == 1


async def test_credentials_are_authorized_before_any_resolution_or_connection(
    platform, monkeypatch
):
    service, _accounts, users, resolved, _dispatched = platform
    connect = Mock()
    monkeypatch.setattr(qq_adapter(service), "configure", connect)
    for method in ("create", "update"):
        desired = account().model_copy(
            update={
                "settings": {**account().settings, "api_base_url": "http://127.0.0.4:5700"},
                "credentials": {**account().credentials, "client_secret": "env:UNRELATED_PRIVATE"},
            }
        )
        with pytest.raises(OpsMeshError) as denied:
            if method == "create":
                await service.create_account(desired, actor_id=users["binder"].id)
            else:
                await service.update_account(
                    desired, expected_revision=1, actor_id=users["binder"].id
                )
        assert denied.value.code == "FORBIDDEN"
        assert resolved == []
    # Reusing unchanged credentials with a different endpoint still needs permission.
    with pytest.raises(OpsMeshError) as denied:
        await service.update_account(
            account().model_copy(
                update={"settings": {**account().settings, "api_base_url": "http://127.0.0.4:5700"}}
            ),
            expected_revision=1,
            actor_id=users["operator"].id,
        )
    assert denied.value.code == "FORBIDDEN" and resolved == []
    connect.assert_not_called()
    allowed = await service.update_account(
        account().model_copy(
            update={"settings": {**account().settings, "api_base_url": "http://127.0.0.5:5700"}}
        ),
        expected_revision=1,
        actor_id=users["binder"].id,
    )
    assert allowed.revision == 2
    assert resolved == ["env:BOT_SECRET"]


async def test_concurrent_admin_binding_invalidates_configuration_authorization(
    platform, monkeypatch
):
    service, _accounts, users, _resolved, _dispatched = platform
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._authorize_credentials

    async def wait_after_identity_checks(config, actor_id):
        if actor_id == users["binder"].id:
            entered.set()
            await release.wait()
        await original(config, actor_id)

    monkeypatch.setattr(service, "_authorize_credentials", wait_after_identity_checks)
    update = asyncio.create_task(
        service.update_account(
            account().model_copy(
                update={"settings": {**account().settings, "api_base_url": "http://127.0.0.6:5700"}}
            ),
            expected_revision=1,
            actor_id=users["binder"].id,
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await update
    assert conflict.value.code == "CONFLICT"
    assert (await service.get_account("support", actor_id=users["admin"].id)).revision == 1


async def test_concurrent_configuration_change_invalidates_pending_identity_binding(
    platform, monkeypatch
):
    service, _accounts, users, _resolved, _dispatched = platform
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._authorize_identity_target

    async def wait_after_target_checks(principal_id, actor_id, **kwargs):
        await original(principal_id, actor_id, **kwargs)
        if actor_id == users["binder"].id:
            entered.set()
            await release.wait()

    monkeypatch.setattr(service, "_authorize_identity_target", wait_after_target_checks)
    binding = asyncio.create_task(
        service.bind_identity("support", "200", users["alice"].id, actor_id=users["binder"].id)
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.update_account(
        account().model_copy(
            update={"settings": {**account().settings, "api_base_url": "http://127.0.0.6:5700"}}
        ),
        expected_revision=1,
        actor_id=users["admin"].id,
    )
    release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await binding
    assert conflict.value.code == "CONFLICT"
    assert await service.list_identities("support", actor_id=users["admin"].id) == []


async def test_route_changes_require_existing_identity_authority_but_sending_does_not(platform):
    service, _accounts, users, _resolved, _dispatched = platform
    await service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    for operation in (
        service.bind_route("support", "c2c:200", "controlled@1", actor_id=users["operator"].id),
        service.bind_route("support", "group:300", "controlled@1", actor_id=users["operator"].id),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"
    original = await service.bind_route(
        "support", "c2c:200", "trusted@1", actor_id=users["admin"].id
    )
    for operation in (
        service.bind_route("support", "c2c:200", "controlled@1", actor_id=users["operator"].id),
        service.unbind_route("support", "c2c:200", actor_id=users["operator"].id),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"
    assert await service.get_route("support", "c2c:200", actor_id=users["operator"].id) == original
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"id": "1", "access_token": "synthetic-token", "expires_in": 7200}
            )
        )
    ) as client:
        qq_adapter(service).client = client
        await qq_adapter(service).disconnect_account("support")
        receipt = await service.send(
            reply_target(),
            OpsMeshMessage("ordinary send"),
            idempotency_key="send",
            actor_id=users["operator"].id,
        )
    assert receipt.message_id == "1"
    await service.unbind_route("support", "c2c:200", actor_id=users["admin"].id)
    assert await service.list_routes("support", actor_id=users["admin"].id) == []


async def test_concurrent_admin_binding_invalidates_pending_route_change(platform, monkeypatch):
    service, _accounts, users, _resolved, _dispatched = platform
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.agent_resolver

    async def resolve_after_binding(reference):
        entered.set()
        await release.wait()
        return await original(reference)

    monkeypatch.setattr(service, "agent_resolver", resolve_after_binding)
    route = asyncio.create_task(
        service.bind_route("support", "c2c:200", "controlled@1", actor_id=users["operator"].id)
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await route
    assert conflict.value.code == "CONFLICT"
    assert await service.list_routes("support", actor_id=users["admin"].id) == []


async def test_concurrent_route_change_invalidates_pending_admin_binding(platform, monkeypatch):
    service, _accounts, users, _resolved, _dispatched = platform
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._authorize_identity_target

    async def wait_after_target_checks(principal_id, actor_id, **kwargs):
        await original(principal_id, actor_id, **kwargs)
        entered.set()
        await release.wait()

    monkeypatch.setattr(service, "_authorize_identity_target", wait_after_target_checks)
    binding = asyncio.create_task(
        service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.bind_route("support", "c2c:200", "controlled@1", actor_id=users["operator"].id)
    release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await binding
    assert conflict.value.code == "CONFLICT"
    assert await service.list_identities("support", actor_id=users["admin"].id) == []


@pytest.mark.parametrize("operation", ["bind", "unbind"])
async def test_identity_delete_recreate_cannot_reuse_old_authorization(
    platform, monkeypatch, operation
):
    service, _accounts, users, _resolved, _dispatched = platform
    await service.bind_identity("support", "200", users["alice"].id, actor_id=users["admin"].id)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._authorize_identity_target
    checks = 0

    async def wait_after_existing_target(principal_id, actor_id, **kwargs):
        nonlocal checks
        await original(principal_id, actor_id, **kwargs)
        if actor_id == users["binder"].id:
            checks += 1
            if checks == (2 if operation == "bind" else 1):
                entered.set()
                await release.wait()

    monkeypatch.setattr(service, "_authorize_identity_target", wait_after_existing_target)
    request = asyncio.create_task(
        service.bind_identity("support", "200", users["alice"].id, actor_id=users["binder"].id)
        if operation == "bind"
        else service.unbind_identity("support", "200", actor_id=users["binder"].id)
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.unbind_identity("support", "200", actor_id=users["admin"].id)
    await service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await request
    assert conflict.value.code == "CONFLICT"
    identity = await service.get_identity("support", "200", actor_id=users["admin"].id)
    assert identity.principal_id == users["admin"].id


async def test_route_snapshot_includes_target_after_identity_delete_recreate(platform, monkeypatch):
    service, _accounts, users, _resolved, _dispatched = platform
    await service.bind_identity("support", "200", users["alice"].id, actor_id=users["admin"].id)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.agent_resolver

    async def resolve_after_replacement(reference):
        entered.set()
        await release.wait()
        return await original(reference)

    monkeypatch.setattr(service, "agent_resolver", resolve_after_replacement)
    request = asyncio.create_task(
        service.bind_route("support", "c2c:200", "controlled@1", actor_id=users["binder"].id)
    )
    await asyncio.wait_for(entered.wait(), 2)
    await service.unbind_identity("support", "200", actor_id=users["admin"].id)
    await service.bind_identity("support", "200", users["admin"].id, actor_id=users["admin"].id)
    release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await request
    assert conflict.value.code == "CONFLICT"
    assert await service.list_routes("support", actor_id=users["admin"].id) == []
