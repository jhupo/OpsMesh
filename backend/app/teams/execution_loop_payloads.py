from __future__ import annotations

from backend.app.core.typing import dict_list, int_or_zero
from backend.app.tasks.models import Task, TaskMessage


def _result(
    task: Task,
    status: str,
    reason: str,
    *,
    final_output: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "previous_status": task.status,
        "status": status,
        "reason": reason,
        "final_output": final_output,
    }


def _iteration_summary(
    *,
    command_center_actions: dict[str, object] | None,
    finalization: dict[str, object] | None,
    apply_command_center_actions: bool,
    finalize_ready_tasks: bool,
) -> dict[str, object]:
    return {
        "apply_command_center_actions": apply_command_center_actions,
        "finalize_ready_tasks": finalize_ready_tasks,
        "eligible_action_count": _int_from(command_center_actions, "eligible_action_count"),
        "applied_action_count": _int_from(command_center_actions, "applied_action_count"),
        "scheduled_run_count": _int_from(command_center_actions, "scheduled_run_count"),
        "scheduled_run_skip_reason": _string_from(
            command_center_actions,
            "scheduled_run_skip_reason",
        ),
        "scanned_task_count": _int_from(finalization, "scanned_task_count"),
        "finalized_task_count": _int_from(finalization, "finalized_task_count"),
        "skipped_task_count": _int_from(finalization, "skipped_task_count"),
    }


def _advanced(summary: dict[str, object]) -> bool:
    return any(
        _int(summary.get(key)) > 0
        for key in ("applied_action_count", "scheduled_run_count", "finalized_task_count")
    )


def _int_from(payload: dict[str, object] | None, key: str) -> int:
    if payload is None:
        return 0
    return _int(payload.get(key))


def _string_from(payload: dict[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int:
    return int_or_zero(value)


def _without_finalizable_review_actions(
    command_center: dict[str, object],
    finalization: dict[str, object],
) -> dict[str, object]:
    finalizable_task_ids = _finalizable_task_ids(finalization)
    if not finalizable_task_ids:
        return command_center

    action_plan = dict_list(command_center.get("action_plan"))
    filtered_actions: list[dict[str, object]] = []
    suppressed = 0
    for item in action_plan:
        action = item.get("action")
        task_ids = _object_list(item.get("task_ids"))
        if action != "request_manager_review" or not task_ids:
            filtered_actions.append(item)
            continue

        kept_task_ids = [
            task_id for task_id in task_ids if str(task_id) not in finalizable_task_ids
        ]
        if not kept_task_ids and not _object_list(item.get("task_step_ids")):
            suppressed += 1
            continue

        adjusted = {**item, "task_ids": kept_task_ids}
        if isinstance(adjusted.get("count"), int):
            adjusted["count"] = min(int(adjusted["count"]), len(kept_task_ids))
        filtered_actions.append(adjusted)

    if suppressed == 0 and len(filtered_actions) == len(action_plan):
        return command_center

    summary = (
        dict(command_center["summary"])
        if isinstance(command_center.get("summary"), dict)
        else {}
    )
    summary["action_plan_count"] = len(filtered_actions)
    summary["action_plan_source_counts"] = _action_source_counts(filtered_actions)
    summary["suppressed_finalization_action_count"] = suppressed
    return {
        **command_center,
        "summary": summary,
        "action_plan": filtered_actions,
    }


def _finalizable_task_ids(finalization: dict[str, object]) -> set[str]:
    return {
        str(item["task_id"])
        for item in dict_list(finalization.get("results"))
        if item.get("status") in {"would_finalize", "finalized"} and item.get("task_id")
    }


def _action_source_counts(action_plan: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in action_plan:
        source = item.get("source")
        if isinstance(source, str) and source:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _final_output_from_acceptance(message: TaskMessage) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary")
    return {
        "summary": summary if isinstance(summary, str) and summary else "Approved",
        "source": "pm_acceptance_decision",
        "acceptance_message_id": str(message.id),
        "decision": "approved",
    }
