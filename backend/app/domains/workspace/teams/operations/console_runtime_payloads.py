from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import UUID

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import (
    datetime_or_none,
    dedupe_strings,
    dict_list,
    dict_or_empty,
    int_or_zero,
    positive_int_or_default,
)
from backend.app.domains.workspace.teams.runtime.service import TeamRuntimeState
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.queue import RedisQueue


def runtime_payload(
    state: TeamRuntimeState,
    *,
    runtime_queue: dict[str, object],
    runtime_blocking: dict[str, object],
) -> dict[str, object]:
    return {
        "status": state.status,
        "workspace_runtime_id": state.workspace_runtime_id,
        "runtime_status": state.runtime_status,
        "runtime_space_id": state.runtime_space_id,
        "runtime_health": state.runtime_health,
        "thread_id": state.thread_id,
        "team_session_id": state.team_session_id,
        "team_session_key": state.team_session_key,
        "member_session_count": state.member_session_count,
        "member_agent_ids": state.member_agent_ids,
        "last_iteration": _redacted_dict_or_none(state.last_iteration),
        "last_message_at": state.last_message_at,
        "operating_policy": redact_sensitive_payload(dict(state.operating_policy)),
        "memory_summary": redact_sensitive_payload(dict(state.memory_summary)),
        "scheduling": redact_sensitive_payload(
            _runtime_scheduling_payload(state.metadata, state.generated_at)
        ),
        "blocked_steps": redact_sensitive_payload(runtime_blocked_steps_payload(runtime_blocking)),
        "queue": redact_sensitive_payload(runtime_queue),
        "ready": state.status == "running"
        and state.workspace_runtime_id is not None
        and state.runtime_status == "running"
        and state.runtime_health == "healthy",
        "metadata": redact_sensitive_payload(dict(state.metadata)),
    }


def team_loop_queue_payload(
    *,
    queue: RedisQueue | None,
    workspace_id: UUID,
    team_id: UUID,
    limit: int,
) -> dict[str, object]:
    if queue is None:
        return {
            "available": False,
            "queue_name": None,
            "queued": 0,
            "scheduled_retry": 0,
            "dead_letter": 0,
            "latest_jobs": [],
        }
    scan_limit = min(max(limit, 1), 200)
    queued = queue.list_queued(
        scan_limit,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team_id,
    )
    scheduled_retry = queue.list_scheduled_retries(
        scan_limit,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team_id,
    )
    dead_letter = queue.list_dead_letters(
        scan_limit,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team_id,
    )
    latest_jobs = [
        *_job_payloads("queued", queued),
        *_job_payloads("scheduled_retry", scheduled_retry),
        *_job_payloads("dead_letter", dead_letter),
    ]
    latest_jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "available": True,
        "queue_name": queue.queue_name,
        "job_type": JobType.TEAM_EXECUTION_LOOP.value,
        "queued": len(queued),
        "scheduled_retry": len(scheduled_retry),
        "dead_letter": len(dead_letter),
        "latest_jobs": latest_jobs[:10],
    }


def runtime_queue_suggested_actions(
    runtime_queue: dict[str, object],
) -> list[dict[str, object]]:
    if runtime_queue.get("available") is not True:
        return []
    actions: list[dict[str, object]] = []
    dead_letter = int_or_zero(runtime_queue.get("dead_letter"))
    scheduled_retry = int_or_zero(runtime_queue.get("scheduled_retry"))
    queued = int_or_zero(runtime_queue.get("queued"))
    if dead_letter > 0:
        actions.append(
            {
                "source": "team_runtime_queue",
                "automation": "team_runtime_control",
                "action": "inspect_dead_letter",
                "priority": 95,
                "reason": "team_execution_loop_dead_letter",
                "count": dead_letter,
            }
        )
    if scheduled_retry > 0:
        actions.append(
            {
                "source": "team_runtime_queue",
                "automation": "team_runtime_control",
                "action": "monitor_retry",
                "priority": 60,
                "reason": "team_execution_loop_retry_scheduled",
                "count": scheduled_retry,
            }
        )
    if queued > 1:
        actions.append(
            {
                "source": "team_runtime_queue",
                "automation": "team_runtime_control",
                "action": "inspect_queue",
                "priority": 40,
                "reason": "team_execution_loop_queue_backlog",
                "count": queued,
            }
        )
    return actions


def runtime_blocked_steps_payload(runtime_blocking: dict[str, object]) -> dict[str, object]:
    steps = dict_list(runtime_blocking.get("blocked_steps"))
    return {
        "count": len(steps),
        "blocked_reasons": dict_or_empty(runtime_blocking.get("blocked_reasons")),
        "latest": steps[:10],
        "truncated": len(steps) > 10,
    }


def runtime_blocked_step_suggested_actions(
    runtime_blocking: dict[str, object],
) -> list[dict[str, object]]:
    payload = runtime_blocked_steps_payload(runtime_blocking)
    count = int_or_zero(payload.get("count"))
    if count <= 0:
        return []
    steps = dict_list(payload.get("latest"))
    return [
        {
            "source": "team_runtime",
            "automation": "team_runtime_control",
            "action": "review_scheduling_blocks",
            "priority": 90,
            "reason": "team_runtime_step_scheduling_blocked",
            "count": count,
            "blocked_reasons": payload["blocked_reasons"],
            "task_ids": _unique_strings(item.get("task_id") for item in steps),
            "task_step_ids": _unique_strings(item.get("task_step_id") for item in steps),
        }
    ]


def _job_payloads(state: str, jobs: list[JobPayload]) -> list[dict[str, object]]:
    return [
        {
            "state": state,
            "job_id": str(job.job_id),
            "resource_id": str(job.resource_id),
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "priority": job.priority,
            "last_error": "[redacted]" if job.last_error is not None else None,
            "last_error_type": job.last_error_type,
            "last_failed_at": job.last_failed_at.isoformat() if job.last_failed_at else None,
            "created_at": job.created_at.isoformat(),
            "routing": redact_sensitive_payload(dict(job.routing)),
        }
        for job in jobs
    ]


def _runtime_scheduling_payload(
    metadata: dict[str, object],
    generated_at: datetime,
) -> dict[str, object]:
    scheduling_policy = dict_or_empty(metadata.get("scheduling_policy"))
    last_iteration = dict_or_empty(metadata.get("last_iteration"))
    anchor = datetime_or_none(last_iteration.get("recorded_at")) or datetime_or_none(
        metadata.get("last_heartbeat_at")
    )
    loop_interval_seconds = positive_int_or_default(
        scheduling_policy.get("loop_interval_seconds"),
        positive_int_or_default(metadata.get("loop_interval_seconds"), 300),
    )
    next_due_at = anchor + timedelta(seconds=loop_interval_seconds) if anchor else None
    return {
        "scheduled_loop_enabled": scheduling_policy.get("scheduled_loop_enabled", True)
        is not False,
        "loop_interval_seconds": loop_interval_seconds,
        "priority": positive_int_or_default(scheduling_policy.get("priority"), 5),
        "last_iteration_at": anchor,
        "next_due_at": next_due_at,
        "due": next_due_at is None or generated_at >= next_due_at,
        "last_scheduler_scan": dict_or_empty(metadata.get("last_scheduler_scan")),
    }


def _unique_strings(values: Iterable[object]) -> list[str]:
    return dedupe_strings(str(value) for value in values if value is not None)


def _redacted_dict_or_none(value: dict[str, object] | None) -> dict[str, object] | None:
    return redact_sensitive_payload(dict(value)) if isinstance(value, dict) else None
