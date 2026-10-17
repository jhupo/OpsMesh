"""Generated workspaces API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``workspaces.list``
- ``workspaces.create``
- ``workspaces.invitations.received``
- ``workspaces.invitations.accept``
- ``workspaces.projects.invitations.received``
- ``workspaces.projects.invitations.accept``
- ``workspaces.delete``
- ``workspaces.read``
- ``workspaces.update``
- ``workspaces.archive``
- ``workspaces.capacity.read``
- ``workspaces.capacity.update_limit``
- ``workspaces.credentials.list``
- ``workspaces.credentials.create``
- ``workspaces.credentials.read``
- ``workspaces.credentials.revoke``
- ``workspaces.credentials.rotate``
- ``workspaces.members.list``
- ``workspaces.invitations.list``
- ``workspaces.invitations.create``
- ``workspaces.invitations.revoke``
- ``workspaces.members.remove``
- ``workspaces.members.update_role``
- ``workspaces.model_providers.list``
- ``workspaces.model_providers.create``
- ``workspaces.model_providers.read_default``
- ``workspaces.model_providers.disable``
- ``workspaces.model_providers.read``
- ``workspaces.model_providers.update``
- ``workspaces.model_providers.set_default``
- ``workspaces.model_providers.read_probe``
- ``workspaces.model_providers.probe``
- ``workspaces.notifications.list``
- ``workspaces.notifications.read_preferences``
- ``workspaces.notifications.update_preferences``
- ``workspaces.notifications.mark_read``
- ``workspaces.notifications.archive``
- ``workspaces.projects.list``
- ``workspaces.projects.create``
- ``workspaces.projects.delete``
- ``workspaces.projects.read``
- ``workspaces.projects.update``
- ``workspaces.projects.archive``
- ``workspaces.projects.members.list``
- ``workspaces.projects.invitations.list``
- ``workspaces.projects.invitations.create``
- ``workspaces.projects.invitations.revoke``
- ``workspaces.projects.members.remove``
- ``workspaces.projects.members.update_role``
- ``workspaces.transfer_ownership``
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
)


class CapacityKindView(BaseModel):
    kind: str
    limit_value: int | None
    reserved_value: int


class CapacityLimitUpdateRequest(BaseModel):
    limit_value: int | None = None


class CredentialCreateRequest(BaseModel):
    kind: str
    name: str
    secret: dict[str, object]


class CredentialRotateRequest(BaseModel):
    secret: dict[str, object]


class CredentialView(BaseModel):
    created_at: datetime
    created_by_user_id: UUID
    credential_id: UUID
    fingerprint: str
    key_id: str
    kind: str
    name: str
    revision: int
    revoked_at: datetime | None
    status: str
    updated_at: datetime
    workspace_id: UUID


class DefaultModelView(BaseModel):
    """The resolved default model of one workspace; no secret is part of it."""

    base_url: str | None
    connection_id: UUID
    default_model: str
    provider: str


class InvitationCreateRequest(BaseModel):
    invitee_user_id: UUID
    role: str
    ttl_seconds: int = 604800


class MarkReadRequest(BaseModel):
    mark_all: bool = False
    notification_ids: list[UUID] | None = None


class MarkReadView(BaseModel):
    marked_count: int


class MemberRoleUpdateRequest(BaseModel):
    role: str


class ModelProviderCreateRequest(BaseModel):
    base_url: str | None = None
    credential_id: UUID
    default_model: str
    is_default: bool = False
    models: list[str] | None = None
    name: str


class ModelProviderUpdateRequest(BaseModel):
    base_url: str | None = None
    default_model: str | None = None
    is_default: bool | None = None
    models: list[str] | None = None
    name: str | None = None


class ModelProviderView(BaseModel):
    base_url: str | None
    connection_id: UUID
    created_at: datetime
    credential_id: UUID
    default_model: str
    is_default: bool
    last_probe_at: datetime | None
    last_probe_detail: str | None
    last_probe_status: str
    models: list[str]
    name: str
    provider: str
    status: str
    updated_at: datetime
    workspace_id: UUID


class NotificationPreferenceUpdateRequest(BaseModel):
    email_enabled: bool | None = None
    in_app_enabled: bool | None = None


class NotificationPreferenceView(BaseModel):
    email_enabled: bool
    in_app_enabled: bool
    user_id: UUID
    workspace_id: UUID


class NotificationView(BaseModel):
    archived_at: datetime | None
    body: str
    created_at: datetime
    notification_id: UUID
    notification_type: str
    payload: dict[str, object]
    read_at: datetime | None
    recipient_user_id: UUID
    severity: str
    source_id: UUID | None
    source_type: str
    title: str
    workspace_id: UUID


class ProbeView(BaseModel):
    connection_id: UUID
    detail: str | None
    probed_at: datetime | None
    status: str


class ProjectCreateRequest(BaseModel):
    description: str = ''
    name: str


class ProjectInvitationView(BaseModel):
    created_at: datetime
    expires_at: datetime
    invitation_id: UUID
    invited_by_user_id: UUID
    invitee_user_id: UUID
    project_id: UUID
    role: str
    state: str
    workspace_id: UUID


class ProjectMemberView(BaseModel):
    created_at: datetime
    project_id: UUID
    role: str
    updated_at: datetime
    user_id: UUID
    workspace_id: UUID


class ProjectUpdateRequest(BaseModel):
    description: str | None = None
    name: str | None = None


class ProjectView(BaseModel):
    created_at: datetime
    created_by_user_id: UUID
    description: str
    name: str
    project_id: UUID
    status: str
    updated_at: datetime
    workspace_id: UUID


class SpaceInvitationView(BaseModel):
    created_at: datetime
    expires_at: datetime
    invitation_id: UUID
    invited_by_user_id: UUID
    invitee_user_id: UUID
    role: str
    state: str
    workspace_id: UUID


class SpaceMemberView(BaseModel):
    created_at: datetime
    role: str
    updated_at: datetime
    user_id: UUID
    workspace_id: UUID


class TransferOwnershipRequest(BaseModel):
    new_owner_user_id: UUID


class WorkspaceCreateRequest(BaseModel):
    description: str = ''
    name: str


class WorkspaceUpdateRequest(BaseModel):
    description: str | None = None
    name: str | None = None


class WorkspaceView(BaseModel):
    created_at: datetime
    created_by_user_id: UUID
    description: str
    name: str
    status: str
    updated_at: datetime
    workspace_id: UUID


class CapacitySnapshotView(BaseModel):
    kinds: list[CapacityKindView]
    workspace_id: str


class CredentialPage(BaseModel):
    credentials: list[CredentialView]
    total: int


class ModelProviderPage(BaseModel):
    connections: list[ModelProviderView]
    total: int


class NotificationPage(BaseModel):
    notifications: list[NotificationView]
    total: int
    unread_count: int


class ProjectInvitationPage(BaseModel):
    invitations: list[ProjectInvitationView]
    total: int


class ProjectMemberPage(BaseModel):
    members: list[ProjectMemberView]
    total: int


class ProjectPage(BaseModel):
    projects: list[ProjectView]
    total: int


class SpaceInvitationPage(BaseModel):
    invitations: list[SpaceInvitationView]
    total: int


class SpaceMemberPage(BaseModel):
    members: list[SpaceMemberView]
    total: int


class WorkspacePage(BaseModel):
    total: int
    workspaces: list[WorkspaceView]


class WorkspacesCapacityClient:
    """Generated client for the ``workspaces.capacity`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, workspace_id: UUID) -> CapacitySnapshotView:
        """Read every capacity kind's limit and held reservations."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/capacity",
            ),
            self._channel.context(),
        )
        return CapacitySnapshotView.model_validate(response.body)

    async def update_limit(
        self,
        workspace_id: UUID,
        kind: str,
        request: CapacityLimitUpdateRequest,
    ) -> CapacitySnapshotView:
        """Replace one capacity kind's limit; below the held amount is refused."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/capacity/{kind}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return CapacitySnapshotView.model_validate(response.body)


class WorkspacesCredentialsClient:
    """Generated client for the ``workspaces.credentials`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, workspace_id: UUID, *, limit: int = 50, offset: int = 0) -> CredentialPage:
        """List the workspace's credential metadata."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/credentials",
                query=query,
            ),
            self._channel.context(),
        )
        return CredentialPage.model_validate(response.body)

    async def create(self, workspace_id: UUID, request: CredentialCreateRequest) -> CredentialView:
        """Store one encrypted workspace secret."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/credentials",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CredentialView.model_validate(response.body)

    async def read(self, workspace_id: UUID, credential_id: UUID) -> CredentialView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/credentials/{credential_id}",
            ),
            self._channel.context(),
        )
        return CredentialView.model_validate(response.body)

    async def revoke(self, workspace_id: UUID, credential_id: UUID) -> CredentialView:
        """Revoke one credential; every future resolution is refused."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/credentials/{credential_id}/revoke",
            ),
            self._channel.context(),
        )
        return CredentialView.model_validate(response.body)

    async def rotate(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        request: CredentialRotateRequest,
    ) -> CredentialView:
        """Replace the stored secret and advance its revision."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/credentials/{credential_id}/rotate",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return CredentialView.model_validate(response.body)


