"""Capability catalog, version and package HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.capabilities.models import CapabilityVersion

CapabilityTypeName = Literal[
    "expert", "team", "mcp", "skill", "tool", "channel", "extension"
]
VisibilityName = Literal["private", "public"]
DefinitionStatusName = Literal["active", "disabled", "deleted"]
VersionStatusName = Literal["draft", "published"]


class CapabilityDefinitionView(BaseModel):
    """One definition as the catalog exposes it; content lives in versions."""

    model_config = ConfigDict(extra="forbid")

    definition_id: UUID
    type: str
    key: str
    name: str
    description: str
    visibility: str
    status: str
    source: str
    created_by_user_id: UUID | None
    latest_version: int
    has_published_version: bool
    created_at: datetime
    updated_at: datetime


class CapabilityDefinitionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CapabilityDefinitionView]
    total: int


class CapabilityVersionView(BaseModel):
    """One immutable-or-draft version with its full declared content."""

    model_config = ConfigDict(extra="forbid")

    definition_id: UUID
    version: int
    status: str
    configuration: dict[str, object]
    resource_manifest: list[dict[str, object]]
    dependencies: list[dict[str, object]]
    flow_templates: list[dict[str, object]]
    digest: str | None
    package_digest: str | None
    package_size: int | None
    package_filename: str | None
    published_at: datetime | None
    published_by_user_id: UUID | None
    created_at: datetime


class UpdateDefinitionRequest(BaseModel):
    """The definition-level metadata an author may change."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class UpdateDefinitionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DefinitionStatusName


class UpdateDefinitionVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: VisibilityName


def version_view(version: CapabilityVersion, package_filename: str | None) -> CapabilityVersionView:
    """Map one stored version onto its HTTP contract."""
    return CapabilityVersionView(
        definition_id=version.definition_id,
        version=version.version,
        status=version.status,
        configuration=version.configuration,
        resource_manifest=list(version.resource_manifest),
        dependencies=list(version.dependencies),
        flow_templates=list(version.flow_templates),
        digest=version.digest,
        package_digest=version.package_digest,
        package_size=version.package_size,
        package_filename=package_filename,
        published_at=version.published_at,
        published_by_user_id=version.published_by_user_id,
        created_at=version.created_at,
    )
