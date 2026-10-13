"""Remove the retired RuntimeSpace network enforcement contract."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0130_retired_runtime_network_policy"
down_revision = "0129_audit_event_timestamp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("runtime_spaces", "network_policy")


def downgrade() -> None:
    # The field is retired configuration. Downgrade restores its schema, with no
    # invented enforcement promise or reconstruction of discarded settings.
    op.add_column(
        "runtime_spaces",
        sa.Column("network_policy", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.alter_column("runtime_spaces", "network_policy", server_default=None)
