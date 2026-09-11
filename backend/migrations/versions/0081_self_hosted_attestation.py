"""Persist self-hosted capability attestation state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0081_self_hosted_attestation"
down_revision = "0080_runtime_command_security_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "self_hosted_workers",
        sa.Column(
            "capability_attestation_state",
            sa.String(length=32),
            nullable=False,
            server_default="untrusted",
        ),
    )
    op.add_column(
        "self_hosted_workers",
        sa.Column("capability_attestation_fingerprint", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "self_hosted_workers",
        sa.Column(
            "capability_attestation_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "self_hosted_workers",
        sa.Column("capability_attested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "self_hosted_workers",
        sa.Column(
            "host_isolation_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "self_hosted_workers",
        "capability_attestation_state",
        server_default=None,
    )
    op.alter_column(
        "self_hosted_workers",
        "capability_attestation_metadata",
        server_default=None,
    )
    op.alter_column(
        "self_hosted_workers",
        "host_isolation_verified",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("self_hosted_workers", "host_isolation_verified")
    op.drop_column("self_hosted_workers", "capability_attested_at")
    op.drop_column("self_hosted_workers", "capability_attestation_metadata")
    op.drop_column("self_hosted_workers", "capability_attestation_fingerprint")
    op.drop_column("self_hosted_workers", "capability_attestation_state")
