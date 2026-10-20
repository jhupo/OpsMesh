"""Public flow authoring and immutable revision contracts."""

from typing import Any
from uuid import UUID

from pydantic import Field

from opsmesh.orchestration.definitions.contracts import FlowValue
from opsmesh.orchestration.definitions.schemas import Permission
from opsmesh.orchestration.execution.contracts import (
    AssetRef,
    HookBudget,
    ModelConfiguration,
    RunOutput,
)


class CapabilitySelection(FlowValue):
    binding_id: UUID
    parameters: dict[str, Any] = Field(default_factory=dict)


class ConfigurationHook(FlowValue):
    binding_id: UUID
    stages: list[str] = Field(min_length=1, max_length=9)
    order: int = Field(default=0, ge=0)
    budget: HookBudget = Field(default_factory=HookBudget)


class ConfigurationSpec(FlowValue):
    models: dict[str, ModelConfiguration] = Field(default_factory=dict, max_length=100)
    capabilities: dict[str, CapabilitySelection] = Field(default_factory=dict, max_length=100)
    hooks: list[ConfigurationHook] = Field(default_factory=list, max_length=64)
    environment_id: UUID | None = None
    assets: list[AssetRef] = Field(default_factory=list, max_length=100)
    outputs: list[RunOutput] = Field(default_factory=list, max_length=100)


class ConfigurationCreate(FlowValue):
    name: str = Field(min_length=1, max_length=120)
    definition_revision_id: UUID
    root_directory_id: UUID | None = None
    specification: ConfigurationSpec | None = None


class ConfigurationUpdate(FlowValue):
    expected_revision: int = Field(ge=0)
    specification: ConfigurationSpec


class ConfigurationCloseRequest(FlowValue):
    expected_revision: int = Field(ge=0)


class ConfigurationClosureView(FlowValue):
    id: UUID
    configuration_id: UUID
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ConfigurationInvitationView(FlowValue):
    id: UUID
    configuration_id: UUID
    status: str


class ConfigurationMembershipView(FlowValue):
    configuration_id: UUID


class ConfigurationView(FlowValue):
    id: UUID
    owner_user_id: UUID
    definition_revision_id: UUID
    root_directory_id: UUID
    name: str
    status: str
    revision: int


class ConfigurationRevisionView(FlowValue):
    id: UUID
    configuration_id: UUID
    number: int
    specification: ConfigurationSpec
    digest: str


class ConfigurationInvitationCreate(FlowValue):
    user_id: UUID
    permissions: list[Permission] = Field(min_length=1, max_length=4)
    directory_access: bool = False
    expires_in_seconds: int = Field(default=604800, ge=60, le=2592000)


class ConfigurationRunCreate(FlowValue):
    configuration_revision: int = Field(ge=1)
    session_id: UUID | None = None
    input: str | list[dict[str, Any]]
