from dataclasses import dataclass

from backend.app.capabilities.mcp_policy import layered_int_policy, layered_optional_int_policy
from backend.app.capabilities.models import McpToolAllowlist


@dataclass(frozen=True)
class McpExecutionPolicy:
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int
    max_calls_per_run: int | None
    max_calls_per_hour: int | None


def resolve_mcp_execution_policy(
    snapshot: dict[str, object],
    allow: McpToolAllowlist,
) -> McpExecutionPolicy:
    runtime_policy = snapshot.get("runtime_policy")
    snapshot_mcp_policy = runtime_policy.get("mcp") if isinstance(runtime_policy, dict) else None
    allow_policy = allow.policy if isinstance(allow.policy, dict) else {}
    return McpExecutionPolicy(
        timeout_seconds=layered_int_policy(
            allow_policy,
            snapshot_mcp_policy,
            "timeout_seconds",
            30,
        ),
        max_input_bytes=layered_int_policy(
            allow_policy,
            snapshot_mcp_policy,
            "max_input_bytes",
            64_000,
        ),
        max_output_bytes=layered_int_policy(
            allow_policy,
            snapshot_mcp_policy,
            "max_output_bytes",
            256_000,
        ),
        max_calls_per_run=layered_optional_int_policy(
            allow_policy,
            snapshot_mcp_policy,
            "max_calls_per_run",
        ),
        max_calls_per_hour=layered_optional_int_policy(
            allow_policy,
            snapshot_mcp_policy,
            "max_calls_per_hour",
        ),
    )
