"""Load the host's Agent and provider declarations for API and Worker processes."""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.agents.config.sandbox import AgentConfiguration
from opsmesh.agents.manager import AgentDefinition, AgentManager
from opsmesh.agents.providers.config import ProviderConfig
from opsmesh.agents.providers.registry import ProviderRegistry
from opsmesh.agents.resolvers.agents import resolve_agents


class AgentCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[AgentDefinition] = Field(default_factory=list)
    providers: list[ProviderConfig] = Field(default_factory=list)


@dataclass(frozen=True)
class HostCatalog:
    agents: AgentManager
    providers: ProviderRegistry


def load_catalog(path: Path | None) -> HostCatalog:
    catalog = (
        AgentCatalog() if path is None else AgentCatalog.model_validate_json(path.read_bytes())
    )
    agents, providers = AgentManager(), ProviderRegistry()
    for provider in catalog.providers:
        for reference in (provider.credential_ref, provider.network.proxy_ref):
            if reference is not None and not re.fullmatch(r"env:[A-Z_][A-Z0-9_]*", reference):
                raise ValueError("Host credentials require an env:VARIABLE reference")
        providers.register(provider)
    for definition in catalog.agents:
        agents.register(definition)

    def configured(identifier: str) -> AgentConfiguration:
        try:
            definition = agents.get(identifier)
        except KeyError:
            raise ValueError(f"An Agent configuration is missing: {identifier}") from None
        if not definition.enabled:
            raise ValueError(f"An Agent references a disabled configuration: {identifier}")
        return definition.config

    for definition in agents.list():
        if not definition.enabled:
            continue
        graph = resolve_agents(definition.id, configured)
        for config in graph.values():
            if config.model is None:
                raise ValueError("Host Agent configurations require an explicit model reference")
            try:
                providers.select(config.model.provider_id, config.model.model_id)
            except KeyError:
                raise ValueError(
                    "An Agent references an unregistered provider/model: "
                    f"{config.model.provider_id}/{config.model.model_id}"
                ) from None
    return HostCatalog(agents, providers)
