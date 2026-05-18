from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_profiles"
    __table_args__ = (
        Index("ix_agent_profiles_workspace_status", "workspace_id", "status"),
        Index("ix_agent_profiles_workspace_role", "workspace_id", "role"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(2_000), nullable=False, default="")
    instructions: Mapped[str] = mapped_column(String, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="gpt-4.1")
    model_provider_credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_provider_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_settings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    skills: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    tool_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    runtime_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    memory_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    approval_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    team_memberships: Mapped[list["AgentTeamMember"]] = relationship(
        back_populates="agent_profile",
        cascade="all, delete-orphan",
    )


from backend.app.teams.models import AgentTeamMember  # noqa: E402
