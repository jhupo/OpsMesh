from __future__ import annotations

from collections import Counter

from backend.app.runtime_manager.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.teams.project_space.policies import (
    ACTIVE_RESERVATION_STATUSES,
    SPACE_TIER_TEMPLATES,
)
from backend.app.teams.project_space.utils import list_of_dicts, number


def runtime_space_payload(
    space: RuntimeSpace,
    *,
    quotas: list[RuntimeSpaceQuota],
    reservations: list[RuntimeSpaceReservation],
    events: list[RuntimeSpaceEvent],
) -> dict[str, object]:
    active_reservations = [
        reservation
        for reservation in reservations
        if reservation.status in ACTIVE_RESERVATION_STATUSES
    ]
    return {
        "id": space.id,
        "name": space.name,
        "scope": space.scope,
        "status": space.status,
        "policy": space.policy,
        "network_policy": space.network_policy,
        "storage_policy": space.storage_policy,
        "cleanup_policy": space.cleanup_policy,
        "quota_limits": [quota_payload(quota) for quota in quotas],
        "reservation_summary": reservation_summary(active_reservations),
        "latest_events": [event_payload(event) for event in events[:10]],
    }


def quota_payload(quota: RuntimeSpaceQuota) -> dict[str, object]:
    available = max(quota.limit_value - quota.reserved_value, 0)
    utilization = round(quota.reserved_value / quota.limit_value, 4) if quota.limit_value else 0.0
    return {
        "quota_key": quota.quota_key,
        "limit_value": quota.limit_value,
        "reserved_value": quota.reserved_value,
        "available_value": available,
        "unit": quota.unit,
        "utilization": utilization,
    }


def reservation_summary(reservations: list[RuntimeSpaceReservation]) -> dict[str, object]:
    usage_totals: dict[str, int | float] = {}
    active_count = 0
    status_counts: Counter[str] = Counter()
    for reservation in reservations:
        status_counts[reservation.status] += 1
        if reservation.status not in ACTIVE_RESERVATION_STATUSES:
            continue
        active_count += 1
        for key, value in reservation.resource_usage.items():
            if isinstance(value, int | float):
                usage_totals[key] = usage_totals.get(key, 0) + value
    return {
        "total_reservation_count": len(reservations),
        "active_reservation_count": active_count,
        "status_counts": dict(sorted(status_counts.items())),
        "resource_usage": dict(sorted(usage_totals.items())),
    }


def event_payload(event: RuntimeSpaceEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "message": event.message,
        "metadata": event.event_metadata,
        "created_at": event.created_at,
    }


def capacity_summary(
    *,
    runtime_space_items: list[dict[str, object]],
    storage: dict[str, object],
    memory: dict[str, object],
    reservations: dict[str, object],
) -> dict[str, object]:
    aggregate_limits = aggregate_quota_limits(runtime_space_items)
    return {
        "runtime_space_count": len(runtime_space_items),
        "quota_limits": list(aggregate_limits.values()),
        "storage": storage,
        "memory": memory,
        "reservations": reservations,
    }


def aggregate_quota_limits(
    runtime_space_items: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    aggregate_limits: dict[str, dict[str, object]] = {}
    for space in runtime_space_items:
        for quota in list_of_dicts(space.get("quota_limits")):
            key = str(quota.get("quota_key"))
            current = aggregate_limits.setdefault(key, empty_quota_limit(key, quota))
            current["limit_value"] = number(current.get("limit_value")) + number(
                quota.get("limit_value")
            )
            current["reserved_value"] = number(current.get("reserved_value")) + number(
                quota.get("reserved_value")
            )
            current["available_value"] = number(current.get("available_value")) + number(
                quota.get("available_value")
            )
            current["space_count"] = number(current.get("space_count")) + 1
    for quota in aggregate_limits.values():
        limit = number(quota.get("limit_value"))
        reserved = number(quota.get("reserved_value"))
        quota["utilization"] = round(reserved / limit, 4) if limit else 0.0
    return aggregate_limits


def empty_quota_limit(key: str, quota: dict[str, object]) -> dict[str, object]:
    return {
        "quota_key": key,
        "limit_value": 0,
        "reserved_value": 0,
        "available_value": 0,
        "unit": quota.get("unit"),
        "space_count": 0,
    }


def max_project_space(runtime_space_items: list[dict[str, object]]) -> dict[str, object]:
    current_limits: dict[str, object] = {}
    for item in runtime_space_items:
        for quota in list_of_dicts(item.get("quota_limits")):
            key = str(quota.get("quota_key"))
            current_limits[key] = number(current_limits.get(key)) + number(
                quota.get("limit_value")
            )
    current_tier = infer_tier(current_limits)
    return {
        "current_tier": current_tier,
        "effective_limits": dict(sorted(current_limits.items())),
        "policy_source": "runtime_space_quotas",
        "available_tiers": [template["tier"] for template in SPACE_TIER_TEMPLATES],
        "upgrade_recommendation": upgrade_recommendation(current_tier),
    }


def infer_tier(current_limits: dict[str, object]) -> str | None:
    active_runs = number(current_limits.get("active_runs"))
    if active_runs <= 0:
        return None
    matched = None
    for template in SPACE_TIER_TEMPLATES:
        limits = template.get("quota_limits")
        if not isinstance(limits, dict):
            continue
        tier_active_runs = number(limits.get("active_runs"))
        if active_runs <= tier_active_runs:
            return str(template["tier"])
        matched = str(template["tier"])
    return matched


def upgrade_recommendation(current_tier: str | None) -> str | None:
    if current_tier is None:
        return "standard"
    tiers = [str(template["tier"]) for template in SPACE_TIER_TEMPLATES]
    try:
        index = tiers.index(current_tier)
    except ValueError:
        return "standard"
    if index + 1 >= len(tiers):
        return None
    return tiers[index + 1]
