"""Allow governed knowledge text sources without an external file or URL."""

from alembic import op

revision = "0121_text_knowledge_sources"
down_revision = "0120_retired_egress_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("knowledge_source_type_valid", "knowledge_sources", type_="check")
    op.create_check_constraint(
        "knowledge_source_type_valid",
        "knowledge_sources",
        "source_type in ('url', 'workspace_file', 'text')",
    )
    op.drop_constraint(
        "knowledge_source_revision_type_valid", "knowledge_source_revisions", type_="check"
    )
    op.create_check_constraint(
        "knowledge_source_revision_type_valid",
        "knowledge_source_revisions",
        "source_type in ('url', 'workspace_file', 'text')",
    )


def downgrade() -> None:
    # Preserve text sources: operators must export/remove them before downgrading.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_sources WHERE source_type = 'text')
               OR EXISTS (SELECT 1 FROM knowledge_source_revisions WHERE source_type = 'text') THEN
                RAISE EXCEPTION 'Export and remove text knowledge sources before downgrade';
            END IF;
        END $$;
    """)
    op.drop_constraint("knowledge_source_type_valid", "knowledge_sources", type_="check")
    op.create_check_constraint(
        "knowledge_source_type_valid",
        "knowledge_sources",
        "source_type in ('url', 'workspace_file')",
    )
    op.drop_constraint(
        "knowledge_source_revision_type_valid", "knowledge_source_revisions", type_="check"
    )
    op.create_check_constraint(
        "knowledge_source_revision_type_valid",
        "knowledge_source_revisions",
        "source_type in ('url', 'workspace_file')",
    )
