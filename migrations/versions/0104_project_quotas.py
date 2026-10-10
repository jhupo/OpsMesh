"""Add workspace-project quota budgets and scope reservations to projects."""

import sqlalchemy as sa
from alembic import op

revision = "0104_project_quotas"
down_revision = "0103_user_avatars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_project_quotas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("quota_key", sa.String(80), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("reserved_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(32), nullable=False, server_default="count"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["workspace_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "quota_key", name="uq_workspace_project_quotas_key"
        ),
        sa.CheckConstraint("limit_value >= 0", name="project_limit_value_non_negative"),
        sa.CheckConstraint("reserved_value >= 0", name="project_reserved_value_non_negative"),
    )
    op.create_index(
        "ix_workspace_project_quotas_workspace_project_status",
        "workspace_project_quotas",
        ["workspace_id", "project_id", "status"],
    )
    op.add_column(
        "workspace_reservations",
        sa.Column("workspace_project_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workspace_reservations_project",
        "workspace_reservations",
        "workspace_projects",
        ["workspace_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workspace_reservations_workspace_project_status",
        "workspace_reservations",
        ["workspace_id", "workspace_project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_reservations_workspace_project_status",
        table_name="workspace_reservations",
    )
    op.drop_constraint(
        "fk_workspace_reservations_project", "workspace_reservations", type_="foreignkey"
    )
    op.drop_column("workspace_reservations", "workspace_project_id")
    op.drop_index(
        "ix_workspace_project_quotas_workspace_project_status",
        table_name="workspace_project_quotas",
    )
    op.drop_table("workspace_project_quotas")
