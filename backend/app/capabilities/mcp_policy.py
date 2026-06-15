from datetime import UTC, datetime, timedelta

from backend.app.capabilities.models import McpServer

MCP_LIMIT_COUNTED_STATUSES = (
    "completed",
    "failed",
    "waiting_approval",
    "waiting_self_hosted",
)


def mcp_health_check_stale(server: McpServer, *, stale_after: timedelta) -> bool:
    checked_at = server.last_health_check_at
    if checked_at is None:
        return False
    normalized = checked_at if checked_at.tzinfo is not None else checked_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - normalized > stale_after


def positive_int_policy(policy: dict[str, object], key: str, default: int) -> int:
    value = policy.get(key)
    return value if isinstance(value, int) and value > 0 else default


def optional_positive_int_policy(policy: dict[str, object], key: str) -> int | None:
    value = policy.get(key)
    return value if isinstance(value, int) and value > 0 else None


def mcp_tool_policy_summary(
    policy: dict[str, object],
    *,
    hourly_count: int,
) -> dict[str, object]:
    max_calls_per_hour = optional_positive_int_policy(policy, "max_calls_per_hour")
    return {
        "timeout_seconds": positive_int_policy(policy, "timeout_seconds", 30),
        "max_input_bytes": positive_int_policy(policy, "max_input_bytes", 64_000),
        "max_output_bytes": positive_int_policy(policy, "max_output_bytes", 256_000),
        "max_calls_per_run": optional_positive_int_policy(policy, "max_calls_per_run"),
        "max_calls_per_hour": max_calls_per_hour,
        "current_hour_call_count": hourly_count,
        "hourly_limit_remaining": (
            max(max_calls_per_hour - hourly_count, 0) if max_calls_per_hour is not None else None
        ),
        "limit_window_seconds": 3600,
    }


def layered_int_policy(
    allow_policy: dict[str, object],
    snapshot_policy: object,
    key: str,
    default: int,
) -> int:
    value = allow_policy.get(key)
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(snapshot_policy, dict):
        value = snapshot_policy.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return default


def layered_optional_int_policy(
    allow_policy: dict[str, object],
    snapshot_policy: object,
    key: str,
) -> int | None:
    value = allow_policy.get(key)
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(snapshot_policy, dict):
        value = snapshot_policy.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None
