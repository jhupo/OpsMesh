from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.typing import dict_or_empty, json_safe_payload
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskStep


def scheduled_run_blocking_summary(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    limit: int = 20,
) -> dict[str, object]:
    steps = session.scalars(
        select(TaskStep)
        .join(Task, Task.id == TaskStep.task_id)
        .where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
            TaskStep.workspace_id == workspace_id,
            TaskStep.status == "queued",
        )
        .order_by(Task.priority.desc(), TaskStep.updated_at.desc(), TaskStep.id.asc())
        .limit(limit)
    ).all()
    blocked_reasons: dict[str, int] = {}
    blocked_steps: list[dict[str, object]] = []
    for step in steps:
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        if dependencies.get("scheduling_status") != "blocked":
            continue
        reason = dependencies.get("blocked_reason")
        if not isinstance(reason, str) or not reason:
            reason = "scheduler_blocked"
        blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
        blocked_steps.append(
            json_safe_payload(
                {
                    "task_id": step.task_id,
                    "task_step_id": step.id,
                    "agent_profile_id": step.assigned_agent_profile_id,
                    "runtime_space_id": step.runtime_space_id,
                    "blocked_reason": reason,
                    "blocked_details": redact_sensitive_payload(
                        dict_or_empty(dependencies.get("blocked_details"))
                    ),
                }
            )
        )
    return {
        "blocked_reasons": dict(sorted(blocked_reasons.items())),
        "blocked_steps": blocked_steps,
    }
