from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockedReasonExplanation:
    reason: str
    code: str
    message: str
    resource_key: str | None = None


BLOCKED_REASON_MESSAGES = {
    "workspace_quota_exceeded": "Workspace quota is exhausted.",
    "runtime_space_quota_exceeded": "Runtime space quota is exhausted.",
    "runtime_space_paused": "Runtime space is paused or unavailable.",
    "worker_unavailable": "No eligible worker is available.",
    "member_concurrency_exceeded": "Workspace member or task concurrency limit is reached.",
    "reservation_conflict": "An existing reservation conflicts with this request.",
    "workspace_scheduler_paused": "Workspace scheduler is paused.",
    "unknown": "Scheduling is blocked for an unknown reason.",
}


def explain_blocked_reason(reason: object) -> BlockedReasonExplanation:
    raw_reason = reason if isinstance(reason, str) and reason else "unknown"
    base_reason, resource_key = _split_resource_key(raw_reason)
    code = _normalize_blocked_reason_code(base_reason)
    return BlockedReasonExplanation(
        reason=raw_reason,
        code=code,
        message=BLOCKED_REASON_MESSAGES.get(code, BLOCKED_REASON_MESSAGES["unknown"]),
        resource_key=resource_key,
    )


def _split_resource_key(reason: str) -> tuple[str, str | None]:
    if ":" not in reason:
        return reason, None
    base_reason, resource_key = reason.split(":", 1)
    return base_reason, resource_key or None


def _normalize_blocked_reason_code(reason: str) -> str:
    if reason in {
        "workspace_quota_exceeded",
        "workspace_run_quota_exceeded",
        "workspace_resource_quota_exceeded",
    }:
        return "workspace_quota_exceeded"
    if reason == "runtime_space_quota_exceeded":
        return "runtime_space_quota_exceeded"
    if reason in {"runtime_space_paused", "runtime_space_unavailable"}:
        return "runtime_space_paused"
    if reason in {"workspace_task_quota_exceeded", "member_concurrency_exceeded"}:
        return "member_concurrency_exceeded"
    if reason in {"workspace_reservation_conflict", "runtime_space_reservation_conflict"}:
        return "reservation_conflict"
    if reason in {"worker_unavailable", "worker_capacity_full"}:
        return "worker_unavailable"
    if reason in {"workspace_scheduler_paused", "scheduler_paused"}:
        return "workspace_scheduler_paused"
    return "unknown"
