"""Authorized Agent use cases shared by HTTP and dependency-injected commands."""

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation

from .api import AgentDefinition, AgentDefinitionCatalog


class AgentManagement:
    def __init__(
        self, catalog: AgentDefinitionCatalog, accounts: AccountDirectory, audit: AuditJournal
    ) -> None:
        self.catalog, self.accounts, self.audit = catalog, accounts, audit

    async def _authorize(
        self, actor_id: str, action: str, resource: str, owner_id: str | None = None
    ) -> None:
        principal = await self.accounts.get_principal(actor_id)
        await self.accounts.authorize(principal, action, resource, owner_id=owner_id)

    async def create(self, specification: AgentSpecification, *, actor_id: str) -> AgentDefinition:
        await self._authorize(actor_id, "agent:create", specification.id)
        async with audited_operation(self.audit, "agent.create", specification.id, actor_id):
            return await self.catalog.create(specification, actor_id)

    async def get(self, agent_id: str, *, actor_id: str) -> AgentDefinition:
        value = await self.catalog.get(agent_id)
        await self._authorize(actor_id, "agent:read", agent_id, value.owner_id)
        return value

    async def list(self, *, actor_id: str) -> list[AgentDefinition]:
        result = []
        for value in await self.catalog.list():
            try:
                await self._authorize(actor_id, "agent:read", value.id, value.owner_id)
            except OpsMeshError as error:
                if error.code != "FORBIDDEN":
                    raise
            else:
                result.append(value)
        return result

    async def get_revision(self, reference: str, *, actor_id: str) -> AgentSpecification:
        value = await self.catalog.get_revision(reference)
        await self.get(value.id, actor_id=actor_id)
        return value

    async def update(
        self,
        agent_id: str,
        specification: AgentSpecification,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> AgentDefinition:
        current = await self.catalog.get(agent_id)
        await self._authorize(actor_id, "agent:write", agent_id, current.owner_id)
        async with audited_operation(
            self.audit,
            "agent.update",
            agent_id,
            actor_id,
            details={"expected_revision": expected_revision},
        ):
            return await self.catalog.update(agent_id, specification, expected_revision)

    async def publish(
        self, agent_id: str, expected_revision: int, *, actor_id: str
    ) -> AgentSpecification:
        current = await self.catalog.get(agent_id)
        await self._authorize(actor_id, "agent:publish", agent_id, current.owner_id)
        async with audited_operation(
            self.audit,
            "agent.publish",
            agent_id,
            actor_id,
            details={"expected_revision": expected_revision},
        ):
            return await self.catalog.publish(agent_id, expected_revision)

    async def set_enabled(
        self, agent_id: str, enabled: bool, expected_revision: int, *, actor_id: str
    ) -> AgentDefinition:
        current = await self.catalog.get(agent_id)
        await self._authorize(actor_id, "agent:write", agent_id, current.owner_id)
        async with audited_operation(
            self.audit,
            "agent.set_enabled",
            agent_id,
            actor_id,
            details={"enabled": enabled, "expected_revision": expected_revision},
        ):
            return await self.catalog.set_enabled(agent_id, enabled, expected_revision)

    async def clone(self, reference: str, target_id: str, *, actor_id: str) -> AgentDefinition:
        await self.get_revision(reference, actor_id=actor_id)
        await self._authorize(actor_id, "agent:create", target_id)
        async with audited_operation(
            self.audit, "agent.clone", target_id, actor_id, details={"source": reference}
        ):
            return await self.catalog.clone(reference, target_id, actor_id)
