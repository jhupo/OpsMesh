"""Use one explicit tool allowlist for execution and capability governance."""

from alembic import op

revision = "0136_agent_tool_allowlist"
down_revision = "0135_native_plugin_deployments"
branch_labels = None
depends_on = None


def _migrate(*, downgrade: bool) -> None:
    source, target = ("allowed_tools", "mcp_tools") if downgrade else ("mcp_tools", "allowed_tools")
    # The previous execution policy preferred allowed_tools when both fields existed.
    # Expand the retired wildcard to the current workspace allowlist without granting
    # tools that are added later or belong to another workspace.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION pg_temp.opsmesh_tool_allowlist(policy jsonb, scope uuid)
        RETURNS jsonb LANGUAGE SQL AS $$
          SELECT CASE
            WHEN NOT policy ? '{source}' THEN policy
            ELSE (policy - '{source}') || jsonb_build_object('{target}',
              CASE
                WHEN policy ? '{target}' THEN policy -> '{target}'
                WHEN policy -> '{source}' = '\"*\"'::jsonb THEN (
                  SELECT COALESCE(jsonb_agg(DISTINCT allow.tool_name), '[]'::jsonb)
                  FROM mcp_tool_allowlist AS allow
                  JOIN mcp_servers AS server ON server.id = allow.mcp_server_id
                  WHERE allow.workspace_id = scope AND server.workspace_id = scope
                    AND allow.status = 'active' AND server.status = 'active'
                    AND NOT allow.platform_blocked AND NOT server.platform_blocked
                )
                WHEN jsonb_typeof(policy -> '{source}') = 'array'
                  THEN policy -> '{source}'
                ELSE '[]'::jsonb
              END)
          END
        $$
        """
    )
    op.execute(
        "UPDATE agent_profiles SET tool_policy = "
        "pg_temp.opsmesh_tool_allowlist(tool_policy, workspace_id)"
    )
    for table, column, scope, path, condition in (
        ("agent_profile_versions", "snapshot", "workspace_id", "{tool_policy}", "TRUE"),
        (
            "talent_listings",
            "metadata",
            "source_workspace_id",
            "{agent_snapshot,tool_policy}",
            "TRUE",
        ),
        (
            "marketplace_listings",
            "manifest",
            "workspace_id",
            "{agent,tool_policy}",
            "listing_type = 'agent'",
        ),
        (
            "marketplace_listings",
            "manifest",
            "workspace_id",
            "{tool_policy}",
            "listing_type = 'agent'",
        ),
        (
            "workspace_marketplace_installs",
            "installed_manifest",
            "workspace_id",
            "{agent,tool_policy}",
            "listing_type = 'agent'",
        ),
        (
            "workspace_marketplace_installs",
            "installed_manifest",
            "workspace_id",
            "{tool_policy}",
            "listing_type = 'agent'",
        ),
    ):
        op.execute(
            f"UPDATE {table} SET {column} = jsonb_set({column}, '{path}', "
            f"pg_temp.opsmesh_tool_allowlist({column} #> '{path}', {scope}), false) "
            f"WHERE {condition} AND jsonb_typeof({column} #> '{path}') = 'object'"
        )


def upgrade() -> None:
    _migrate(downgrade=False)


def downgrade() -> None:
    _migrate(downgrade=True)
