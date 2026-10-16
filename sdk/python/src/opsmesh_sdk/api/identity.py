"""Generated identity API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``identity.auth.login``
- ``identity.auth.logout``
- ``identity.auth.change_password``
- ``identity.auth.register``
- ``identity.auth.sessions.list``
- ``identity.auth.sessions.revoke``
- ``identity.invitations.list``
- ``identity.invitations.create``
- ``identity.invitations.accept``
- ``identity.invitations.read``
- ``identity.invitations.resend``
- ``identity.invitations.revoke``
- ``identity.users.list``
- ``identity.users.read_current``
- ``identity.users.update_current``
- ``identity.users.avatar.delete``
- ``identity.users.avatar.read``
- ``identity.users.avatar.upload``
- ``identity.quotas.read_current``
- ``identity.users.read``
- ``identity.users.update``
- ``identity.quotas.read``
- ``identity.quotas.update``
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


class AcceptInvitationRequest(BaseModel):
    """The invitation token is the credential that opens the account."""

    display_name: str
    password: str
    token: UUID


class AcceptInvitationResponse(BaseModel):
    """The account and first session one accepted invitation produced."""

    expires_at: datetime
    session_id: UUID
    user_id: UUID


class AdminUpdateUserRequest(BaseModel):
    """The administrator-writable fields of one account."""

    display_name: str | None = None
    platform_roles: list[str] | None = None
    status: str | None = None
    work_root: str | None = None


class AuthenticatedUser(BaseModel):
    display_name: str
    email: str
    platform_roles: list[str]
    status: str
    user_id: UUID


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateInvitationRequest(BaseModel):
    email: str
    ttl_seconds: int | None = None


class InvitationView(BaseModel):
    """One invitation; the presented status derives expiry, never rewrites it."""

    accepted_at: datetime | None
    created_at: datetime
    email: str
    expires_at: datetime
    invitation_id: UUID
    invited_by_user_id: UUID | None
    last_sent_at: datetime | None
    send_count: int
    status: str


class LoginRequest(BaseModel):
    email: str
    password: str


class QuotaLimitsRequest(BaseModel):
    """The administrator-writable allowances of one account."""

    storage_limit_bytes: int | None = None
    token_limit: int | None = None


class QuotaView(BaseModel):
    """One account's limits and current ledger position."""

    available_bytes: int | None
    available_tokens: int | None
    reserved_bytes: int
    reserved_tokens: int
    storage_limit_bytes: int | None
    token_limit: int | None
    used_bytes: int
    used_tokens: int


class RegisterRequest(BaseModel):
    display_name: str
    email: str
    password: str


class SessionInfo(BaseModel):
    expires_at: datetime
    revoked_at: datetime | None
    session_id: UUID


class UpdateProfileRequest(BaseModel):
    """The fields an account may change on its own profile."""

    display_name: str | None = None
    work_root: str | None = None


class UserProfileView(BaseModel):
    """One account profile; avatar bytes are fetched from their own route."""

    avatar_content_type: str | None
    avatar_updated_at: datetime | None
    created_at: datetime
    display_name: str
    email: str
    has_avatar: bool
    platform_roles: list[str]
    status: str
    updated_at: datetime
    user_id: UUID
    work_root: str | None


class InvitationPage(BaseModel):
    invitations: list[InvitationView]
    total: int


class SessionGrant(BaseModel):
    """A session grant returned by registration and login."""

    expires_at: datetime
    session_id: UUID
    user: AuthenticatedUser


class UserPage(BaseModel):
    total: int
    users: list[UserProfileView]


