"""Canonical runtime/MCP contracts, retired memory configuration and cancellation intent.

Rollback snapshots are migration-owned data; production has no alternate format reader.
"""

import copy
import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0116_execution_contracts"
down_revision = "0115_queue_dispatches"
branch_labels = None
depends_on = None


def _egress(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    mode = source.get("mode") or source.get("egress_mode")
    aliases = {
        "disabled": "none",
        "off": "none",
        "deny": "none",
        "offline": "none",
        "allowlist": "restricted",
        "allow_list": "restricted",
        "gateway": "restricted",
        "online": "internet",
        "public": "internet",
        "bridge": "internet",
    }
    if isinstance(mode, str):
        mode = mode.strip().lower().replace("-", "_")
        mode = aliases.get(mode, mode)
    else:
        mode = "internet" if source.get("allow_network") is True else "none"
    if source.get("disabled") is True or source.get("allow_network") is False:
        mode = "none"
    if mode not in {"none", "restricted", "internet"}:
        raise ValueError("Migration encountered an invalid egress policy")
    allowed = {
        "allowed_domains",
        "allowed_cidrs",
        "allowed_ports",
        "allowed_protocols",
        "gateway_network",
        "proxy_url",
    }
    return {"mode": mode, **{key: item for key, item in source.items() if key in allowed}}


def connection_config(value: object) -> object:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    endpoint = result.pop("endpoint", None)
    if endpoint is not None:
        if result.get("url") is not None and result["url"] != endpoint:
            raise ValueError("Conflicting MCP URL fields require explicit repair")
        result["url"] = endpoint
    command = result.get("command")
    if isinstance(command, list):
        if not command or not all(isinstance(part, str) for part in command):
            raise ValueError("Invalid MCP argv cannot be migrated")
        if result.get("args"):
            raise ValueError("Conflicting MCP argv and args require explicit repair")
        result["command"], result["args"] = command[0], command[1:]
    return result


def _config(value: object) -> object:
    if isinstance(value, list):
        return [_config(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "memory_policy" and isinstance(item, dict):
            result[key] = {k: _config(v) for k, v in item.items() if k != "working_memory"}
        elif key in {"network", "network_policy", "default_network_policy"}:
            result[key] = _egress({"mode": item} if isinstance(item, str) else item)
        elif key == "network_mode":
            if "network_policy" in value:
                raise ValueError("Conflicting MCP network policies require explicit repair")
            result["network_policy"] = _egress({"mode": item})
        elif key == "connection":
            result[key] = connection_config(_config(item))
        else:
            result[key] = _config(item)
    if "fingerprint" in result and "runtime_binding" in result and "version" in result:
        payload = {key: item for key, item in result.items() if key != "fingerprint"}
        result["fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
        )
    return result


def _change(table: str, column: str, transform: object) -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    relation = sa.Table(table, metadata, autoload_with=connection)
    backup = sa.Table("execution_contract_rollback", metadata, autoload_with=connection)
    for record in connection.execute(sa.select(relation.c.id, relation.c[column])).mappings():
        old = record[column]
        new = transform(copy.deepcopy(old))
        if new == old:
            continue
        connection.execute(
            backup.insert().values(
                table_name=table,
                record_id=str(record["id"]),
                column_name=column,
                original=old,
            )
        )
        connection.execute(
            relation.update().where(relation.c.id == record["id"]).values({column: new})
        )


def upgrade() -> None:
    op.add_column(
        "self_hosted_mcp_jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True))
    )
    op.create_table(
        "execution_contract_rollback",
        sa.Column("table_name", sa.String(80), primary_key=True),
        sa.Column("record_id", sa.String(80), primary_key=True),
        sa.Column("column_name", sa.String(80), primary_key=True),
        sa.Column("original", JSONB(), nullable=True),
    )
    _change("runtime_templates", "default_network_policy", _egress)
    _change("workspace_runtimes", "network_policy", _egress)
    _change("runtime_spaces", "network_policy", _egress)
    _change(
        "agent_profiles",
        "memory_policy",
        lambda value: {key: item for key, item in (value or {}).items() if key != "working_memory"},
    )
    for table, column in (
        ("agent_profiles", "runtime_policy"),
        ("agent_profile_versions", "snapshot"),
        ("tasks", "team_snapshot"),
        ("agent_teams", "default_task_policy"),
        ("runtime_spaces", "policy"),
    ):
        _change(table, column, _config)

    def run_input(value: object) -> object:
        if not isinstance(value, dict) or "authorization_snapshot" not in value:
            return value
        return {**value, "authorization_snapshot": _config(value["authorization_snapshot"])}

    _change("agent_runs", "input", run_input)

    _change("mcp_servers", "connection", connection_config)

    def queue_payload(value: object) -> object:
        if not isinstance(value, dict):
            return value
        routing = value.get("routing")
        if not isinstance(routing, dict) or "runtime_id" not in routing:
            return value
        result = dict(value)
        canonical = dict(routing)
        runtime = canonical.pop("runtime_id")
        if canonical.get("workspace_runtime_id", runtime) != runtime:
            raise ValueError("Conflicting queue runtime identities require explicit repair")
        canonical["workspace_runtime_id"] = runtime
        result["routing"] = canonical
        return result

    _change("queue_dispatches", "payload", queue_payload)
    # Only unfinished RPC bookkeeping changes. Immutable completed audit records
    # and their historical hashes are retained exactly as recorded.
    connection = op.get_bind()
    logs = sa.Table("mcp_tool_call_logs", sa.MetaData(), autoload_with=connection)
    for record in connection.execute(
        sa.select(logs).where(logs.c.status == "waiting_self_hosted")
    ).mappings():
        response = record["response"]
        if isinstance(response, dict) and isinstance(response.get("result"), dict):
            connection.execute(
                sa.text("""
                INSERT INTO execution_contract_rollback(table_name, record_id, column_name, original)
                VALUES ('mcp_tool_call_logs', :id, 'response', CAST(:original AS jsonb))
            """),
                {"id": str(record["id"]), "original": json.dumps(response)},
            )
            connection.execute(
                logs.update().where(logs.c.id == record["id"]).values(response=response["result"])
            )


def downgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    backup = sa.Table("execution_contract_rollback", metadata, autoload_with=connection)
    for record in connection.execute(sa.select(backup)).mappings():
        relation = sa.Table(record["table_name"], metadata, autoload_with=connection)
        connection.execute(
            relation.update()
            .where(sa.cast(relation.c.id, sa.String) == record["record_id"])
            .values({record["column_name"]: record["original"]})
        )
    op.drop_table("execution_contract_rollback")
    op.drop_column("self_hosted_mcp_jobs", "cancel_requested_at")
