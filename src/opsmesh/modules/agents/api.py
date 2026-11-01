"""Agent definitions and deployed factories, without native SDK instances."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import BaseModel

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.typing import OpsMeshContract


class AgentDefinition(OpsMeshContract):
    id: str
    owner_id: str
    draft: AgentSpecification
    published_revision: int | None = None
    enabled: bool = True
    revision: int = 1


class AgentFactoryReference(OpsMeshContract):
    name: str
    version: str

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


AgentFactory = Callable[
    [BaseModel, dict[str, Any]], AgentSpecification | Awaitable[AgentSpecification]
]
AgentValidator = Callable[[AgentSpecification], Awaitable[None]]


class AgentDefinitionCatalog(Protocol):
    async def create(self, specification: AgentSpecification, owner_id: str) -> AgentDefinition: ...
    async def get(self, agent_id: str) -> AgentDefinition: ...
    async def list(self) -> list[AgentDefinition]: ...
    async def publish(self, agent_id: str, expected_revision: int) -> AgentSpecification: ...
    async def get_revision(self, reference: str) -> AgentSpecification: ...
    async def update(
        self, agent_id: str, specification: AgentSpecification, expected_revision: int
    ) -> AgentDefinition: ...
    async def set_enabled(
        self, agent_id: str, enabled: bool, expected_revision: int
    ) -> AgentDefinition: ...
    async def clone(self, reference: str, target_id: str, owner_id: str) -> AgentDefinition: ...


class AgentFactoryRegistry(Protocol):
    def register(
        self, *, name: str, version: str, config_schema: type[BaseModel], factory: AgentFactory
    ) -> AgentFactoryReference: ...
    async def build(
        self, reference: str, configuration: dict[str, Any], context: dict[str, Any]
    ) -> AgentSpecification: ...
    def freeze(self) -> None: ...
