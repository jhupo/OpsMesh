"""enhance team members

Revision ID: 0019_enhance_team_members
Revises: 0018_capability_visibility
Create Date: 2026-05-19 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_enhance_team_members"
down_revision: str | None = "0018_capability_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_team_members",
        sa.Column("reports_to_member_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("agent_team_members", sa.Column("department", sa.String(length=120), nullable=True))
    op.add_column(
        "agent_team_members",
        sa.Column("position_title", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "agent_team_members",
        sa.Column("responsibilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_team_members",
        sa.Column("skill_weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_team_members",
        sa.Column("availability", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "agent_team_members",
        sa.Column("max_concurrent_tasks", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "agent_team_members",
        sa.Column("accepts_tasks", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "agent_team_members",
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
    )
    op.execute("UPDATE agent_team_members SET responsibilities = '[]'::jsonb WHERE responsibilities IS NULL")
    op.execute("UPDATE agent_team_members SET skill_weights = '{}'::jsonb WHERE skill_weights IS NULL")
    op.execute("UPDATE agent_team_members SET availability = '{}'::jsonb WHERE availability IS NULL")
    op.alter_column("agent_team_members", "responsibilities", nullable=False)
    op.alter_column("agent_team_members", "skill_weights", nullable=False)
    op.alter_column("agent_team_members", "availability", nullable=False)
    op.alter_column("agent_team_members", "max_concurrent_tasks", server_default=None)
    op.alter_column("agent_team_members", "accepts_tasks", server_default=None)
    op.alter_column("agent_team_members", "status", server_default=None)
    op.create_foreign_key(
        op.f("fk_agent_team_members_reports_to_member_id_agent_team_members"),
        "agent_team_members",
        "agent_team_members",
        ["reports_to_member_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_team_members_workspace_status",
        "agent_team_members",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_agent_team_members_workspace_department",
        "agent_team_members",
        ["workspace_id", "department"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_team_members_workspace_department", table_name="agent_team_members")
    op.drop_index("ix_agent_team_members_workspace_status", table_name="agent_team_members")
    op.drop_constraint(
        op.f("fk_agent_team_members_reports_to_member_id_agent_team_members"),
        "agent_team_members",
        type_="foreignkey",
    )
    op.drop_column("agent_team_members", "status")
    op.drop_column("agent_team_members", "accepts_tasks")
    op.drop_column("agent_team_members", "max_concurrent_tasks")
    op.drop_column("agent_team_members", "availability")
    op.drop_column("agent_team_members", "skill_weights")
    op.drop_column("agent_team_members", "responsibilities")
    op.drop_column("agent_team_members", "position_title")
    op.drop_column("agent_team_members", "department")
    op.drop_column("agent_team_members", "reports_to_member_id")
