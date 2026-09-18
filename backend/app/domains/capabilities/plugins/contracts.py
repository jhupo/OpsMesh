from typing import Literal
from uuid import UUID

from opsmesh_plugin_sdk.packages import SignedPluginPackage
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.contracts import TimestampedModel


class TrustKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    plugin_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    public_key: str = Field(min_length=44, max_length=44)


class TrustKeyResponse(TimestampedModel):
    workspace_id: UUID
    key_id: str
    plugin_key: str
    public_key: str
    status: str


class CapabilityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: UUID


class PluginInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: SignedPluginPackage
    bindings: dict[str, CapabilityBinding] = Field(min_length=1, max_length=128)
    approved_permissions: list[str] = Field(default_factory=list, max_length=128)
    expected_generation: int | None = Field(default=None, ge=1)


class PluginAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["enable", "disable", "switch_version", "retire_version", "uninstall"]
    expected_generation: int = Field(ge=1)
    version: str | None = Field(default=None, max_length=64)


class PluginInstallResponse(TimestampedModel):
    workspace_id: UUID
    plugin_key: str
    status: str
    generation: int
    current_version: str


class PluginReleaseResponse(TimestampedModel):
    workspace_id: UUID
    install_id: UUID
    version: str
    status: str
    checksum: str
    package: dict[str, object]
    approved_permissions: list[str]


class PluginBindingResponse(TimestampedModel):
    capability_key: str
    kind: str
    resource_id: UUID
    release_id: UUID
    configuration: dict[str, object]
