"""Generated capabilities API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``capabilities.definitions.list``
- ``capabilities.definitions.create``
- ``capabilities.definitions.read``
- ``capabilities.definitions.update``
- ``capabilities.definitions.update_draft``
- ``capabilities.definitions.package.attach``
- ``capabilities.definitions.update_status``
- ``capabilities.versions.list``
- ``capabilities.versions.read``
- ``capabilities.versions.package.read``
- ``capabilities.publication_reviews.submit``
- ``capabilities.versions.publish``
- ``capabilities.definitions.update_visibility``
- ``capabilities.publication_reviews.admin_list``
- ``capabilities.publication_reviews.decide``
- ``capabilities.publication_reviews.mine``
- ``capabilities.publication_reviews.read``
- ``capabilities.publication_reviews.withdraw``
"""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
    decode_array,
    decode_binary,
)


class CapabilityDefinitionView(BaseModel):
    """One definition as the catalog exposes it; content lives in versions."""

    created_at: datetime
    created_by_user_id: UUID | None
    definition_id: UUID
    description: str
    has_published_version: bool
    key: str
    latest_version: int
    name: str
    source: str
    status: str
    type: str
    updated_at: datetime
    visibility: str


class CapabilityVersionView(BaseModel):
    """One immutable-or-draft version with its full declared content."""

    configuration: dict[str, object]
    created_at: datetime
    definition_id: UUID
    dependencies: list[dict[str, object]]
    digest: str | None
    flow_templates: list[dict[str, object]]
    package_digest: str | None
    package_filename: str | None
    package_size: int | None
    published_at: datetime | None
    published_by_user_id: UUID | None
    resource_manifest: list[dict[str, object]]
    status: str
    version: int
    version_id: UUID


class ChannelOperation(BaseModel):
    """One adapter operation with its identifier and typed I/O schemas."""

    description: str = ''
    entrypoint: str
    input_schema: dict[str, object] | None = None
    name: str
    output_schema: dict[str, object] | None = None


class ExtensionEvent(BaseModel):
    """One declared event consumer with its operation and idempotency fields."""

    description: str
    export_id: str
    filter_scope: list[str] | None = None
    idempotency_key_fields: list[str]
    operation_export_id: str
    source_types: list[str]
    source_versions: list[str]


class ExtensionHook(BaseModel):
    """One declared hook bound to fixed stages with a failure policy."""

    description: str
    export_id: str
    failure_policy: str = 'block'
    input_schema: dict[str, object] | None = None
    patch_fields: list[str] | None = None
    permissions: list[str] | None = None
    stages: list[str]
    timeout_seconds: int = 60


class ExtensionOperation(BaseModel):
    """One declared operation: typed I/O, effects, timeout and permissions."""

    description: str
    effects: list[str] | None = None
    export_id: str
    input_schema: dict[str, object] | None = None
    output_schema: dict[str, object] | None = None
    permissions: list[str] | None = None
    timeout_seconds: int = 60


class McpToolDeclaration(BaseModel):
    """One advertised tool with its parameter schema; discovery is runtime."""

    description: str = ''
    name: str
    parameters_schema: dict[str, object] | None = None


class PublicationReviewDecision(BaseModel):
    decision: str
    decision_note: str = ''


class PublicationReviewSubmission(BaseModel):
    submission_note: str = ''


class SkillFrontmatter(BaseModel):
    """The standard SKILL.md metadata block."""

    description: str
    license: str | None = None
    name: str


class ToolOperation(BaseModel):
    """One callable operation with its schemas, effects and approval demand."""

    description: str = ''
    effects: list[str] | None = None
    input_schema: dict[str, object] | None = None
    name: str
    output_schema: dict[str, object] | None = None
    requires_approval: bool = False
    timeout_seconds: int = 60


class UpdateDefinitionRequest(BaseModel):
    """The definition-level metadata an author may change."""

    description: str | None = None
    name: str | None = None


class UpdateDefinitionStatusRequest(BaseModel):
    status: str


class UpdateDefinitionVisibilityRequest(BaseModel):
    visibility: str


class CapabilityDefinitionPage(BaseModel):
    items: list[CapabilityDefinitionView]
    total: int


class ChannelConfiguration(BaseModel):
    """The vendor protocol one channel definition speaks.

A channel declares one vendor protocol and the adapter operations
that speak it. The declaration is static: an entrypoint is an
identifier the package must carry as a declared resource, and no
vendor client is constructed, no signature is verified and no
message is exchanged at publish time."""

    operations: list[ChannelOperation]
    parameters_schema: dict[str, object] | None = None
    protocol: str
    signature_algorithm: Literal['hmac-sha256'] = 'hmac-sha256'


