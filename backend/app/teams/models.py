from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentTeam(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_teams"
    __table_args__ = (
        Index("ix_agent_teams_workspace_status", "workspace_id", "status"),
        Index("ix_agent_teams_workspace_type", "workspace_id", "team_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    team_type: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    manager_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    coordination_rules: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    default_task_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    members: Mapped[list["AgentTeamMember"]] = relationship(
        back_populates="agent_team",
        cascade="all, delete-orphan",
        foreign_keys="AgentTeamMember.agent_team_id",
    )


class AgentTeamMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_team_members"
    __table_args__ = (
        UniqueConstraint(
            "agent_team_id",
            "agent_profile_id",
            name="uq_agent_team_members_team_agent",
        ),
        Index("ix_agent_team_members_workspace_team", "workspace_id", "agent_team_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_team_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_role: Mapped[str] = mapped_column(String(80), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    agent_team: Mapped[AgentTeam] = relationship(
        back_populates="members",
        foreign_keys=[agent_team_id],
    )
    agent_profile: Mapped["AgentProfile"] = relationship(back_populates="team_memberships")


from backend.app.agents.models import AgentProfile  # noqa: E402
