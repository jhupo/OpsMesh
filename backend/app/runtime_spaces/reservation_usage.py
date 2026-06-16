from __future__ import annotations

from backend.app.runtime_spaces.models import RuntimeSpaceQuota, RuntimeSpaceReservation

RUN_CAPACITY_QUOTA_KEY = "active_runs"


def normalize_reservation_usage(resource_usage: dict[str, int] | None) -> dict[str, int]:
    usage = {
        key: value
        for key, value in (resource_usage or {}).items()
        if isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
    }
    return usage or {RUN_CAPACITY_QUOTA_KEY: 1}


def reservation_usage(reservation: RuntimeSpaceReservation) -> dict[str, int]:
    return normalize_reservation_usage(
        reservation.resource_usage if isinstance(reservation.resource_usage, dict) else {}
    )


def first_exceeded_quota(
    quotas: dict[str, RuntimeSpaceQuota],
    usage: dict[str, int],
) -> RuntimeSpaceQuota | None:
    for quota_key, amount in usage.items():
        quota = quotas.get(quota_key)
        if quota is None:
            continue
        if quota.reserved_value + amount > quota.limit_value:
            return quota
    return None


def active_reservation_matches(
    *,
    reservation: RuntimeSpaceReservation,
    task_id: object,
    task_step_id: object,
    resource_usage: dict[str, int],
) -> bool:
    return (
        reservation.task_id == task_id
        and reservation.task_step_id == task_step_id
        and reservation_usage(reservation) == resource_usage
    )
