"""Legacy secret grants retain their scopes across authorization and delegation."""

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.accounts.internal.management import AuthorizedAccountManagement
from opsmesh.modules.accounts.internal.service import LocalAccountService


@pytest.mark.parametrize("grant", ["secret:use", "secret:*"])
async def test_permission_names_preserve_resource_scope(tmp_path, grant):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'aliases.db').as_posix()}")
    accounts = LocalAccountService(database)
    await database.initialize()
    try:
        await accounts.create_role(
            AccessRole(
                name="key-user",
                grants=(PermissionGrant(action=grant, resource="env:APP_KEY"),),
            )
        )
        user = await accounts.create_user("operator", "test-password-123", roles=("key-user",))
        for action in ("secret:use",):
            await accounts.authorize(user, action, "env:APP_KEY")
            with pytest.raises(OpsMeshError) as denied:
                await accounts.authorize(user, action, "env:PRIVATE_KEY")
            assert denied.value.code == "FORBIDDEN"
        with pytest.raises(OpsMeshError):
            await accounts.authorize(user, "accounts:manage", "env:APP_KEY")
        with pytest.raises(OpsMeshError):
            await accounts.authorize(user, "secrets:use", "env:APP_KEY")
        if grant.endswith(":use"):
            with pytest.raises(OpsMeshError):
                await accounts.authorize(user, "secret:delete", "env:APP_KEY")
    finally:
        await database.close()


async def test_delegation_checks_the_same_exact_actions_without_broadening(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'delegation.db').as_posix()}")
    accounts = LocalAccountService(database)
    management = AuthorizedAccountManagement(accounts)
    await database.initialize()
    try:
        await accounts.create_role(
            AccessRole(
                name="delegator",
                grants=(
                    PermissionGrant(action="accounts:roles:manage"),
                    PermissionGrant(action="secret:use", resource="env:APP_KEY"),
                ),
            )
        )
        user = await accounts.create_user("operator", "test-password-123", roles=("delegator",))
        role = await management.create_role(
            AccessRole(
                name="canonical-user",
                grants=(PermissionGrant(action="secret:use", resource="env:APP_KEY"),),
            ),
            actor_id=user.id,
        )
        assert role.grants[0].action == "secret:use"
        for grant in (
            PermissionGrant(action="secret:*", resource="env:APP_KEY"),
            PermissionGrant(action="secrets:*", resource="env:APP_KEY"),
            PermissionGrant(action="secret:use", resource="env:*"),
            PermissionGrant(action="secrets:use", resource="env:PRIVATE_KEY"),
        ):
            with pytest.raises(OpsMeshError) as denied:
                await management.create_role(
                    AccessRole(name="excessive", grants=(grant,)), actor_id=user.id
                )
            assert denied.value.code == "FORBIDDEN"
    finally:
        await database.close()
