"""Remove retired gateway configuration from reusable execution definitions."""

from alembic import op

revision = "0120_retired_egress_configuration"
down_revision = "0119_runtime_host_process_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen Run authorization and audit evidence are deliberately immutable.
    # Reusable definitions must not reintroduce removed gateway configuration.
    definitions = (
        ("agent_profiles", "runtime_policy", "{network}"),
        ("agent_profiles", "runtime_policy", "{mcp,network_policy}"),
        ("agent_profile_versions", "snapshot", "{runtime_policy,network}"),
        ("agent_profile_versions", "snapshot", "{runtime_policy,mcp,network_policy}"),
        ("agent_teams", "default_task_policy", "{runtime,network}"),
        ("runtime_spaces", "policy", "{runtime,network}"),
        ("runtime_spaces", "network_policy", None),
        ("talent_listings", "metadata", "{agent_snapshot,runtime_policy,network}"),
        ("talent_listings", "metadata", "{agent_snapshot,runtime_policy,mcp,network_policy}"),
        ("marketplace_listings", "manifest", "{agent,runtime_policy,network}"),
        ("marketplace_listings", "manifest", "{agent,runtime_policy,mcp,network_policy}"),
    )
    for table, column, path in definitions:
        expression = f"{column} #> '{path}'" if path else column
        cleaned = f"({expression}) - 'proxy_url' - 'gateway_network' - 'disabled'"
        replacement = f"jsonb_set({column}, '{path}', {cleaned}, false)" if path else cleaned
        op.execute(
            f"UPDATE {table} SET {column}={replacement} "
            f"WHERE jsonb_typeof({expression})='object' "
            f"AND ({expression}) ?| ARRAY['proxy_url','gateway_network','disabled']"
        )


def downgrade() -> None:
    # Revision 0119 already removes these fields and cannot run a gateway.
    # Reintroducing the obsolete addresses would violate its strict contract.
    pass