class WorkspacesInvitationsClient:
    """Generated client for the ``workspaces.invitations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def received(self, *, limit: int = 50, offset: int = 0) -> SpaceInvitationPage:
        """List the workspace invitations addressed to the current user."""
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
                path=f"{self._channel.api_root}/workspaces/invitations/received",
                query=query,
            ),
            self._channel.context(),
        )
        return SpaceInvitationPage.model_validate(response.body)

    async def accept(self, invitation_id: UUID) -> SpaceMemberView:
        """Accept one invitation addressed to the current user and join."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/invitations/{invitation_id}/accept",
            ),
            self._channel.context(),
        )
        return SpaceMemberView.model_validate(response.body)

    async def list(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> SpaceInvitationPage:
        """List the invitations of one workspace."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/members/invitations",
                query=query,
            ),
            self._channel.context(),
        )
        return SpaceInvitationPage.model_validate(response.body)

    async def create(
        self,
        workspace_id: UUID,
        request: InvitationCreateRequest,
    ) -> SpaceInvitationView:
        """Invite one active user to the workspace under a stated role."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/members/invitations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return SpaceInvitationView.model_validate(response.body)

    async def revoke(self, workspace_id: UUID, invitation_id: UUID) -> SpaceInvitationView:
        """Revoke one pending invitation; a decided one is never rewritten."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/members/invitations/{invitation_id}",
            ),
            self._channel.context(),
        )
        return SpaceInvitationView.model_validate(response.body)


class WorkspacesMembersClient:
    """Generated client for the ``workspaces.members`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> SpaceMemberPage:
        """List the members of one workspace."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/members",
                query=query,
            ),
            self._channel.context(),
        )
        return SpaceMemberPage.model_validate(response.body)

    async def remove(self, workspace_id: UUID, user_id: UUID) -> None:
        """Remove one member, or leave the workspace as that member."""
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/members/{user_id}",
            ),
            self._channel.context(),
        )

    async def update_role(
        self,
        workspace_id: UUID,
        user_id: UUID,
        request: MemberRoleUpdateRequest,
    ) -> SpaceMemberView:
        """Change one member's space role; ownership moves only by transfer."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/members/{user_id}",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return SpaceMemberView.model_validate(response.body)


class WorkspacesModelProvidersClient:
    """Generated client for the ``workspaces.model_providers`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> ModelProviderPage:
        """List the workspace's model provider connections."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers",
                query=query,
            ),
            self._channel.context(),
        )
        return ModelProviderPage.model_validate(response.body)

    async def create(
        self,
        workspace_id: UUID,
        request: ModelProviderCreateRequest,
    ) -> ModelProviderView:
        """Register one connection over an existing active provider credential."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelProviderView.model_validate(response.body)

    async def read_default(self, workspace_id: UUID) -> DefaultModelView:
        """Resolve the workspace's default model; a missing default is a 404."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/default",
            ),
            self._channel.context(),
        )
        return DefaultModelView.model_validate(response.body)

    async def disable(self, workspace_id: UUID, connection_id: UUID) -> ModelProviderView:
        """Disable one connection; a disabled default steps down with it."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/{connection_id}",
            ),
            self._channel.context(),
        )
        return ModelProviderView.model_validate(response.body)

    async def read(self, workspace_id: UUID, connection_id: UUID) -> ModelProviderView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/{connection_id}",
            ),
            self._channel.context(),
        )
        return ModelProviderView.model_validate(response.body)

    async def update(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        request: ModelProviderUpdateRequest,
    ) -> ModelProviderView:
        """Change one connection's catalog, endpoint or default selection."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/{connection_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelProviderView.model_validate(response.body)

    async def set_default(self, workspace_id: UUID, connection_id: UUID) -> ModelProviderView:
        """Make one connection the workspace default."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/{connection_id}/default",
            ),
            self._channel.context(),
        )
        return ModelProviderView.model_validate(response.body)

    async def read_probe(self, workspace_id: UUID, connection_id: UUID) -> ProbeView:
        """Read the latest probe state of one connection."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/{connection_id}/probe",
            ),
            self._channel.context(),
        )
        return ProbeView.model_validate(response.body)

    async def probe(self, workspace_id: UUID, connection_id: UUID) -> ProbeView:
        """Start one durable connection probe; the result is read back later."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/model-providers/{connection_id}/probe",
            ),
            self._channel.context(),
        )
        return ProbeView.model_validate(response.body)


