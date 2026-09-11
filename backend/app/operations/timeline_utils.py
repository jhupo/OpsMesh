from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from backend.app.observability.audit_models import AuditEvent
from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters

TEAM_RUNTIME_CAPABILITY_KEY = "team_runtime"


def apply_time_filters(statement: Any, column: Any, filters: TimelineFilters) -> Any:
    if filters.since is not None:
        statement = statement.where(column >= filters.since)
    if filters.until is not None:
        statement = statement.where(column <= filters.until)
    return statement


def matches_filters(event: TimelineEvent, filters: TimelineFilters) -> bool:
    if filters.source_type is not None and event.source_type != filters.source_type:
        return False
    if filters.event_type is not None and event.event_type != filters.event_type:
        return False
    return within(event.occurred_at, filters)


def within(value: datetime, filters: TimelineFilters) -> bool:
    occurred_at = aware_datetime(value)
    if filters.since is not None and occurred_at < aware_datetime(filters.since):
        return False
    return not (filters.until is not None and occurred_at > aware_datetime(filters.until))


def counts(values: Iterable[str]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for value in values:
        totals[value] = totals.get(value, 0) + 1
    return totals


def team_runtime_team_id(capabilities: dict[str, object]) -> str | None:
    team_runtime = capabilities.get(TEAM_RUNTIME_CAPABILITY_KEY)
    if not isinstance(team_runtime, dict):
        return None
    team_id = team_runtime.get("team_id")
    return team_id if isinstance(team_id, str) else None


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def datetime_from_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return aware_datetime(value)
    if isinstance(value, str):
        try:
            return aware_datetime(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def queue_job_time(job: Any) -> datetime:
    return aware_datetime(job.last_failed_at or job.created_at)


def configured_mcp_tools(policy: object) -> set[str]:
    if not isinstance(policy, dict):
        return set()
    raw_tools = policy.get("mcp_tools")
    if not isinstance(raw_tools, list):
        return set()
    return {tool for tool in raw_tools if isinstance(tool, str) and tool}


def mcp_governance_message(event: AuditEvent) -> str:
    metadata = event.audit_metadata if isinstance(event.audit_metadata, dict) else {}
    name = metadata.get("name")
    target = name if isinstance(name, str) and name else event.target_id
    if event.action == "capability_governance.mcp_health_check_refreshed":
        status = metadata.get("health_status")
        status_text = status if isinstance(status, str) and status else "recorded"
        return f"MCP health check refreshed for {target}: {status_text}"
    if event.action == "capability_governance.mcp_server_disabled":
        return f"MCP server disabled by governance: {target}"
    return event.action