class ExtensionConfiguration(BaseModel):
    """The export contract one extension definition publishes.

An extension declares the exports its package installs with — typed
operations with their effects, hooks bound to fixed stages, and event
consumers — plus the permissions those exports need. The declaration
is frozen at publish; nothing is imported, registered or run here."""

    events: list[ExtensionEvent] | None = None
    hooks: list[ExtensionHook] | None = None
    operations: list[ExtensionOperation] | None = None
    permissions: list[str] | None = None


class McpConfiguration(BaseModel):
    arguments: list[str] | None = None
    command: list[str] | None = None
    endpoint: str | None = None
    environment: dict[str, str] | None = None
    parameters_schema: dict[str, object] | None = None
    tools: list[McpToolDeclaration] | None = None
    transport: str


class PublicationReviewView(BaseModel):
    capability_type: str
    created_at: datetime
    decision_note: str | None
    definition_id: UUID
    key: str
    name: str
    requested_by_user_id: UUID
    review_id: UUID
    reviewed_at: datetime | None
    reviewed_by_user_id: UUID | None
    status: str
    submission_note: str
    version: CapabilityVersionView
    version_id: UUID
    version_number: int


class SkillConfiguration(BaseModel):
    frontmatter: SkillFrontmatter
    instructions: str
    parameters_schema: dict[str, object] | None = None


class opsmesh__capabilities__tools__schemas__ToolConfiguration(BaseModel):
    entrypoint: str
    execution_mode: str
    operations: list[ToolOperation]
    parameters_schema: dict[str, object] | None = None


class ChannelDeclaration(BaseModel):
    configuration: ChannelConfiguration
    type: Literal['channel']


class ExtensionDeclaration(BaseModel):
    configuration: ExtensionConfiguration
    type: Literal['extension']


class McpDeclaration(BaseModel):
    configuration: McpConfiguration
    type: Literal['mcp']


class SkillDeclaration(BaseModel):
    configuration: SkillConfiguration | None = None
    type: Literal['skill']


class ToolDeclaration(BaseModel):
    configuration: opsmesh__capabilities__tools__schemas__ToolConfiguration
    type: Literal['tool']


class DefinitionInput(BaseModel):
    """Common definition metadata; the discriminated declaration chooses its type."""

    declaration: Annotated[
        McpDeclaration
        | ToolDeclaration
        | SkillDeclaration
        | ChannelDeclaration
        | ExtensionDeclaration,
        Field(discriminator='type'),
    ]
    dependencies: list[dict[str, object]] | None = None
    description: str = ''
    flow_templates: list[dict[str, object]] | None = None
    key: str
    name: str
    resource_manifest: list[dict[str, object]] | None = None
    source: str = 'user'
    visibility: str = 'private'


class DraftInput(BaseModel):
    """Complete static draft input; publishing freezes this validated content."""

    declaration: Annotated[
        McpDeclaration
        | ToolDeclaration
        | SkillDeclaration
        | ChannelDeclaration
        | ExtensionDeclaration,
        Field(discriminator='type'),
    ]
    dependencies: list[dict[str, object]] | None = None
    flow_templates: list[dict[str, object]] | None = None
    resource_manifest: list[dict[str, object]] | None = None


class CapabilitiesDefinitionsPackageClient:
    """Generated client for the ``capabilities.definitions.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        filename: str = 'package.zip',
        content_type: str,
    ) -> CapabilityVersionView:
        query = {
            name: str(value)
            for name, value in (
                ("filename", filename),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/draft/package",
                query=query,
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesDefinitionsClient:
    """Generated client for the ``capabilities.definitions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesDefinitionsPackageClient(channel)

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        type: str | None = None,
        visibility: str | None = None,
        source: str | None = None,
        status: str | None = None,
        key_query: str | None = None,
        owned_only: bool = False,
    ) -> CapabilityDefinitionPage:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("type", type),
                ("visibility", visibility),
                ("source", source),
                ("status", status),
                ("key_query", key_query),
                ("owned_only", owned_only),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/definitions",
                query=query,
            ),
            self._channel.context(),
        )
        return CapabilityDefinitionPage.model_validate(response.body)

    async def create(self, request: DefinitionInput) -> CapabilityVersionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/definitions",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def read(self, definition_id: UUID) -> CapabilityDefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}",
            ),
            self._channel.context(),
        )
        return CapabilityDefinitionView.model_validate(response.body)

    async def update(
        self,
        definition_id: UUID,
        request: UpdateDefinitionRequest,
    ) -> CapabilityDefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityDefinitionView.model_validate(response.body)

    async def update_draft(self, definition_id: UUID, request: DraftInput) -> CapabilityVersionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/draft",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_status(
        self,
        definition_id: UUID,
        request: UpdateDefinitionStatusRequest,
    ) -> CapabilityDefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/status",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityDefinitionView.model_validate(response.body)

    async def update_visibility(
        self,
        definition_id: UUID,
        request: UpdateDefinitionVisibilityRequest,
    ) -> CapabilityDefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/visibility",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityDefinitionView.model_validate(response.body)


