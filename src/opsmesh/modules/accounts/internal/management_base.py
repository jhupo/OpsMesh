"""Shared primitives for protected account administration."""

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import UserPrincipal

from .management_policy import AccountManagementPolicy
from .service import LocalAccountService
from .transactions import AccountTransaction


class AccountManagementBase:
    def __init__(self, directory: LocalAccountService) -> None:
        self.directory = directory
        self.policy = AccountManagementPolicy(directory)

    async def require(
        self,
        tx: AccountTransaction,
        actor_id: str,
        action: str,
        resource: str,
    ) -> UserPrincipal:
        actor = await self.policy.actor(tx, actor_id)
        await self.policy.check(tx, actor, action, resource)
        return actor

    @staticmethod
    def validate_password(password: str) -> None:
        if not 12 <= len(password) <= 1024:
            raise OpsMeshError("INVALID_REQUEST", "Password must contain 12-1024 characters")
