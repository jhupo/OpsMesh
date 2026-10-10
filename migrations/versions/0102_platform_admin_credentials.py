"""Add the installation-managed platform administrator identity."""

import sqlalchemy as sa
from alembic import op

revision = "0102_platform_admin_credentials"
down_revision = "0101_plugin_deployments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=80), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_index("ix_users_platform_admin", "users", ["platform_admin"], unique=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_index("ix_users_platform_admin", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "platform_admin")
    op.drop_column("users", "username")
