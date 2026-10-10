"""Align deployed column contracts with the current application models.

Revision ID: 0072_release_schema
Revises: 0071_platform_delivery
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0072_release_schema"
down_revision = "0071_platform_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not delete orphaned listings to force the constraint through: operators must resolve them.
    op.alter_column(
        "talent_listings", "source_agent_profile_id", existing_type=sa.Uuid(), nullable=False
    )
    op.alter_column(
        "workspace_agent_installs", "source_agent_profile_id", existing_type=sa.Uuid(), nullable=True
    )
    op.alter_column(
        "workspace_memory_versions", "snapshot", existing_type=sa.JSON(),
        type_=postgresql.JSONB(), postgresql_using="snapshot::jsonb",
    )
    for table in ("workspace_quotas", "workspace_reservations"):
        for column in ("created_at", "updated_at"):
            # Existing application timestamps are UTC; never use the database session's timezone.
            op.alter_column(
                table, column, existing_type=sa.DateTime(), type_=sa.DateTime(timezone=True),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    for table in ("workspace_quotas", "workspace_reservations"):
        for column in ("created_at", "updated_at"):
            op.alter_column(
                table, column, existing_type=sa.DateTime(timezone=True), type_=sa.DateTime(),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )
    op.alter_column(
        "workspace_memory_versions", "snapshot", existing_type=postgresql.JSONB(),
        type_=sa.JSON(), postgresql_using="snapshot::json",
    )
    # Fail rather than discard installed agents whose source was removed after upgrading.
    op.alter_column(
        "workspace_agent_installs", "source_agent_profile_id", existing_type=sa.Uuid(), nullable=False
    )
    op.alter_column(
        "talent_listings", "source_agent_profile_id", existing_type=sa.Uuid(), nullable=True
    )
