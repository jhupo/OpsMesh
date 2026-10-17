"""Generated capabilities API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``capabilities.channels.create``
- ``capabilities.channels.update_draft``
- ``capabilities.channels.package.attach``
- ``capabilities.definitions.list``
- ``capabilities.definitions.read``
- ``capabilities.definitions.update``
- ``capabilities.definitions.update_status``
- ``capabilities.versions.list``
- ``capabilities.versions.read``
- ``capabilities.versions.package.read``
- ``capabilities.versions.publish``
- ``capabilities.definitions.update_visibility``
- ``capabilities.experts.create``
- ``capabilities.experts.update_draft``
- ``capabilities.experts.package.attach``
- ``capabilities.extensions.create``
- ``capabilities.extensions.update_draft``
- ``capabilities.extensions.package.attach``
- ``capabilities.mcp.create``
- ``capabilities.mcp.update_draft``
- ``capabilities.mcp.package.attach``
- ``capabilities.skills.create``
- ``capabilities.skills.package.attach``
- ``capabilities.teams.create``
- ``capabilities.teams.update_draft``
- ``capabilities.teams.package.attach``
- ``capabilities.tools.create``
- ``capabilities.tools.update_draft``
- ``capabilities.tools.package.attach``
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
    decode_array,
    decode_binary,
)


class AgentAsTool(BaseModel):
    """One member exposed to another member as a callable tool."""

    source: str
    tool: str


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


class CapabilitySlot(BaseModel):
    """One named capability slot an expert fills through fixed dependencies."""

    kind: str
    slot: str


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
    stages: list[str]


class ExtensionOperation(BaseModel):
    """One declared operation: typed I/O, effects, timeout and permissions."""

    description: str
    effects: list[str] | None = None
    export_id: str
    input_schema: dict[str, object] | None = None
    output_schema: dict[str, object] | None = None
    permissions: list[str] | None = None
    timeout_seconds: int = 60


class Handoff(BaseModel):
    """One directed handoff edge between two member slots."""

    source: str
    target: str


class McpToolDeclaration(BaseModel):
    """One advertised tool with its parameter schema; discovery is runtime."""

    description: str = ''
    name: str
    parameters_schema: dict[str, object] | None = None


class ModelRequirements(BaseModel):
    """The model an expert expects; selection itself belongs to execution."""

    model: str
    reasoning: str | None = None


class SkillCreateRequest(BaseModel):
    """Import one skill definition; its configuration comes from the package."""

    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class TeamMember(BaseModel):
    """One expert member filling one named slot with one unique role."""

    expert_key: str
    role: str
    slot: str


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
    secret_slots: list[str] | None = None
    signature_algorithm: str = 'hmac-sha256'


class ExpertConfiguration(BaseModel):
    capability_slots: list[CapabilitySlot] = []
    guardrails: list[str] = []
    instructions: str
    model_requirements: ModelRequirements | None = None
    parameters_schema: dict[str, object] = {}
    prompt: str = ''
    secret_slots: list[str] = []


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
    environment: dict[str, object] | None = None
    parameters_schema: dict[str, object] | None = None
    secret_slots: list[str] | None = None
    tools: list[McpToolDeclaration] | None = None
    transport: str


class TeamConfiguration(BaseModel):
    agents_as_tools: list[AgentAsTool] | None = None
    entrypoint: str
    handoffs: list[Handoff] | None = None
    members: list[TeamMember]
    parameters_schema: dict[str, object] | None = None
    secret_slots: list[str] | None = None


class ToolConfiguration(BaseModel):
    entrypoint: str
    execution_mode: str
    operations: list[ToolOperation]
    parameters_schema: dict[str, object] | None = None
    secret_slots: list[str] | None = None


class ChannelCreateRequest(BaseModel):
    """Create one channel definition with its first draft version."""

    configuration: ChannelConfiguration
    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class ChannelDraftRequest(BaseModel):
    """Replace the current draft configuration of one channel definition."""

    configuration: ChannelConfiguration


class ExpertCreateRequest(BaseModel):
    """Create one expert definition with its first draft version."""

    configuration: ExpertConfiguration
    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class ExpertDraftRequest(BaseModel):
    """Replace the current draft configuration of one expert definition."""

    configuration: ExpertConfiguration


class ExtensionCreateRequest(BaseModel):
    """Create one extension definition with its first draft version."""

    configuration: ExtensionConfiguration
    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class ExtensionDraftRequest(BaseModel):
    """Replace the current draft configuration of one extension definition."""

    configuration: ExtensionConfiguration


class McpCreateRequest(BaseModel):
    """Create one MCP definition with its first draft version."""

    configuration: McpConfiguration
    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class McpDraftRequest(BaseModel):
    """Replace the current draft configuration of one MCP definition."""

    configuration: McpConfiguration


class TeamCreateRequest(BaseModel):
    """Create one team definition with its first draft version."""

    configuration: TeamConfiguration
    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class TeamDraftRequest(BaseModel):
    """Replace the current draft configuration of one team definition."""

    configuration: TeamConfiguration


class ToolCreateRequest(BaseModel):
    """Create one tool definition with its first draft version."""

    configuration: ToolConfiguration
    description: str = ''
    key: str
    name: str
    visibility: str = 'private'


class ToolDraftRequest(BaseModel):
    """Replace the current draft configuration of one tool definition."""

    configuration: ToolConfiguration


class CapabilitiesChannelsPackageClient:
    """Generated client for the ``capabilities.channels.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Attach a channel adapter package to the current draft.

