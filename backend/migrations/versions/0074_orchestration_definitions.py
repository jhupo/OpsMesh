"""Add workspace-owned user orchestration definitions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0074_orchestration_definitions"
down_revision = "0073_task_transfers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orchestration_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2_000), nullable=False, server_default=""),
        sa.Column(
            "definition",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_orchestration_definitions_workspace_key",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_orchestration_definitions_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_orchestration_definitions_version"),
    )
    op.create_index(
        "ix_orchestration_definitions_workspace_status",
        "orchestration_definitions",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_orchestration_definitions_workspace_updated",
        "orchestration_definitions",
        ["workspace_id", "updated_at"],
    )
    op.alter_column("orchestration_definitions", "description", server_default=None)
    op.alter_column("orchestration_definitions", "definition", server_default=None)
    op.alter_column("orchestration_definitions", "version", server_default=None)
    op.alter_column("orchestration_definitions", "status", server_default=None)

    op.add_column(
        "tasks",
        sa.Column(
            "orchestration_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("tasks", sa.Column("orchestration_version", sa.Integer(), nullable=True))
    op.create_index(
        "ix_tasks_workspace_orchestration",
        "tasks",
        ["workspace_id", "orchestration_definition_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_workspace_orchestration", table_name="tasks")
    op.drop_column("tasks", "orchestration_version")
    op.drop_column("tasks", "orchestration_definition_id")
    op.drop_index(
        "ix_orchestration_definitions_workspace_updated",
        table_name="orchestration_definitions",
    )
    op.drop_index(
        "ix_orchestration_definitions_workspace_status",
        table_name="orchestration_definitions",
    )
    op.drop_table("orchestration_definitions")
