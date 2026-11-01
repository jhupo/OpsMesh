"""Role delegation and department hierarchy administration."""

from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccessRole, DepartmentProfile, UserPrincipal

from .management_base import AccountManagementBase
from .management_policy import principal_view
from .transactions import AccountTransaction, account_transaction


class AccountCatalogManagement(AccountManagementBase):
    async def create_role(self, role: AccessRole, *, actor_id: str) -> AccessRole:
        async with account_transaction(self.directory.database) as tx:
            actor = await self.require(tx, actor_id, "accounts:roles:manage", "roles")
            await self.policy.grant_ceiling(tx, actor, role.grants)
            row = await tx.write(
                self.directory.roles,
                role.name,
                role.model_dump(mode="json"),
                actor_id=actor_id,
                action="accounts.role.created",
            )
            return AccessRole.model_validate({**row.data, "revision": row.revision})

    async def get_role(self, name: str, *, actor_id: str) -> AccessRole:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:read", "roles")
            row = await tx.get(self.directory.roles, name)
            return AccessRole.model_validate({**row.data, "revision": row.revision})

    async def list_roles(self, *, actor_id: str) -> list[AccessRole]:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:read", "roles")
            return [
                AccessRole.model_validate({**row.data, "revision": row.revision})
                for row in await tx.list(self.directory.roles)
            ]

    async def _check_role_target(
        self,
        tx: AccountTransaction,
        actor: UserPrincipal,
        name: str,
    ) -> None:
        previous = AccessRole.model_validate((await tx.get(self.directory.roles, name)).data)
        await self.policy.grant_ceiling(tx, actor, previous.grants)
        if not actor.is_admin and any(
            (user := principal_view(row)).is_admin and name in user.roles
            for row in await tx.list(self.directory.users)
        ):
            raise OpsMeshError("FORBIDDEN", "Only administrators may change administrator roles")
        if not actor.is_admin and name in actor.roles:
            raise OpsMeshError("FORBIDDEN", "Non-administrators cannot change their own roles")

    async def update_role(
        self,
        role: AccessRole,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> AccessRole:
        async with account_transaction(self.directory.database) as tx:
            actor = await self.require(tx, actor_id, "accounts:roles:manage", "roles")
            await self._check_role_target(tx, actor, role.name)
            await self.policy.grant_ceiling(tx, actor, role.grants)
            row = await tx.write(
                self.directory.roles,
                role.name,
                role.model_dump(mode="json"),
                actor_id=actor_id,
                action="accounts.role.updated",
                expected_revision=expected_revision,
            )
            return AccessRole.model_validate({**row.data, "revision": row.revision})

    async def delete_role(self, name: str, *, actor_id: str, expected_revision: int) -> None:
        async with account_transaction(self.directory.database) as tx:
            actor = await self.require(tx, actor_id, "accounts:roles:manage", "roles")
            await self._check_role_target(tx, actor, name)
            if any(
                name in principal_view(row).roles for row in await tx.list(self.directory.users)
            ):
                raise OpsMeshError("CONFLICT", "Role is still assigned to users")
            await tx.delete(
                self.directory.roles,
                name,
                actor_id=actor_id,
                action="accounts.role.deleted",
                expected_revision=expected_revision,
            )

    async def create_department(
        self,
        name: str,
        parent_id: str | None = None,
        *,
        actor_id: str,
    ) -> DepartmentProfile:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:manage", "departments")
            if parent_id:
                await tx.get(self.directory.departments, parent_id)
            department = DepartmentProfile(id=str(uuid4()), name=name, parent_id=parent_id)
            self._validate_department(department)
            await tx.write(
                self.directory.departments,
                department.id,
                department.model_dump(mode="json"),
                actor_id=actor_id,
                action="accounts.department.created",
            )
            return department

    @staticmethod
    def _validate_department(department: DepartmentProfile) -> None:
        if not department.name.strip() or len(department.name) > 200:
            raise OpsMeshError("INVALID_REQUEST", "Department name must contain 1-200 characters")

    async def get_department(self, department_id: str, *, actor_id: str) -> DepartmentProfile:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:read", "departments")
            row = await tx.get(self.directory.departments, department_id)
            return DepartmentProfile.model_validate({**row.data, "revision": row.revision})

    async def list_departments(self, *, actor_id: str) -> list[DepartmentProfile]:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:read", "departments")
            return [
                DepartmentProfile.model_validate({**row.data, "revision": row.revision})
                for row in await tx.list(self.directory.departments)
            ]

    async def update_department(
        self,
        department: DepartmentProfile,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> DepartmentProfile:
        self._validate_department(department)
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:manage", "departments")
            await tx.get(self.directory.departments, department.id)
            seen = {department.id}
            parent_id = department.parent_id
            while parent_id is not None:
                if parent_id in seen:
                    raise OpsMeshError("CONFLICT", "Department hierarchy cannot contain cycles")
                seen.add(parent_id)
                parent_id = (await tx.get(self.directory.departments, parent_id)).data["parent_id"]
            row = await tx.write(
                self.directory.departments,
                department.id,
                department.model_dump(mode="json"),
                actor_id=actor_id,
                action="accounts.department.updated",
                expected_revision=expected_revision,
            )
            return DepartmentProfile.model_validate({**row.data, "revision": row.revision})

    async def delete_department(
        self,
        department_id: str,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> None:
        async with account_transaction(self.directory.database) as tx:
            await self.require(tx, actor_id, "accounts:manage", "departments")
            await tx.get(self.directory.departments, department_id)
            if any(
                department_id in principal_view(row).departments
                for row in await tx.list(self.directory.users)
            ) or any(
                row.data.get("parent_id") == department_id
                for row in await tx.list(self.directory.departments)
            ):
                raise OpsMeshError(
                    "CONFLICT", "Department is still referenced by users or children"
                )
            await tx.delete(
                self.directory.departments,
                department_id,
                actor_id=actor_id,
                action="accounts.department.deleted",
                expected_revision=expected_revision,
            )
