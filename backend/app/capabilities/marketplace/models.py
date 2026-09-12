from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
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
    install_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upgrade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="public")

    @property
    def average_rating(self) -> float:
        if self.review_count == 0:
            return 0.0
        return round(self.rating_sum / self.review_count, 2)


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
    current_talent_listing_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("talent_listings.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pinned_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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


class TalentListingReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "talent_listing_reviews"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "talent_listing_id",
            name="uq_talent_listing_reviews_workspace_listing",
        ),
        Index("ix_talent_listing_reviews_listing_status", "talent_listing_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    talent_listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("talent_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_agent_install_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_agent_installs.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    body: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class MarketplaceListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        Index(
            "ix_marketplace_listings_type_visibility_status",
            "listing_type",
            "visibility",
            "status",
        ),
        Index("ix_marketplace_listings_workspace_type", "workspace_id", "listing_type"),
        Index("ix_marketplace_listings_owner_status", "owner_user_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    listing_type: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0.0")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    listing_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    install_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkspaceMarketplaceInstall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_marketplace_installs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "marketplace_listing_id",
            name="uq_workspace_marketplace_installs_listing",
        ),
        Index(
            "ix_workspace_marketplace_installs_workspace_type",
            "workspace_id",
            "listing_type",
        ),
        Index(
            "ix_workspace_marketplace_installs_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    marketplace_listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    installed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    listing_type: Mapped[str] = mapped_column(String(32), nullable=False)
    installed_name: Mapped[str] = mapped_column(String(160), nullable=False)
    installed_version: Mapped[str] = mapped_column(String(64), nullable=False)
    installed_manifest: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    listing: Mapped[MarketplaceListing] = relationship()


from backend.app.agents.models import AgentProfile  # noqa: E402
