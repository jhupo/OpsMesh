"""Replace legacy implicit review settings with explicit approval rules."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0112_configured_approvals"
down_revision = "0111_managed_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve exact settings for rollback; do not infer a working review model or prompt.
    op.create_table(
        "approval_settings_migration_backup",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("settings", sa.JSON(), nullable=False),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT id, settings FROM workspaces")).mappings():
        settings = dict(row["settings"] or {})
        legacy = settings.pop("resource_review", None)
        if legacy is None:
            continue
        connection.execute(
            sa.text(
                "INSERT INTO approval_settings_migration_backup (workspace_id, settings) "
                "VALUES (:id, CAST(:settings AS json))"
            ),
            {"id": row["id"], "settings": json.dumps(row["settings"])},
        )
        if "approvals" not in settings:
            rules = []
            if isinstance(legacy, dict):
                for scope, visibility in [
                    ("private_resources", "private"),
                    ("public_resources", "public"),
                ]:
                    values = legacy.get(scope, {})
                    if isinstance(values, dict):
                        for name, enabled in values.items():
                            if enabled is True:
                                rules.append(
                                    {
                                        "id": f"migrated-{visibility}-{name}",
                                        "action": "resource",
                                        "name": name,
                                        "visibility": visibility,
                                        "decision": "review",
                                        "reviewer": "human",
                                    }
                                )
                model = legacy.get("model_request_review", {})
                if isinstance(model, dict) and model.get("semantic_mode") == "always":
                    rules.append(
                        {
                            "id": "migrated-model-request",
                            "action": "model_request",
                            "decision": "review",
                            "reviewer": "human",
                        }
                    )
            settings["approvals"] = {"default": "allow", "reviewer": "human", "rules": rules}
        connection.execute(
            sa.text("UPDATE workspaces SET settings=CAST(:settings AS jsonb) WHERE id=:id"),
            {"id": row["id"], "settings": json.dumps(settings)},
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE workspaces w SET settings=CAST(b.settings AS jsonb) "
            "FROM approval_settings_migration_backup b WHERE w.id=b.workspace_id"
        )
    )
    op.drop_table("approval_settings_migration_backup")
