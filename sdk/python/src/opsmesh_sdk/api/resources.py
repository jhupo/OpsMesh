"""Generated resources API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``resources.artifacts.list``
- ``resources.artifacts.read``
- ``resources.artifacts.content.read``
- ``resources.files.list``
- ``resources.files.create``
- ``resources.files.directories.list``
- ``resources.files.directories.create``
- ``resources.files.directories.delete``
- ``resources.files.delete``
- ``resources.files.read``
- ``resources.files.content.read``
- ``resources.files.content.write``
- ``resources.files.versions.diff``
- ``resources.files.grants.list``
- ``resources.files.grants.create``
- ``resources.files.grants.delete``
- ``resources.files.runtime_policy.read``
- ``resources.files.runtime_policy.update``
- ``resources.files.versions.list``
- ``resources.files.versions.read``
- ``resources.files.versions.content.read``
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


class ArtifactView(BaseModel):
    artifact_id: UUID
    created_at: datetime
    digest: str
    labels: dict[str, object]
    media_type: str | None
    name: str
    owner_user_id: UUID
    size: int
    source_id: UUID
    source_kind: str


class DirectoryCreateRequest(BaseModel):
    path: str


class DirectoryView(BaseModel):
    created_at: datetime
    directory_id: UUID
    parent_id: UUID | None
    path: str


class FileCreateRequest(BaseModel):
    directory_id: UUID
    name: str


class FileVersionView(BaseModel):
    created_at: datetime
    created_by_user_id: UUID
    digest: str
    file_id: UUID
    media_type: str | None
    size: int
    version: int


class FileView(BaseModel):
    created_at: datetime
    directory_id: UUID
    file_id: UUID
    latest_version: int
    name: str
    owner_user_id: UUID
    updated_at: datetime


class GrantCreateRequest(BaseModel):
    grantee_user_id: UUID
    permissions: list[str]


class GrantView(BaseModel):
    created_at: datetime
    grant_id: UUID
    grantee_user_id: UUID
    permissions: list[str]


class OutputDeclaration(BaseModel):
    """One declared execution output: a safe relative path and its media type.

The path is where a run may write back inside the granted workspace;
it is validated as a safe relative path again in the service, because
a persisted declaration is later resolved against a storage root."""

    description: str = ''
    media_type: str | None = None
    path: str


class VersionDiffView(BaseModel):
    from_size: int
    from_version: int
    identical: bool
    size_delta: int
    to_size: int
    to_version: int


class FilePage(BaseModel):
    items: list[FileView]
    total: int


class RuntimePolicyRequest(BaseModel):
    frozen_input_versions: list[int] | None = None
    output_declarations: list[OutputDeclaration] | None = None
    relative_paths: list[str] | None = None
    use_in_execution: bool = False


class RuntimePolicyView(BaseModel):
    file_id: UUID
    frozen_input_versions: list[int]
    output_declarations: list[OutputDeclaration]
    relative_paths: list[str]
    updated_at: datetime
    use_in_execution: bool


class ResourcesArtifactsContentClient:
    """Generated client for the ``resources.artifacts.content`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, artifact_id: UUID) -> bytes:
        """Download one artifact's verified bytes."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/artifacts/{artifact_id}/content",
            ),
            self._channel.context(),
        )
        return decode_binary(response)


class ResourcesArtifactsClient:
    """Generated client for the ``resources.artifacts`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.content = ResourcesArtifactsContentClient(channel)

    async def list(
        self,
        *,
        source: str | None = None,
        source_id: UUID | None = None,
    ) -> list[ArtifactView]:
        """One page-free listing of the reader's own artifacts."""
        query = {
            name: str(value)
            for name, value in (
                ("source", source),
                ("source_id", source_id),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/artifacts",
                query=query,
            ),
            self._channel.context(),
        )
        return [ArtifactView.model_validate(item) for item in decode_array(response)]

    async def read(self, artifact_id: UUID) -> ArtifactView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/artifacts/{artifact_id}",
            ),
            self._channel.context(),
        )
        return ArtifactView.model_validate(response.body)