class IdentityAuthSessionsClient:
    """Generated client for the ``identity.auth.sessions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self) -> list[SessionInfo]:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/auth/sessions"),
            self._channel.context(),
        )
        return [SessionInfo.model_validate(item) for item in decode_array(response)]

    async def revoke(self, session_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(method="DELETE", path=f"/api/v1/auth/sessions/{session_id}"),
            self._channel.context(),
        )


class IdentityAuthClient:
    """Generated client for the ``identity.auth`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.sessions = IdentityAuthSessionsClient(channel)

    async def login(self, request: LoginRequest) -> SessionGrant:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/auth/login",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return SessionGrant.model_validate(response.body)

    async def logout(self) -> None:
        """Revoke the presenting session; the identity channel stays available."""
        await self._channel.transport.send(
            TransportRequest(method="POST", path="/api/v1/auth/logout"),
            self._channel.context(),
        )

    async def change_password(self, request: ChangePasswordRequest) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/auth/password",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )

    async def register(self, request: RegisterRequest) -> SessionGrant:
        """Open an account and return its first session."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/auth/register",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return SessionGrant.model_validate(response.body)


class IdentityInvitationsClient:
    """Generated client for the ``identity.invitations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> InvitationPage:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("status", status),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/invitations", query=query),
            self._channel.context(),
        )
        return InvitationPage.model_validate(response.body)

    async def create(self, request: CreateInvitationRequest) -> InvitationView:
        """Create one pending invitation and register its mail in the same transaction."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/invitations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return InvitationView.model_validate(response.body)

    async def accept(self, request: AcceptInvitationRequest) -> AcceptInvitationResponse:
        """Accept one invitation with its token; no session exists before acceptance."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path="/api/v1/invitations/accept",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return AcceptInvitationResponse.model_validate(response.body)

    async def read(self, invitation_id: UUID) -> InvitationView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path=f"/api/v1/invitations/{invitation_id}"),
            self._channel.context(),
        )
        return InvitationView.model_validate(response.body)

    async def resend(self, invitation_id: UUID) -> InvitationView:
        """Resend with a freshly rotated token; the delivered code stops working."""
        response = await self._channel.transport.send(
            TransportRequest(method="POST", path=f"/api/v1/invitations/{invitation_id}/resend"),
            self._channel.context(),
        )
        return InvitationView.model_validate(response.body)

    async def revoke(self, invitation_id: UUID) -> InvitationView:
        response = await self._channel.transport.send(
            TransportRequest(method="POST", path=f"/api/v1/invitations/{invitation_id}/revoke"),
            self._channel.context(),
        )
        return InvitationView.model_validate(response.body)


class IdentityQuotasClient:
    """Generated client for the ``identity.quotas`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read_current(self) -> QuotaView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/users/me/quotas"),
            self._channel.context(),
        )
        return QuotaView.model_validate(response.body)

    async def read(self, user_id: UUID) -> QuotaView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path=f"/api/v1/users/{user_id}/quotas"),
            self._channel.context(),
        )
        return QuotaView.model_validate(response.body)

    async def update(self, user_id: UUID, request: QuotaLimitsRequest) -> QuotaView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"/api/v1/users/{user_id}/quotas",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return QuotaView.model_validate(response.body)


class IdentityUsersAvatarClient:
    """Generated client for the ``identity.users.avatar`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def delete(self) -> None:
        await self._channel.transport.send(
            TransportRequest(method="DELETE", path="/api/v1/users/me/avatar"),
            self._channel.context(),
        )

    async def read(self) -> bytes:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/users/me/avatar"),
            self._channel.context(),
        )
        return decode_binary(response)

    async def upload(self, content: bytes, *, content_type: str) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path="/api/v1/users/me/avatar",
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )


class IdentityUsersClient:
    """Generated client for the ``identity.users`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.avatar = IdentityUsersAvatarClient(channel)

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        email_query: str | None = None,
    ) -> UserPage:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("status", status),
                ("email_query", email_query),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/users", query=query),
            self._channel.context(),
        )
        return UserPage.model_validate(response.body)

    async def read_current(self) -> UserProfileView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path="/api/v1/users/me"),
            self._channel.context(),
        )
        return UserProfileView.model_validate(response.body)

    async def update_current(self, request: UpdateProfileRequest) -> UserProfileView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path="/api/v1/users/me",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return UserProfileView.model_validate(response.body)

    async def read(self, user_id: UUID) -> UserProfileView:
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path=f"/api/v1/users/{user_id}"),
            self._channel.context(),
        )
        return UserProfileView.model_validate(response.body)

    async def update(self, user_id: UUID, request: AdminUpdateUserRequest) -> UserProfileView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"/api/v1/users/{user_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return UserProfileView.model_validate(response.body)


class IdentityClient:
    """The identity API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.auth = IdentityAuthClient(channel)
        self.invitations = IdentityInvitationsClient(channel)
        self.quotas = IdentityQuotasClient(channel)
        self.users = IdentityUsersClient(channel)


def create_identity_client(channel: GroupChannel) -> IdentityClient:
    """Create the identity group client over one composed platform channel."""
    return IdentityClient(channel)


__all__ = [
    "AcceptInvitationRequest",
    "AcceptInvitationResponse",
    "AdminUpdateUserRequest",
    "AuthenticatedUser",
    "ChangePasswordRequest",
    "CreateInvitationRequest",
    "IdentityAuthClient",
    "IdentityAuthSessionsClient",
    "IdentityClient",
    "IdentityInvitationsClient",
    "IdentityQuotasClient",
    "IdentityUsersAvatarClient",
    "IdentityUsersClient",
    "InvitationPage",
    "InvitationView",
    "LoginRequest",
    "QuotaLimitsRequest",
    "QuotaView",
    "RegisterRequest",
    "SessionGrant",
    "SessionInfo",
    "UpdateProfileRequest",
    "UserPage",
    "UserProfileView",
    "create_identity_client",
]