class WorkspacesNotificationsClient:
    """Generated client for the ``workspaces.notifications`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
        unread_only: bool = False,
    ) -> NotificationPage:
        """List the current user's inbox in one workspace."""
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("include_archived", include_archived),
                ("unread_only", unread_only),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/notifications",
                query=query,
            ),
            self._channel.context(),
        )
        return NotificationPage.model_validate(response.body)

    async def read_preferences(self, workspace_id: UUID) -> NotificationPreferenceView:
        """Read the current user's notification preferences in one workspace."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/notifications/preferences",
            ),
            self._channel.context(),
        )
        return NotificationPreferenceView.model_validate(response.body)

    async def update_preferences(
        self,
        workspace_id: UUID,
        request: NotificationPreferenceUpdateRequest,
    ) -> NotificationPreferenceView:
        """Change the current user's notification preferences."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/notifications/preferences",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return NotificationPreferenceView.model_validate(response.body)

    async def mark_read(self, workspace_id: UUID, request: MarkReadRequest) -> MarkReadView:
        """Mark named notifications, or the whole inbox, read."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/notifications/read",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return MarkReadView.model_validate(response.body)

    async def archive(self, workspace_id: UUID, notification_id: UUID) -> NotificationView:
        """Archive one of the current user's own notifications."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/notifications/{notification_id}/archive",
            ),
            self._channel.context(),
        )
        return NotificationView.model_validate(response.body)


