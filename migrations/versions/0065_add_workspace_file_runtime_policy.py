"""add explicit workspace file runtime policy

Revision ID: 0065_file_runtime_policy
Revises: 0064_run_project_file_io
Create Date: 2026-09-09 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_file_runtime_policy"
down_revision: str | None = "0064_run_project_file_io"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_files",
        sa.Column("sensitivity", sa.String(length=32), server_default="internal", nullable=False),
    )
    op.add_column(
        "workspace_files",
        sa.Column("runtime_access", sa.String(length=32), server_default="allowed", nullable=False),
    )
    op.create_check_constraint(
        "ck_workspace_files_sensitivity_valid",
        "workspace_files",
        "sensitivity in ('public', 'internal', 'confidential', 'restricted')",
    )
    op.create_check_constraint(
        "ck_workspace_files_runtime_access_valid",
        "workspace_files",
        "runtime_access in ('allowed', 'denied')",
    )
    op.create_check_constraint(
        "ck_workspace_files_restricted_runtime_denied",
        "workspace_files",
        "sensitivity != 'restricted' or runtime_access = 'denied'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_files_restricted_runtime_denied",
        "workspace_files",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_files_runtime_access_valid",
        "workspace_files",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_files_sensitivity_valid",
        "workspace_files",
        type_="check",
    )
    op.drop_column("workspace_files", "runtime_access")
    op.drop_column("workspace_files", "sensitivity")
