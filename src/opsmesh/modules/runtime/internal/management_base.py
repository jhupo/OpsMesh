"""Authorization shared by user-facing Run and approval management."""

from opsmesh.modules.accounts.api import AccountDirectory, UserPrincipal
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.runtime.api import AgentRuntime, AgentRunView


class RuntimeManagementBase:
    def __init__(
        self, runtime: AgentRuntime, accounts: AccountDirectory, audit: AuditJournal
    ) -> None:
        self.runtime, self.accounts, self.audit = runtime, accounts, audit

    async def authorize(self, actor_id: str, run: AgentRunView, action: str) -> UserPrincipal:
        actor = await self.accounts.get_principal(actor_id)
        await self.accounts.authorize(actor, action, run.id, owner_id=run.principal_id)
        return actor
