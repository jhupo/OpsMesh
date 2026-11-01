"""Protected account use cases are independent of HTTP and cannot grant unheld authority."""

import asyncio

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.accounts.internal.management import AuthorizedAccountManagement
from opsmesh.modules.accounts.internal.service import LocalAccountService


@pytest.fixture
async def accounts(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'accounts.db').as_posix()}")
    directory = LocalAccountService(database)
    await database.initialize()
    admin = await directory.create_user("admin", "admin-password-123", is_admin=True)
    manager = AuthorizedAccountManagement(directory)
    try:
        yield directory, manager, admin
    finally:
        await database.close()


async def test_account_manager_cannot_mint_permissions_or_take_over_admin(accounts):
    directory, manager, admin = accounts
    await directory.create_role(
        AccessRole(name="manager", grants=(PermissionGrant(action="accounts:manage"),))
    )
    operator = await directory.create_user("operator", "operator-password", roles=("manager",))
    ordinary = await manager.create_user("ordinary", "ordinary-password", actor_id=operator.id)
    assert ordinary.active
    for call in (
        manager.create_role(
            AccessRole(name="root", grants=(PermissionGrant(action="*"),)), actor_id=operator.id
        ),
        manager.update_user(operator.id, roles=("manager",), actor_id=operator.id),
        manager.update_user(admin.id, active=False, actor_id=operator.id),
        manager.reset_password(admin.id, "replacement-password", actor_id=operator.id),
        manager.create_user("newadmin", "newadmin-password", is_admin=True, actor_id=operator.id),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await call
        assert denied.value.code == "FORBIDDEN"


async def test_delegation_never_exceeds_held_actions_or_department_scope(accounts):
    directory, manager, _admin = accounts
    await directory.create_role(
        AccessRole(
            name="delegator",
            grants=(
                PermissionGrant(action="accounts:roles:manage"),
                PermissionGrant(action="accounts:roles:assign"),
                PermissionGrant(action="accounts:manage"),
                PermissionGrant(action="agent:read", resource="team:*"),
            ),
        )
    )
    operator = await directory.create_user("operator", "operator-password", roles=("delegator",))
    await manager.create_role(
        AccessRole(
            name="reader", grants=(PermissionGrant(action="agent:read", resource="team:one"),)
        ),
        actor_id=operator.id,
    )
    target = await manager.create_user("target", "target-password", actor_id=operator.id)
    await manager.update_user(target.id, roles=("reader",), actor_id=operator.id)
    for grant in (
        PermissionGrant(action="*"),
        PermissionGrant(action="agent:read", resource="*"),
        PermissionGrant(action="agent:*", resource="team:*"),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await manager.create_role(
                AccessRole(name="excessive", grants=(grant,)), actor_id=operator.id
            )
        assert denied.value.code == "FORBIDDEN"
    with pytest.raises(OpsMeshError):
        await manager.update_role(
            AccessRole(name="delegator"), actor_id=operator.id, expected_revision=1
        )


async def test_last_admin_guard_is_atomic_under_concurrent_disable(accounts):
    directory, manager, first = accounts
    second = await manager.create_user(
        "second", "second-password", is_admin=True, actor_id=first.id
    )
    results = await asyncio.gather(
        manager.update_user(first.id, active=False, actor_id=first.id),
        manager.update_user(second.id, active=False, actor_id=second.id),
        return_exceptions=True,
    )
    assert sum(isinstance(result, OpsMeshError) for result in results) == 1
    assert sum(user.active and user.is_admin for user in await directory.list_users()) == 1


async def test_password_rotation_and_session_revocation(accounts):
    directory, manager, admin = accounts
    user = await manager.create_user("member", "member-password", actor_id=admin.id)
    first = await directory.login("member", "member-password")
    second = await directory.login("member", "member-password")
    sessions = await manager.list_sessions(user.id, actor_id=user.id)
    assert len(sessions) == 2
    assert all(session.id != first.token for session in sessions)
    with pytest.raises(OpsMeshError):
        await manager.change_password("incorrect", "replacement-password", actor_id=user.id)
    await manager.change_password("member-password", "replacement-password", actor_id=user.id)
    for token in (first.token, second.token):
        with pytest.raises(OpsMeshError) as revoked:
            await directory.authenticate(token)
        assert revoked.value.code == "UNAUTHENTICATED"
    assert await manager.list_sessions(user.id, actor_id=user.id) == []
    third = await directory.login("member", "replacement-password")
    session = (await manager.list_sessions(user.id, actor_id=user.id))[0]
    await manager.revoke_session(user.id, session.id, actor_id=admin.id)
    with pytest.raises(OpsMeshError):
        await directory.authenticate(third.token)
    fourth = await directory.login("member", "replacement-password")
    await manager.reset_password(user.id, "reset-password-123", actor_id=admin.id)
    with pytest.raises(OpsMeshError):
        await directory.authenticate(fourth.token)
    fifth = await directory.login("member", "reset-password-123")
    await manager.revoke_sessions(user.id, actor_id=user.id)
    with pytest.raises(OpsMeshError):
        await directory.authenticate(fifth.token)


async def test_role_cas_references_and_immediate_authorization(accounts):
    directory, manager, admin = accounts
    role = await manager.create_role(
        AccessRole(name="reader", grants=(PermissionGrant(action="agent:read"),)), actor_id=admin.id
    )
    user = await manager.create_user(
        "member", "member-password", roles=(role.name,), actor_id=admin.id
    )
    await directory.authorize(user, "agent:read", "any")
    role = await manager.update_role(
        role.model_copy(update={"grants": ()}), expected_revision=1, actor_id=admin.id
    )
    assert role.revision == 2
    with pytest.raises(OpsMeshError):
        await directory.authorize(user, "agent:read", "any")
    with pytest.raises(OpsMeshError) as stale:
        await manager.update_role(role, expected_revision=1, actor_id=admin.id)
    assert stale.value.code == "CONFLICT"
    with pytest.raises(OpsMeshError):
        await manager.delete_role(role.name, expected_revision=2, actor_id=admin.id)
    await manager.update_user(user.id, roles=(), actor_id=admin.id)
    await manager.delete_role(role.name, expected_revision=2, actor_id=admin.id)


async def test_department_cycles_and_references(accounts):
    _directory, manager, admin = accounts
    parent = await manager.create_department("Engineering", actor_id=admin.id)
    child = await manager.create_department("Runtime", parent.id, actor_id=admin.id)
    with pytest.raises(OpsMeshError) as cycle:
        await manager.update_department(
            parent.model_copy(update={"parent_id": child.id}),
            expected_revision=1,
            actor_id=admin.id,
        )
    assert cycle.value.code == "CONFLICT"
    with pytest.raises(OpsMeshError):
        await manager.delete_department(parent.id, expected_revision=1, actor_id=admin.id)
    child = await manager.update_department(
        child.model_copy(update={"name": "Runtime Platform"}),
        expected_revision=1,
        actor_id=admin.id,
    )
    assert child.revision == 2
    user = await manager.create_user(
        "member", "member-password", departments=(child.id,), actor_id=admin.id
    )
    with pytest.raises(OpsMeshError):
        await manager.delete_department(child.id, expected_revision=2, actor_id=admin.id)
    await manager.update_user(user.id, departments=(), actor_id=admin.id)
    await manager.delete_department(child.id, expected_revision=2, actor_id=admin.id)
    await manager.delete_department(parent.id, expected_revision=1, actor_id=admin.id)


async def test_actor_events_are_atomic_and_never_contain_passwords(accounts, monkeypatch):
    directory, manager, admin = accounts
    original = directory.database.enqueue_events

    async def fail(connection, events):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(directory.database, "enqueue_events", fail)
    with pytest.raises(RuntimeError):
        await manager.create_role(AccessRole(name="rolled-back"), actor_id=admin.id)
    assert await directory.roles.get("rolled-back") is None
    monkeypatch.setattr(directory.database, "enqueue_events", original)
    await manager.create_user("member", "member-password", actor_id=admin.id)
    events = await directory.database.pending_events()
    assert any(event.get("actor_ref") == admin.id for event in events)
    assert "member-password" not in str(events) and "password_hash" not in str(events)