class WorkspacesProjectsInvitationsClient:
    """Generated client for the ``workspaces.projects.invitations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def received(self, *, limit: int = 50, offset: int = 0) -> ProjectInvitationPage:
        """List the project invitations addressed to the current user."""
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
                path=f"{self._channel.api_root}/workspaces/projects/invitations/received",
                query=query,
            ),
            self._channel.context(),
        )
        return ProjectInvitationPage.model_validate(response.body)

    async def accept(self, invitation_id: UUID) -> ProjectMemberView:
        """Accept one project invitation addressed to the current user and join."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/projects/invitations/{invitation_id}/accept",
            ),
            self._channel.context(),
        )
        return ProjectMemberView.model_validate(response.body)

    async def list(
        self,
        workspace_id: UUID,
        project_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> ProjectInvitationPage:
        """List the invitations of one project."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/members/invitations",
                query=query,
            ),
            self._channel.context(),
        )
        return ProjectInvitationPage.model_validate(response.body)

    async def create(
        self,
        workspace_id: UUID,
        project_id: UUID,
        request: InvitationCreateRequest,
    ) -> ProjectInvitationView:
        """Invite one workspace member to the project under a stated role."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/members/invitations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ProjectInvitationView.model_validate(response.body)

    async def revoke(
        self,
        workspace_id: UUID,
        project_id: UUID,
        invitation_id: UUID,
    ) -> ProjectInvitationView:
        """Revoke one pending project invitation; a decided one is never rewritten."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/members/invitations/{invitation_id}",
            ),
            self._channel.context(),
        )
        return ProjectInvitationView.model_validate(response.body)


class WorkspacesProjectsMembersClient:
    """Generated client for the ``workspaces.projects.members`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        workspace_id: UUID,
        project_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> ProjectMemberPage:
        """List the members of one project."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/members",
                query=query,
            ),
            self._channel.context(),
        )
        return ProjectMemberPage.model_validate(response.body)

    async def remove(self, workspace_id: UUID, project_id: UUID, user_id: UUID) -> None:
        """Remove one project member, or leave the project as that member."""
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/members/{user_id}",
            ),
            self._channel.context(),
        )

    async def update_role(
        self,
        workspace_id: UUID,
        project_id: UUID,
        user_id: UUID,
        request: MemberRoleUpdateRequest,
    ) -> ProjectMemberView:
        """Change one member's project role."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/members/{user_id}",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return ProjectMemberView.model_validate(response.body)


class WorkspacesProjectsClient:
    """Generated client for the ``workspaces.projects`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.invitations = WorkspacesProjectsInvitationsClient(channel)
        self.members = WorkspacesProjectsMembersClient(channel)

    async def list(self, workspace_id: UUID, *, limit: int = 50, offset: int = 0) -> ProjectPage:
        """List the projects of one workspace the current user may see."""
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
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects",
                query=query,
            ),
            self._channel.context(),
        )
        return ProjectPage.model_validate(response.body)

    async def create(self, workspace_id: UUID, request: ProjectCreateRequest) -> ProjectView:
        """Create one project; the creator becomes its administrator."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ProjectView.model_validate(response.body)

    async def delete(self, workspace_id: UUID, project_id: UUID) -> None:
        """Delete one project; its history stays and every access is refused."""
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}",
            ),
            self._channel.context(),
        )

    async def read(self, workspace_id: UUID, project_id: UUID) -> ProjectView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}",
            ),
            self._channel.context(),
        )
        return ProjectView.model_validate(response.body)

    async def update(
        self,
        workspace_id: UUID,
        project_id: UUID,
        request: ProjectUpdateRequest,
    ) -> ProjectView:
        """Change the project metadata; an archived project refuses writes."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ProjectView.model_validate(response.body)

    async def archive(self, workspace_id: UUID, project_id: UUID) -> ProjectView:
        """Archive one project; reads stay open, writes are refused."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/projects/{project_id}/archive",
            ),
            self._channel.context(),
        )
        return ProjectView.model_validate(response.body)


class WorkspacesClient:
    """The workspaces API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.capacity = WorkspacesCapacityClient(channel)
        self.credentials = WorkspacesCredentialsClient(channel)
        self.invitations = WorkspacesInvitationsClient(channel)
        self.members = WorkspacesMembersClient(channel)
        self.model_providers = WorkspacesModelProvidersClient(channel)
        self.notifications = WorkspacesNotificationsClient(channel)
        self.projects = WorkspacesProjectsClient(channel)

    async def list(self, *, limit: int = 50, offset: int = 0) -> WorkspacePage:
        """List the workspaces the current user is a member of."""
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
                path=f"{self._channel.api_root}/workspaces",
                query=query,
            ),
            self._channel.context(),
        )
        return WorkspacePage.model_validate(response.body)

    async def create(self, request: WorkspaceCreateRequest) -> WorkspaceView:
        """Create one workspace; the creator becomes its owner."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return WorkspaceView.model_validate(response.body)

    async def delete(self, workspace_id: UUID) -> None:
        """Delete one workspace; its history stays and every access is refused."""
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}",
            ),
            self._channel.context(),
        )

    async def read(self, workspace_id: UUID) -> WorkspaceView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}",
            ),
            self._channel.context(),
        )
        return WorkspaceView.model_validate(response.body)

    async def update(self, workspace_id: UUID, request: WorkspaceUpdateRequest) -> WorkspaceView:
        """Change the workspace metadata; an archived workspace refuses writes."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return WorkspaceView.model_validate(response.body)

    async def archive(self, workspace_id: UUID) -> WorkspaceView:
        """Archive one workspace; reads stay open, writes are refused."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/archive",
            ),
            self._channel.context(),
        )
        return WorkspaceView.model_validate(response.body)

    async def transfer_ownership(
        self,
        workspace_id: UUID,
        request: TransferOwnershipRequest,
    ) -> WorkspaceView:
        """Move the owner role to another member; the previous owner becomes admin."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/workspaces/{workspace_id}/transfer-ownership",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return WorkspaceView.model_validate(response.body)