Every adapter entrypoint must name a module the package declares and
carries. Nothing is imported or executed."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/channels/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesChannelsClient:
    """Generated client for the ``capabilities.channels`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesChannelsPackageClient(channel)

    async def create(self, request: ChannelCreateRequest) -> CapabilityVersionView:
        """Create one channel definition with its first draft version."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/channels",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_draft(
        self,
        definition_id: UUID,
        request: ChannelDraftRequest,
    ) -> CapabilityVersionView:
        """Replace the current draft configuration of one channel definition."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/channels/{definition_id}/draft",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesDefinitionsClient:
    """Generated client for the ``capabilities.definitions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

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
    ) -> CapabilityDefinitionPage:
        """One page of the catalog: public definitions plus the caller's own."""
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


class CapabilitiesExpertsPackageClient:
    """Generated client for the ``capabilities.experts.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Attach a resource package to the current expert draft.

The package is verified against the draft's declared manifests and
stored under its digest; nothing inside it is imported or executed."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/experts/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesExpertsClient:
    """Generated client for the ``capabilities.experts`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesExpertsPackageClient(channel)

    async def create(self, request: ExpertCreateRequest) -> CapabilityVersionView:
        """Create one expert definition with its first draft version."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/experts",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_draft(
        self,
        definition_id: UUID,
        request: ExpertDraftRequest,
    ) -> CapabilityVersionView:
        """Replace the current draft configuration of one expert definition."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/experts/{definition_id}/draft",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesExtensionsPackageClient:
    """Generated client for the ``capabilities.extensions.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Attach an extension package to the current draft.

The declared resources and flow templates must exist in the package
with their digests. Nothing is imported or executed."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/extensions/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesExtensionsClient:
    """Generated client for the ``capabilities.extensions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesExtensionsPackageClient(channel)

    async def create(self, request: ExtensionCreateRequest) -> CapabilityVersionView:
        """Create one extension definition with its first draft version."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/extensions",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_draft(
        self,
        definition_id: UUID,
        request: ExtensionDraftRequest,
    ) -> CapabilityVersionView:
        """Replace the current draft configuration of one extension definition."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/extensions/{definition_id}/draft",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesMcpPackageClient:
    """Generated client for the ``capabilities.mcp.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Attach a local MCP package to the current draft.

The package is verified against the draft's declared manifests; the
launch command it declares is only recorded, never executed here."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/mcp/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesMcpClient:
    """Generated client for the ``capabilities.mcp`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesMcpPackageClient(channel)

    async def create(self, request: McpCreateRequest) -> CapabilityVersionView:
        """Create one MCP definition with its first draft version."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/mcp",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_draft(
        self,
        definition_id: UUID,
        request: McpDraftRequest,
    ) -> CapabilityVersionView:
        """Replace the current draft configuration of one MCP definition."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/mcp/{definition_id}/draft",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesSkillsPackageClient:
    """Generated client for the ``capabilities.skills.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Import the standard skill package that becomes the draft content.

The SKILL.md frontmatter and body become the configuration and every
other file becomes a declared resource; scripts stay resources and are
never executed by the import."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/skills/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesSkillsClient:
    """Generated client for the ``capabilities.skills`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesSkillsPackageClient(channel)

    async def create(self, request: SkillCreateRequest) -> CapabilityVersionView:
        """Create one skill definition; its content arrives with the package."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/skills",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesTeamsPackageClient:
    """Generated client for the ``capabilities.teams.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Attach a resource package to the current team draft.

The package is verified against the draft's declared manifests and
stored under its digest; nothing inside it is imported or executed."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/teams/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesTeamsClient:
    """Generated client for the ``capabilities.teams`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesTeamsPackageClient(channel)

    async def create(self, request: TeamCreateRequest) -> CapabilityVersionView:
        """Create one team definition with its first draft version."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/teams",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_draft(
        self,
        definition_id: UUID,
        request: TeamDraftRequest,
    ) -> CapabilityVersionView:
        """Replace the current draft configuration of one team definition."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/teams/{definition_id}/draft",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesToolsPackageClient:
    """Generated client for the ``capabilities.tools.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def attach(
        self,
        definition_id: UUID,
        content: bytes,
        *,
        content_type: str,
    ) -> CapabilityVersionView:
        """Attach a tool package to the current draft.

