"""add three layer memory foundation

Revision ID: 0067_memory_layers
Revises: 0066_agent_context_budget
Create Date: 2026-09-09 00:00:00.000000
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_memory_layers"
down_revision: str | None = "0066_agent_context_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_WORKING_MEMORY = {
    "enabled": True,
    "ttl_seconds": 86_400,
    "max_entries": 64,
    "max_entry_tokens": 2_048,
}


def upgrade() -> None:
    op.add_column(
        "workspace_memory_entries",
        sa.Column("memory_layer", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("scope_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("scope_id", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("memory_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_memory_entries",
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
    )

    entries = sa.table(
        "workspace_memory_entries",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("entry_type", sa.String()),
        sa.column("title", sa.String()),
        sa.column("content", sa.String()),
        sa.column("memory_layer", sa.String()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_id", sa.String()),
        sa.column("content_fingerprint", sa.String()),
    )
    bind = op.get_bind()
    for row in bind.execute(
        sa.select(
            entries.c.id,
            entries.c.workspace_id,
            entries.c.entry_type,
            entries.c.title,
            entries.c.content,
        )
    ).all():
        memory_layer = "episodic" if row.entry_type == "agent_run_summary" else "semantic"
        fingerprint = hashlib.sha256(
            f"{row.title}\x00{row.content}".encode()
        ).hexdigest()
        bind.execute(
            entries.update()
            .where(entries.c.id == row.id)
            .values(
                memory_layer=memory_layer,
                scope_type="workspace",
                scope_id=str(row.workspace_id),
                content_fingerprint=fingerprint,
            )
        )

    op.alter_column("workspace_memory_entries", "memory_layer", nullable=False)
    op.alter_column("workspace_memory_entries", "scope_type", nullable=False)
    op.alter_column("workspace_memory_entries", "scope_id", nullable=False)
    op.alter_column("workspace_memory_entries", "content_fingerprint", nullable=False)
    op.create_check_constraint(
        "ck_workspace_memory_layer",
        "workspace_memory_entries",
        "memory_layer IN ('working', 'episodic', 'semantic')",
    )
    op.create_check_constraint(
        "ck_workspace_memory_scope_type",
        "workspace_memory_entries",
        "scope_type IN ('run', 'session', 'task', 'agent', 'team', 'workspace')",
    )
    op.create_unique_constraint(
        "uq_workspace_memory_layer_scope_key",
        "workspace_memory_entries",
        ["workspace_id", "memory_layer", "scope_type", "scope_id", "memory_key"],
    )
    op.create_index(
        "ix_workspace_memory_entries_workspace_layer_scope",
        "workspace_memory_entries",
        ["workspace_id", "memory_layer", "scope_type", "scope_id", "status"],
    )
    op.alter_column("workspace_memory_entries", "revision", server_default=None)
    op.alter_column("workspace_memory_entries", "access_count", server_default=None)
    _rewrite_working_memory_policy(add_default=True)


def downgrade() -> None:
    _rewrite_working_memory_policy(add_default=False)
    op.drop_index(
        "ix_workspace_memory_entries_workspace_layer_scope",
        table_name="workspace_memory_entries",
    )
    op.drop_constraint(
        "uq_workspace_memory_layer_scope_key",
        "workspace_memory_entries",
        type_="unique",
    )
    op.drop_constraint(
        "ck_workspace_memory_scope_type",
        "workspace_memory_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_memory_layer",
        "workspace_memory_entries",
        type_="check",
    )
    for column in (
        "access_count",
        "archived_at",
        "expires_at",
        "content_fingerprint",
        "revision",
        "memory_key",
        "scope_id",
        "scope_type",
        "memory_layer",
    ):
        op.drop_column("workspace_memory_entries", column)


def _rewrite_working_memory_policy(*, add_default: bool) -> None:
    profiles = sa.table(
        "agent_profiles",
        sa.column("id", sa.Uuid()),
        sa.column("memory_policy", sa.JSON()),
    )
    bind = op.get_bind()
    for profile_id, raw_policy in bind.execute(
        sa.select(profiles.c.id, profiles.c.memory_policy)
    ).all():
        policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
        if add_default:
            working = policy.get("working_memory")
            policy["working_memory"] = {
                **_DEFAULT_WORKING_MEMORY,
                **(working if isinstance(working, dict) else {}),
            }
        else:
            policy.pop("working_memory", None)
        bind.execute(
            profiles.update().where(profiles.c.id == profile_id).values(memory_policy=policy)
        )