class ResourcesFilesContentClient:
    """Generated client for the ``resources.files.content`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, file_id: UUID) -> bytes:
        """Download the current version's verified bytes."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/content",
            ),
            self._channel.context(),
        )
        return decode_binary(response)

    async def write(
        self,
        file_id: UUID,
        content: bytes,
        *,
        expected_version: int | None = None,
        media_type: str | None = None,
        idempotency_key: str | None = None,
        content_type: str,
    ) -> FileVersionView:
        """Store one new version, optionally under a compare-and-set expectation."""
        query = {
            name: str(value)
            for name, value in (
                ("expected_version", expected_version),
                ("media_type", media_type),
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/resources/files/{file_id}/content",
                query=query,
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return FileVersionView.model_validate(response.body)


class ResourcesFilesDirectoriesClient:
    """Generated client for the ``resources.files.directories`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self) -> list[DirectoryView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/directories",
            ),
            self._channel.context(),
        )
        return [DirectoryView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: DirectoryCreateRequest) -> DirectoryView:
        """Create one user directory at a validated relative path."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/resources/files/directories",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return DirectoryView.model_validate(response.body)

    async def delete(self, directory_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/resources/files/directories/{directory_id}",
            ),
            self._channel.context(),
        )


class ResourcesFilesGrantsClient:
    """Generated client for the ``resources.files.grants`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, file_id: UUID) -> list[GrantView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/grants",
            ),
            self._channel.context(),
        )
        return [GrantView.model_validate(item) for item in decode_array(response)]

    async def create(self, file_id: UUID, request: GrantCreateRequest) -> GrantView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/resources/files/{file_id}/grants",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return GrantView.model_validate(response.body)

    async def delete(self, file_id: UUID, grant_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/resources/files/{file_id}/grants/{grant_id}",
            ),
            self._channel.context(),
        )


class ResourcesFilesRuntimePolicyClient:
    """Generated client for the ``resources.files.runtime_policy`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, file_id: UUID) -> RuntimePolicyView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/runtime-policy",
            ),
            self._channel.context(),
        )
        return RuntimePolicyView.model_validate(response.body)

    async def update(self, file_id: UUID, request: RuntimePolicyRequest) -> RuntimePolicyView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/resources/files/{file_id}/runtime-policy",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RuntimePolicyView.model_validate(response.body)


class ResourcesFilesVersionsContentClient:
    """Generated client for the ``resources.files.versions.content`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, file_id: UUID, version_number: int) -> bytes:
        """Download one version's verified bytes."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/versions/{version_number}/content",
            ),
            self._channel.context(),
        )
        return decode_binary(response)


class ResourcesFilesVersionsClient:
    """Generated client for the ``resources.files.versions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.content = ResourcesFilesVersionsContentClient(channel)

    async def diff(self, file_id: UUID, *, from_version: int, to_version: int) -> VersionDiffView:
        query = {
            name: str(value)
            for name, value in (
                ("from_version", from_version),
                ("to_version", to_version),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/diff",
                query=query,
            ),
            self._channel.context(),
        )
        return VersionDiffView.model_validate(response.body)

    async def list(self, file_id: UUID) -> list[FileVersionView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/versions",
            ),
            self._channel.context(),
        )
        return [FileVersionView.model_validate(item) for item in decode_array(response)]

    async def read(self, file_id: UUID, version_number: int) -> FileVersionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}/versions/{version_number}",
            ),
            self._channel.context(),
        )
        return FileVersionView.model_validate(response.body)


class ResourcesFilesClient:
    """Generated client for the ``resources.files`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.content = ResourcesFilesContentClient(channel)
        self.directories = ResourcesFilesDirectoriesClient(channel)
        self.grants = ResourcesFilesGrantsClient(channel)
        self.runtime_policy = ResourcesFilesRuntimePolicyClient(channel)
        self.versions = ResourcesFilesVersionsClient(channel)

    async def list(self, *, limit: int = 50, offset: int = 0) -> FilePage:
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
                path=f"{self._channel.api_root}/resources/files",
                query=query,
            ),
            self._channel.context(),
        )
        return FilePage.model_validate(response.body)

    async def create(self, request: FileCreateRequest) -> FileView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/resources/files",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return FileView.model_validate(response.body)

    async def delete(self, file_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/resources/files/{file_id}",
            ),
            self._channel.context(),
        )

    async def read(self, file_id: UUID) -> FileView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/resources/files/{file_id}",
            ),
            self._channel.context(),
        )
        return FileView.model_validate(response.body)


class ResourcesClient:
    """The resources API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.artifacts = ResourcesArtifactsClient(channel)
        self.files = ResourcesFilesClient(channel)


def create_resources_client(channel: GroupChannel) -> ResourcesClient:
    """Create the resources group client over one composed platform channel."""
    return ResourcesClient(channel)


__all__ = [
    "ArtifactView",
    "DirectoryCreateRequest",
    "DirectoryView",
    "FileCreateRequest",
    "FilePage",
    "FileVersionView",
    "FileView",
    "GrantCreateRequest",
    "GrantView",
    "OutputDeclaration",
    "ResourcesArtifactsClient",
    "ResourcesArtifactsContentClient",
    "ResourcesClient",
    "ResourcesFilesClient",
    "ResourcesFilesContentClient",
    "ResourcesFilesDirectoriesClient",
    "ResourcesFilesGrantsClient",
    "ResourcesFilesRuntimePolicyClient",
    "ResourcesFilesVersionsClient",
    "ResourcesFilesVersionsContentClient",
    "RuntimePolicyRequest",
    "RuntimePolicyView",
    "VersionDiffView",
    "create_resources_client",
]