A script entrypoint must be a file the package declares and carries;
an SDK export entrypoint is checked as an identifier path. Nothing is
imported or executed."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/tools/{definition_id}/draft/package",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesToolsClient:
    """Generated client for the ``capabilities.tools`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.package = CapabilitiesToolsPackageClient(channel)

    async def create(self, request: ToolCreateRequest) -> CapabilityVersionView:
        """Create one tool definition with its first draft version."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/capabilities/tools",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)

    async def update_draft(
        self,
        definition_id: UUID,
        request: ToolDraftRequest,
    ) -> CapabilityVersionView:
        """Replace the current draft configuration of one tool definition."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/capabilities/tools/{definition_id}/draft",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CapabilityVersionView.model_validate(response.body)


class CapabilitiesVersionsPackageClient:
    """Generated client for the ``capabilities.versions.package`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, definition_id: UUID, version_number: int) -> bytes:
        """Download the immutable package bytes of one published version."""
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

    async def list(self, definition_id: UUID) -> list[CapabilityVersionView]:
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
        self.channels = CapabilitiesChannelsClient(channel)
        self.definitions = CapabilitiesDefinitionsClient(channel)
        self.experts = CapabilitiesExpertsClient(channel)
        self.extensions = CapabilitiesExtensionsClient(channel)
        self.mcp = CapabilitiesMcpClient(channel)
        self.skills = CapabilitiesSkillsClient(channel)
        self.teams = CapabilitiesTeamsClient(channel)
        self.tools = CapabilitiesToolsClient(channel)
        self.versions = CapabilitiesVersionsClient(channel)


def create_capabilities_client(channel: GroupChannel) -> CapabilitiesClient:
    """Create the capabilities group client over one composed platform channel."""
    return CapabilitiesClient(channel)


__all__ = [
    "AgentAsTool",
    "CapabilitiesChannelsClient",
    "CapabilitiesChannelsPackageClient",
    "CapabilitiesClient",
    "CapabilitiesDefinitionsClient",
    "CapabilitiesExpertsClient",
    "CapabilitiesExpertsPackageClient",
    "CapabilitiesExtensionsClient",
    "CapabilitiesExtensionsPackageClient",
    "CapabilitiesMcpClient",
    "CapabilitiesMcpPackageClient",
    "CapabilitiesSkillsClient",
    "CapabilitiesSkillsPackageClient",
    "CapabilitiesTeamsClient",
    "CapabilitiesTeamsPackageClient",
    "CapabilitiesToolsClient",
    "CapabilitiesToolsPackageClient",
    "CapabilitiesVersionsClient",
    "CapabilitiesVersionsPackageClient",
    "CapabilityDefinitionPage",
    "CapabilityDefinitionView",
    "CapabilitySlot",
    "CapabilityVersionView",
    "ChannelConfiguration",
    "ChannelCreateRequest",
    "ChannelDraftRequest",
    "ChannelOperation",
    "ExpertConfiguration",
    "ExpertCreateRequest",
    "ExpertDraftRequest",
    "ExtensionConfiguration",
    "ExtensionCreateRequest",
    "ExtensionDraftRequest",
    "ExtensionEvent",
    "ExtensionHook",
    "ExtensionOperation",
    "Handoff",
    "McpConfiguration",
    "McpCreateRequest",
    "McpDraftRequest",
    "McpToolDeclaration",
    "ModelRequirements",
    "SkillCreateRequest",
    "TeamConfiguration",
    "TeamCreateRequest",
    "TeamDraftRequest",
    "TeamMember",
    "ToolConfiguration",
    "ToolCreateRequest",
    "ToolDraftRequest",
    "ToolOperation",
    "UpdateDefinitionRequest",
    "UpdateDefinitionStatusRequest",
    "UpdateDefinitionVisibilityRequest",
    "create_capabilities_client",
]
