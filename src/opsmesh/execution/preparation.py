"""Freeze an authorized configuration graph before constructing native resources."""

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh.agents.config.sandbox import AgentConfiguration
from opsmesh.agents.manager import AgentDefinition
from opsmesh.agents.resolvers.agents import resolve_agents
from opsmesh.shared.errors import InvalidArgumentError

type DefinitionLoader = Callable[[str], AgentDefinition]


class AgentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    root_id: str
    definitions: dict[str, AgentConfiguration] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_graph(self) -> "AgentSnapshot":
        if self.root_id not in self.definitions:
            raise ValueError("The root Agent must be in the snapshot")
        return self

    @property
    def requires_sandbox(self) -> bool:
        return any(config.type == "sandbox" for config in self.definitions.values())


def freeze_agent(agent_id: str, load: DefinitionLoader) -> AgentSnapshot:
    """The loader authorizes every transitive definition, including Agent tools."""

    def configuration(identifier: str) -> AgentConfiguration:
        definition = load(identifier)
        if not definition.enabled:
            raise InvalidArgumentError("The Agent configuration is disabled")
        config = definition.config.model_copy(deep=True)
        if config.type == "sandbox" and config.run_as is not None:
            raise InvalidArgumentError(
                "Persistent sandbox instances use the compute image identity; run_as is unsupported"
            )
        return config

    try:
        definitions = resolve_agents(agent_id, configuration)
    except ValueError as error:
        raise InvalidArgumentError(str(error)) from error
    return AgentSnapshot(root_id=agent_id, definitions=definitions)