def create_workspaces_client(channel: GroupChannel) -> WorkspacesClient:
    """Create the workspaces group client over one composed platform channel."""
    return WorkspacesClient(channel)


__all__ = [
    "CapacityKindView",
    "CapacityLimitUpdateRequest",
    "CapacitySnapshotView",
    "CredentialCreateRequest",
    "CredentialPage",
    "CredentialRotateRequest",
    "CredentialView",
    "DefaultModelView",
    "InvitationCreateRequest",
    "MarkReadRequest",
    "MarkReadView",
    "MemberRoleUpdateRequest",
    "ModelProviderCreateRequest",
    "ModelProviderPage",
    "ModelProviderUpdateRequest",
    "ModelProviderView",
    "NotificationPage",
    "NotificationPreferenceUpdateRequest",
    "NotificationPreferenceView",
    "NotificationView",
    "ProbeView",
    "ProjectCreateRequest",
    "ProjectInvitationPage",
    "ProjectInvitationView",
    "ProjectMemberPage",
    "ProjectMemberView",
    "ProjectPage",
    "ProjectUpdateRequest",
    "ProjectView",
    "SpaceInvitationPage",
    "SpaceInvitationView",
    "SpaceMemberPage",
    "SpaceMemberView",
    "TransferOwnershipRequest",
    "WorkspaceCreateRequest",
    "WorkspacePage",
    "WorkspaceUpdateRequest",
    "WorkspaceView",
    "WorkspacesCapacityClient",
    "WorkspacesClient",
    "WorkspacesCredentialsClient",
    "WorkspacesInvitationsClient",
    "WorkspacesMembersClient",
    "WorkspacesModelProvidersClient",
    "WorkspacesNotificationsClient",
    "WorkspacesProjectsClient",
    "WorkspacesProjectsInvitationsClient",
    "WorkspacesProjectsMembersClient",
    "create_workspaces_client",
]
