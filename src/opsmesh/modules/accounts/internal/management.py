"""Actor-aware account service used by HTTP, plugins and scheduled operations."""

import asyncio
import re

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import UserPrincipal

from .management_catalog import AccountCatalogManagement
from .management_credentials import AccountCredentialManagement
from .management_policy import principal_view
from .transactions import account_transaction


class AuthorizedAccountManagement(AccountCatalogManagement, AccountCredentialManagement):
    """Protected use cases; LocalAccountService remains trusted bootstrap infrastructure."""

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        actor_id: str,
        is_admin: bool = False,
        roles: tuple[str, ...] = (),
        departments: tuple[str, ...] = (),
    ) -> UserPrincipal:
        username = username.casefold()
        if not re.fullmatch(r"[a-z][a-z0-9._-]{2,63}", username):
            raise OpsMeshError("INVALID_REQUEST", "Invalid username")
        self.validate_password(password)
        digest = await asyncio.to_thread(self.directory.hasher.hash, password)
        async with account_transaction(self.directory.database) as tx:
            actor = await self.require(tx, actor_id, "accounts:manage", "users")
            if is_admin and not actor.is_admin:
                raise OpsMeshError("FORBIDDEN", "Only an administrator may create administrators")
            await self.policy.memberships(
                tx,
                actor,
                roles,
                departments,
                assigning_roles=bool(roles),
                assigning_departments=bool(departments),
            )
            principal = UserPrincipal(
                id=self.directory._user_id(username),
                username=username,
                roles=roles,
                departments=departments,
                is_admin=is_admin,
            )
            row = await tx.write(
                self.directory.users,
                principal.id,
                {**principal.model_dump(mode="json"), "password_hash": digest, "session_epoch": 0},
                actor_id=actor_id,
                action="accounts.user.created",
            )
            return principal_view(row)

    async def get_principal(self, principal_id: str, *, actor_id: str) -> UserPrincipal:
        async with account_transaction(self.directory.database) as tx:
            actor = await self.policy.actor(tx, actor_id)
            if actor.id != principal_id:
                await self.policy.check(tx, actor, "accounts:read", principal_id)
            return principal_view(await tx.get(self.directory.users, principal_id))

    async def list_users(self, *, actor_id: str) -> list[UserPrincipal]:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:read", "users")
            return [principal_view(row) for row in await tx.list(self.directory.users)]

    async def update_user(
        self,
        principal_id: str,
        *,
        actor_id: str,
        expected_revision: int | None = None,
        active: bool | None = None,
        roles: tuple[str, ...] | None = None,
        departments: tuple[str, ...] | None = None,
    ) -> UserPrincipal:
        async with account_transaction(self.directory.database) as tx:
            actor = await self.require(tx, actor_id, "accounts:manage", principal_id)
            row = await tx.get(self.directory.users, principal_id)
            target = principal_view(row)
            self.policy.protect_admin(actor, target)
            # Managing an account that already holds greater authority is equivalent
            # to controlling that authority (for example by disabling or resetting it).
            await self.policy.memberships(
                tx,
                actor,
                target.roles,
                target.departments,
                assigning_roles=False,
                assigning_departments=False,
            )
            if (
                actor.id == principal_id
                and not actor.is_admin
                and (roles is not None or departments is not None)
            ):
                raise OpsMeshError(
                    "FORBIDDEN", "Non-administrators cannot change their own memberships"
                )
            await self.policy.memberships(
                tx,
                actor,
                roles or (),
                departments or (),
                assigning_roles=roles is not None,
                assigning_departments=departments is not None,
            )
            if active is False and target.is_admin and target.active:
                other_admin = any(
                    principal_view(user).is_admin
                    and principal_view(user).active
                    and user.id != principal_id
                    for user in await tx.list(self.directory.users)
                )
                if not other_admin:
                    raise OpsMeshError(
                        "CONFLICT", "The last active administrator cannot be disabled"
                    )
            data = dict(row.data)
            for key, value in (("active", active), ("roles", roles), ("departments", departments)):
                if value is not None:
                    data[key] = list(value) if isinstance(value, tuple) else value
            if active is False:
                data["session_epoch"] = data.get("session_epoch", 0) + 1
            changed = await tx.write(
                self.directory.users,
                principal_id,
                data,
                actor_id=actor_id,
                action="accounts.user.updated",
                expected_revision=row.revision if expected_revision is None else expected_revision,
            )
            return principal_view(changed)
