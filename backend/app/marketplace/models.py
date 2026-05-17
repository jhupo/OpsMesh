from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TalentListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "talent_listings"
    __table_args__ = (
        UniqueConstraint(
            "source_agent_profile_id",
            "version",
            name="uq_talent_listings_agent_version",
        ),
        Index("ix_talent_listings_status_role", "status", "role"),
        Index("ix_talent_listings_owner_status", "owner_user_id", "status"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_agent_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    skill_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    capability_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    required_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    default_team_role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    listing_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="public")


class WorkspaceAgentInstall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_agent_installs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "talent_listing_id",
            name="uq_workspace_agent_installs_listing",
        ),
        Index("ix_workspace_agent_installs_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    talent_listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("talent_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_agent_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    hired_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    installed_agent_profile: Mapped["AgentProfile"] = relationship(
        foreign_keys=[installed_agent_profile_id],
    )


from backend.app.agents.models import AgentProfile  # noqa: E402
