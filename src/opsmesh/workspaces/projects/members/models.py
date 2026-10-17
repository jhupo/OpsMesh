"""Project membership, roles and invitation facts.

Project membership composes with space membership: a workspace owner or
admin administers every project, and everyone else holds exactly the
project role their membership row states. Both composite foreign keys
carry the workspace id, so a removed space member loses project seats and
pending project invitations with the space membership itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PROJECT_ROLE_ADMIN = "admin"
PROJECT_ROLE_MEMBER = "member"
PROJECT_ROLE_VIEWER = "viewer"
PROJECT_ROLES = (PROJECT_ROLE_ADMIN, PROJECT_ROLE_MEMBER, PROJECT_ROLE_VIEWER)

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


class ProjectMember(TimestampMixin, Base):
    """One user's membership in one project with its project role."""

    __tablename__ = "project_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name="project_member_role"
        ),
        Index("ix_project_members_workspace_user", "workspace_id", "user_id"),
    )

    project_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class ProjectInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One pending invitation to join a project under a stated role."""

    __tablename__ = "project_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"], ["projects.workspace_id", "projects.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "invitee_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name="project_invitation_role"
        ),
        CheckConstraint(
            "state IN ('pending', 'accepted', 'revoked', 'expired')",
            name="project_invitation_state",
        ),
        Index("ix_project_invitations_invitee", "invitee_id"),
        Index(
            "uq_project_pending_invitation",
            "project_id",
            "invitee_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    project_id: Mapped[UUID] = mapped_column(nullable=False)
    invitee_id: Mapped[UUID] = mapped_column(nullable=False)
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
