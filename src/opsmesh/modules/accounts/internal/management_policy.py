"""Permission and delegation policy evaluated within account mutation transactions."""

from fnmatch import fnmatchcase

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import StoredRecord
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant, UserPrincipal

from .service import LocalAccountService
from .transactions import AccountTransaction


def principal_view(row: StoredRecord) -> UserPrincipal:
    fields = {key: value for key, value in row.data.items() if key in UserPrincipal.model_fields}
    return UserPrincipal.model_validate({**fields, "revision": row.revision})


class AccountManagementPolicy:
    def __init__(self, directory: LocalAccountService) -> None:
        self.directory = directory

    async def actor(self, tx: AccountTransaction, actor_id: str) -> UserPrincipal:
        actor = principal_view(await tx.get(self.directory.users, actor_id))
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        return actor

    async def grants(self, tx: AccountTransaction, actor: UserPrincipal) -> list[PermissionGrant]:
        roles = {row.id: row for row in await tx.list(self.directory.roles)}
        return [
            grant
            for name in actor.roles
            if name in roles
            for grant in AccessRole.model_validate(roles[name].data).grants
            if grant.department is None or grant.department in actor.departments
        ]

    async def check(
        self,
        tx: AccountTransaction,
        actor: UserPrincipal,
        action: str,
        resource: str,
    ) -> None:
        if actor.is_admin:
            return
        if any(
            fnmatchcase(action, grant.action) and fnmatchcase(resource, grant.resource)
            for grant in await self.grants(tx, actor)
        ):
            return
        raise OpsMeshError("FORBIDDEN", "The account operation is not permitted")

    async def grant_ceiling(
        self,
        tx: AccountTransaction,
        actor: UserPrincipal,
        grants: tuple[PermissionGrant, ...],
    ) -> None:
        if actor.is_admin:
            return
        available = await self.grants(tx, actor)

        def covered(value: str, pattern: str) -> bool:
            # Glob inclusion cannot safely be inferred by matching one pattern against
            # another. Wildcard delegation requires an identical already-held scope.
            return (
                value == pattern if any(c in value for c in "*?[") else fnmatchcase(value, pattern)
            )

        for grant in grants:
            if not any(
                covered(grant.action, own.action)
                and covered(grant.resource, own.resource)
                and (own.department is None or grant.department == own.department)
                for own in available
            ):
                raise OpsMeshError(
                    "FORBIDDEN", "Cannot delegate permissions outside the actor's grants"
                )

    async def memberships(
        self,
        tx: AccountTransaction,
        actor: UserPrincipal,
        roles: tuple[str, ...],
        departments: tuple[str, ...],
        *,
        assigning_roles: bool,
        assigning_departments: bool,
    ) -> None:
        if assigning_roles:
            await self.check(tx, actor, "accounts:roles:assign", "roles")
        if assigning_departments:
            await self.check(tx, actor, "accounts:departments:assign", "departments")
        for name in roles:
            role = AccessRole.model_validate((await tx.get(self.directory.roles, name)).data)
            await self.grant_ceiling(tx, actor, role.grants)
        for department in departments:
            await tx.get(self.directory.departments, department)
            if not actor.is_admin and department not in actor.departments:
                raise OpsMeshError("FORBIDDEN", "Cannot delegate an unheld department membership")

    @staticmethod
    def protect_admin(actor: UserPrincipal, target: UserPrincipal) -> None:
        if target.is_admin and not actor.is_admin:
            raise OpsMeshError("FORBIDDEN", "Only an administrator may manage an administrator")
