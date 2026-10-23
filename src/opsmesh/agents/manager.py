"""Manage configuration snapshots, without keeping live Agent or Session instances."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import RLock
from typing import Literal

from pydantic import Field, TypeAdapter

from opsmesh.agents.assembly import AgentBuild, AssemblyBindings, build_agent
from opsmesh.agents.config.agent import AgentConfig
from opsmesh.agents.config.base import ConfigValue, Identifier
from opsmesh.agents.config.sandbox import AgentConfiguration, SandboxAgentConfig, parse_config


class AgentDefinition(ConfigValue):
    id: Identifier
    owner_user_id: Identifier | None = None
    description: str = ""
    visibility: Literal["private", "public"] = "private"
    enabled: bool = True
    config: AgentConfiguration
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentManager:
    """Trusted application API. HTTP authorization and durable storage are external.

    Import/export exchange complete validated snapshots with the storage owner.
    Build receives a fresh authorized binding view for each execution.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._lock = RLock()

    def register(self, definition: AgentDefinition, *, replace: bool = False) -> None:
        payload = definition.model_dump(mode="json", by_alias=True, exclude_unset=True)
        payload.update(created_at=definition.created_at, updated_at=definition.updated_at)
        snapshot = AgentDefinition.model_validate(payload)
        with self._lock:
            if definition.id in self._definitions and not replace:
                raise ValueError(f"Agent already registered: {definition.id}")
            self._definitions[definition.id] = snapshot

    def get(self, agent_id: str) -> AgentDefinition:
        with self._lock:
            return self._definitions[agent_id].model_copy(deep=True)

    def list(self) -> list[AgentDefinition]:
        with self._lock:
            return [self.get(identifier) for identifier in sorted(self._definitions)]

    def update(self, agent_id: str, config: AgentConfig | SandboxAgentConfig) -> AgentDefinition:
        value = parse_config(config.model_dump(mode="json", by_alias=True, exclude_unset=True))
        with self._lock:
            record = self._definitions[agent_id].model_copy(
                update={"config": value, "updated_at": datetime.now(UTC)}, deep=True
            )
            self._definitions[agent_id] = record
            return record.model_copy(deep=True)

    def patch(self, agent_id: str, values: dict[str, object]) -> AgentDefinition:
        """Omitted fields stay unchanged; supplied lists replace and [] clears."""
        with self._lock:
            current = self._definitions[agent_id].config.model_dump(
                mode="json", by_alias=True, exclude_unset=True
            )
            current.update(values)
            return self.update(agent_id, parse_config(current))

    def remove(self, agent_id: str) -> None:
        with self._lock:
            del self._definitions[agent_id]

    def export_json(self) -> str:
        return (
            TypeAdapter(list[AgentDefinition])
            .dump_json(self.list(), by_alias=True, exclude_unset=True)
            .decode("utf-8")
        )

    def import_json(self, value: str, *, replace: bool = False) -> None:
        records = TypeAdapter(list[AgentDefinition]).validate_json(value)
        ids = [record.id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent snapshot contains duplicate IDs")
        snapshots: dict[str, AgentDefinition] = {}
        for record in records:
            payload = record.model_dump(mode="json", by_alias=True, exclude_unset=True)
            payload.update(created_at=record.created_at, updated_at=record.updated_at)
            snapshots[record.id] = AgentDefinition.model_validate(payload)
        with self._lock:
            if not replace and set(ids) & self._definitions.keys():
                raise ValueError("Agent snapshot would overwrite registered configurations")
            # Validation completes before mutating the registry.
            self._definitions.update(snapshots)

    @asynccontextmanager
    async def build(
        self,
        agent_id: str,
        bindings: AssemblyBindings,
        *,
        max_depth: int = 32,
        max_agents: int = 256,
    ) -> AsyncIterator[AgentBuild]:
        with self._lock:
            records = {key: value.model_copy(deep=True) for key, value in self._definitions.items()}

        def load(identifier: str) -> AgentConfig | SandboxAgentConfig:
            definition = records[identifier]
            if not definition.enabled:
                raise ValueError(f"Agent is disabled: {identifier}")
            return definition.config

        async with build_agent(
            agent_id, load=load, bindings=bindings, max_depth=max_depth, max_agents=max_agents
        ) as result:
            yield result