class CapabilitiesPublicationReviewsClient:
    """Generated client for the ``capabilities.publication_reviews`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def submit(
        self,
        definition_id: UUID,
        version_number: int,
        request: PublicationReviewSubmission,
    ) -> PublicationReviewView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/versions/{version_number}/publication-review",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return PublicationReviewView.model_validate(response.body)

    async def admin_list(
        self,
        *,
        status: str = 'pending',
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[PublicationReviewView]:
        query = {
            name: str(value)
            for name, value in (
                ("status", status),
                ("limit", limit),
                ("offset", offset),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/publication-reviews/admin",
                query=query,
            ),
            self._channel.context(),
        )
        return [PublicationReviewView.model_validate(item) for item in decode_array(response)]

    async def decide(
        self,
        review_id: UUID,
        request: PublicationReviewDecision,
    ) -> PublicationReviewView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/publication-reviews/admin/{review_id}/decision",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return PublicationReviewView.model_validate(response.body)

    async def mine(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[PublicationReviewView]:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/publication-reviews/mine",
                query=query,
            ),
            self._channel.context(),
        )
        return [PublicationReviewView.model_validate(item) for item in decode_array(response)]

    async def read(self, review_id: UUID) -> PublicationReviewView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/publication-reviews/{review_id}",
            ),
            self._channel.context(),
        )
        return PublicationReviewView.model_validate(response.body)

    async def withdraw(self, review_id: UUID) -> PublicationReviewView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/publication-reviews/{review_id}/withdraw",
            ),
            self._channel.context(),
        )
        return PublicationReviewView.model_validate(response.body)


class CapabilitiesVersionsPackageClient:
    """Generated client for the ``capabilities.versions.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, definition_id: UUID, version_number: int) -> bytes:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/versions/{version_number}/package",
            ),
            self._channel.context(),
        )
        return decode_binary(response)


class CapabilitiesVersionsClient:
    """Generated client for the ``capabilities.versions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesVersionsPackageClient(channel)

    async def list(self, definition_id: UUID) -> builtins.list[CapabilityVersionView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/versions",
            ),
            self._channel.context(),
        )
        return [CapabilityVersionView.model_validate(item) for item in decode_array(response)]

    async def read(self, definition_id: UUID, version_number: int) -> CapabilityVersionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/versions/{version_number}",
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def publish(self, definition_id: UUID, version_number: int) -> CapabilityVersionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/definitions/{definition_id}/versions/{version_number}/publish",
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesClient:
    """The capabilities API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.definitions = CapabilitiesDefinitionsClient(channel)
        self.publication_reviews = CapabilitiesPublicationReviewsClient(channel)
        self.versions = CapabilitiesVersionsClient(channel)


def create_capabilities_client(channel: GroupChannel) -> CapabilitiesClient:
    """Create the capabilities group client over one composed platform channel."""
    return CapabilitiesClient(channel)


__all__ = [
    "CapabilitiesClient",
    "CapabilitiesDefinitionsClient",
    "CapabilitiesDefinitionsPackageClient",
    "CapabilitiesPublicationReviewsClient",
    "CapabilitiesVersionsClient",
    "CapabilitiesVersionsPackageClient",
    "CapabilityDefinitionPage",
    "CapabilityDefinitionView",
    "CapabilityVersionView",
    "ChannelConfiguration",
    "ChannelDeclaration",
    "ChannelOperation",
    "DefinitionInput",
    "DraftInput",
    "ExtensionConfiguration",
    "ExtensionDeclaration",
    "ExtensionEvent",
    "ExtensionHook",
    "ExtensionOperation",
    "McpConfiguration",
    "McpDeclaration",
    "McpToolDeclaration",
    "PublicationReviewDecision",
    "PublicationReviewSubmission",
    "PublicationReviewView",
    "SkillConfiguration",
    "SkillDeclaration",
    "SkillFrontmatter",
    "ToolDeclaration",
    "ToolOperation",
    "UpdateDefinitionRequest",
    "UpdateDefinitionStatusRequest",
    "UpdateDefinitionVisibilityRequest",
    "create_capabilities_client",
    "opsmesh__capabilities__tools__schemas__ToolConfiguration",
]
