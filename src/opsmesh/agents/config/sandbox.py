"""SandboxAgent adds exactly the native sandbox constructor settings."""

from typing import Annotated, Literal

from agents.sandbox import User
from pydantic import Field, TypeAdapter

from .agent import AgentConfig, BaseAgentConfig
from .callbacks import CallbackRef
from .capabilities import SandboxCapabilityConfig
from .manifest import ManifestConfig, default_workspace_manifest


class SandboxAgentConfig(BaseAgentConfig):
    type: Literal["sandbox"] = "sandbox"
    default_manifest: ManifestConfig | None = Field(default_factory=default_workspace_manifest)
    base_instructions: str | CallbackRef | None = None
    # None preserves SDK defaults; [] explicitly disables every capability.
    capabilities: list[SandboxCapabilityConfig] | None = None
    run_as: User | str | None = None


AgentConfiguration = Annotated[AgentConfig | SandboxAgentConfig, Field(discriminator="type")]


def parse_config(value: object) -> AgentConfig | SandboxAgentConfig:
    return TypeAdapter(AgentConfiguration).validate_python(value)
