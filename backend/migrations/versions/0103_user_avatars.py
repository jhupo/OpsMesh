"""Store bounded account avatars separately from workspace files."""

import sqlalchemy as sa
from alembic import op

revision = "0103_user_avatars"
down_revision = "0102_platform_admin_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_version", sa.String(64), nullable=True))
    op.create_table(
        "user_avatars",
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 262144", name="avatar_content_size"),
    )


def downgrade() -> None:
    op.drop_table("user_avatars")
    op.drop_column("users", "avatar_version")
