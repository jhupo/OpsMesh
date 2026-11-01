"""Typed, versioned Python factories registered before startup."""

import inspect
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import OpsMeshPlugin, get_current_plugin
from opsmesh.modules.agents.api import AgentFactory, AgentFactoryReference


@dataclass(frozen=True)
class RegisteredAgentFactory:
    configuration_type: type[BaseModel]
    build: AgentFactory
    owner: OpsMeshPlugin | None


class InProcessAgentFactoryRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, RegisteredAgentFactory] = {}
        self._frozen = False

    def register(
        self, *, name: str, version: str, config_schema: type[BaseModel], factory: AgentFactory
    ) -> AgentFactoryReference:
        reference = AgentFactoryReference(name=name, version=version)
        if self._frozen or not name or not version or reference.ref in self._factories:
            raise OpsMeshError("CONFLICT", "Factory registration is frozen, invalid or duplicated")
        owner = get_current_plugin()
        registered = RegisteredAgentFactory(config_schema, factory, owner)
        self._factories[reference.ref] = registered
        if owner is not None:

            def rollback() -> None:
                if self._factories.get(reference.ref) is registered:
                    del self._factories[reference.ref]

            owner.own(rollback)
        return reference

    async def build(
        self, reference: str, configuration: dict[str, Any], context: dict[str, Any]
    ) -> AgentSpecification:
        factory = self._factories.get(reference)
        if factory is None:
            raise OpsMeshError("NOT_FOUND", "Agent factory is not deployed")
        if factory.owner is not None and not factory.owner.enabled:
            raise OpsMeshError("PLUGIN_DISABLED", "Agent factory plugin is not enabled")
        result = factory.build(factory.configuration_type.model_validate(configuration), context)
        specification = await result if inspect.isawaitable(result) else result
        if not isinstance(specification, AgentSpecification):
            raise OpsMeshError("CONFIG_INVALID", "Factory must return an AgentSpecification")
        return specification

    def freeze(self) -> None:
        self._frozen = True
