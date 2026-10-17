"""Space membership, roles and invitation facts.

The membership row is the only place a space role exists: platform roles
never derive one, and an invitation is the only way in. One workspace has
exactly one owner, enforced by a partial unique index; ownership changes
only through the transfer the root service owns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

SPACE_ROLE_OWNER = "owner"
SPACE_ROLE_ADMIN = "admin"
SPACE_ROLE_MEMBER = "member"
SPACE_ROLE_VIEWER = "viewer"
SPACE_ROLES = (SPACE_ROLE_OWNER, SPACE_ROLE_ADMIN, SPACE_ROLE_MEMBER, SPACE_ROLE_VIEWER)
SPACE_INVITATION_ROLES = (SPACE_ROLE_ADMIN, SPACE_ROLE_MEMBER, SPACE_ROLE_VIEWER)

INVITATION_PENDING = "pending"
INVITATION_ACCEPTED = "accepted"
INVITATION_REVOKED = "revoked"
INVITATION_EXPIRED = "expired"
INVITATION_STATES = (
    INVITATION_PENDING,
    INVITATION_ACCEPTED,
    INVITATION_REVOKED,
    INVITATION_EXPIRED,
)

DEFAULT_INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60


class WorkspaceMember(TimestampMixin, Base):
    """One user's membership in one workspace with its space role."""

    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name="workspace_member_role"
        ),
        Index("ix_workspace_members_user", "user_id"),
        Index(
            "uq_workspace_owner",
            "workspace_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class WorkspaceInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One pending invitation to join a workspace under a stated role.

    Expiry is derived from ``expires_at`` and never rewritten into the
    stored state, so a row that aged out still reads as what it is.
    """

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name="workspace_invitation_role"
        ),
        CheckConstraint(
            "state IN ('pending', 'accepted', 'revoked', 'expired')",
            name="workspace_invitation_state",
        ),
        Index("ix_workspace_invitations_invitee", "invitee_id"),
        Index(
            "uq_workspace_pending_invitation",
            "workspace_id",
            "invitee_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    invitee_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default=INVITATION_PENDING, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    def effective_state(self, *, now: datetime | None = None) -> str:
        """The state this invitation presents; expiry is computed, not stored."""
        if self.state != INVITATION_PENDING:
            return self.state
        if self.expires_at <= (now or datetime.now(UTC)):
            return INVITATION_EXPIRED
        return INVITATION_PENDING
