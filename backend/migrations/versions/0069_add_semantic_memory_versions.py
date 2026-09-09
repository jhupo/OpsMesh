"""add semantic memory versions

Revision ID: 0069_semantic_memory
Revises: 0068_episodic_memory
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0069_semantic_memory"
down_revision: str | None = "0068_episodic_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_SEMANTIC_MEMORY = {
    "retrieval_enabled": True,
    "write_enabled": True,
    "max_results": 8,
}


def upgrade() -> None:
    op.create_table(
        "workspace_memory_versions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("memory_entry_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("changed_by_agent_profile_id", sa.Uuid(), nullable=True),
        sa.Column("changed_by_agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("change_reason", sa.String(length=1_000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["memory_entry_id"],
            ["workspace_memory_entries.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_agent_profile_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_agent_run_id"],
            ["agent_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "memory_entry_id",
            "revision",
            name="uq_workspace_memory_versions_entry_revision",
        ),
    )
    op.create_index(
        "ix_workspace_memory_versions_workspace_entry",
        "workspace_memory_versions",
        ["workspace_id", "memory_entry_id", "revision"],
    )
    _backfill_semantic_versions()
    _rewrite_policy(add_semantic=True)


def downgrade() -> None:
    _rewrite_policy(add_semantic=False)
    _restore_semantic_entries()
    op.drop_index(
        "ix_workspace_memory_versions_workspace_entry",
        table_name="workspace_memory_versions",
    )
    op.drop_table("workspace_memory_versions")


def _rewrite_policy(*, add_semantic: bool) -> None:
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
        if add_semantic:
            policy["semantic_memory"] = dict(_DEFAULT_SEMANTIC_MEMORY)
        else:
            policy.pop("semantic_memory", None)
        bind.execute(
            profiles.update().where(profiles.c.id == profile_id).values(memory_policy=policy)
        )
    _rewrite_listing_snapshots(bind, add_semantic=add_semantic)


def _rewrite_listing_snapshots(bind: sa.Connection, *, add_semantic: bool) -> None:
    listings = sa.table(
        "talent_listings",
        sa.column("id", sa.Uuid()),
        sa.column("metadata", sa.JSON()),
    )
    for listing_id, raw_metadata in bind.execute(
        sa.select(listings.c.id, listings.c.metadata)
    ).all():
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        raw_snapshot = metadata.get("agent_snapshot")
        if not isinstance(raw_snapshot, dict):
            continue
        snapshot = dict(raw_snapshot)
        raw_policy = snapshot.get("memory_policy")
        policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
        if add_semantic:
            policy["semantic_memory"] = dict(_DEFAULT_SEMANTIC_MEMORY)
        else:
            policy.pop("semantic_memory", None)
        snapshot["memory_policy"] = policy
        metadata["agent_snapshot"] = snapshot
        bind.execute(
            listings.update().where(listings.c.id == listing_id).values(metadata=metadata)
        )


def _backfill_semantic_versions() -> None:
    entries = sa.table(
        "workspace_memory_entries",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("source_type", sa.String()),
        sa.column("source_id", sa.String()),
        sa.column("memory_layer", sa.String()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_id", sa.String()),
        sa.column("memory_key", sa.String()),
        sa.column("entry_type", sa.String()),
        sa.column("title", sa.String()),
        sa.column("content", sa.String()),
        sa.column("tags", sa.JSON()),
        sa.column("visibility_scope", sa.String()),
        sa.column("importance", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("content_fingerprint", sa.String()),
        sa.column("metadata", sa.JSON()),
        sa.column("archived_at", sa.DateTime(timezone=True)),
    )
    versions = sa.table(
        "workspace_memory_versions",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("memory_entry_id", sa.Uuid()),
        sa.column("revision", sa.Integer()),
        sa.column("snapshot", sa.JSON()),
        sa.column("content_fingerprint", sa.String()),
        sa.column("change_reason", sa.String()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(entries).where(
            entries.c.memory_layer == "semantic",
            entries.c.entry_type != "indexed_chunk",
        )
    ).mappings()
    for row in rows:
        revision = max(int(row["revision"] or 1), 1)
        knowledge_type = str(row["entry_type"] or "").removeprefix("semantic_")
        if knowledge_type not in {"fact", "configuration", "policy", "procedure"}:
            knowledge_type = "fact"
        memory_key = row["memory_key"] or f"memory:{row['id']}"
        bind.execute(
            entries.update()
            .where(entries.c.id == row["id"])
            .values(
                source_type="semantic_memory",
                source_id=str(row["id"]),
                memory_key=memory_key,
                entry_type=f"semantic_{knowledge_type}",
                revision=revision,
            )
        )
        archived_at = row["archived_at"]
        bind.execute(
            versions.insert().values(
                id=uuid4(),
                workspace_id=row["workspace_id"],
                memory_entry_id=row["id"],
                revision=revision,
                content_fingerprint=row["content_fingerprint"],
                change_reason="Initial semantic memory migration",
                snapshot={
                    "memory_layer": "semantic",
                    "scope_type": row["scope_type"],
                    "scope_id": row["scope_id"],
                    "memory_key": memory_key,
                    "knowledge_type": knowledge_type,
                    "title": row["title"],
                    "content": row["content"],
                    "tags": row["tags"] or [],
                    "importance": row["importance"],
                    "metadata": row["metadata"] or {},
                    "status": row["status"],
                    "archived_at": archived_at.isoformat() if archived_at else None,
                    "migration_original": {
                        "source_type": row["source_type"],
                        "source_id": row["source_id"],
                        "memory_key": row["memory_key"],
                        "entry_type": row["entry_type"],
                        "revision": row["revision"],
                    },
                },
            )
        )


def _restore_semantic_entries() -> None:
    entries = sa.table(
        "workspace_memory_entries",
        sa.column("id", sa.Uuid()),
        sa.column("source_type", sa.String()),
        sa.column("source_id", sa.String()),
        sa.column("memory_key", sa.String()),
        sa.column("entry_type", sa.String()),
        sa.column("revision", sa.Integer()),
    )
    versions = sa.table(
        "workspace_memory_versions",
        sa.column("memory_entry_id", sa.Uuid()),
        sa.column("snapshot", sa.JSON()),
        sa.column("change_reason", sa.String()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(versions.c.memory_entry_id, versions.c.snapshot).where(
            versions.c.change_reason == "Initial semantic memory migration"
        )
    ).all()
    for memory_entry_id, snapshot in rows:
        original = snapshot.get("migration_original") if isinstance(snapshot, dict) else None
        if not isinstance(original, dict):
            continue
        bind.execute(
            entries.update()
            .where(entries.c.id == memory_entry_id)
            .values(
                source_type=original.get("source_type"),
                source_id=original.get("source_id"),
                memory_key=original.get("memory_key"),
                entry_type=original.get("entry_type"),
                revision=original.get("revision") or 1,
            )
        )
