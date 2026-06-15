from uuid import UUID

from backend.app.core.typing import dict_list, string_list
from backend.app.tasks.models import TaskMessage, TaskStep


def handoff_chain(
    *,
    manager_steps: dict[str, object],
    specialist_steps: list[TaskStep],
    acceptance_messages: list[TaskMessage],
    follow_up_cycles: list[dict[str, object]],
) -> list[dict[str, object]]:
    planning_step = manager_steps["planning"]
    summaries = manager_steps["summaries"]
    return [
        {
            "phase": "manager_planning",
            "status": phase_status([planning_step] if isinstance(planning_step, TaskStep) else []),
            "step_ids": [step_id(planning_step)] if isinstance(planning_step, TaskStep) else [],
            "blocked_reasons": (
                [] if planning_step is not None else ["missing_manager_planning_step"]
            ),
        },
        {
            "phase": "specialist_execution",
            "status": phase_status(specialist_steps),
            "step_ids": [step_id(step) for step in specialist_steps],
            "blocked_reasons": (
                []
                if all(step.status == "completed" for step in specialist_steps)
                else ["specialist_steps_incomplete"]
            ),
        },
        {
            "phase": "manager_acceptance",
            "status": phase_status(summaries if isinstance(summaries, list) else []),
            "step_ids": [step_id(step) for step in summaries if isinstance(step, TaskStep)],
            "blocked_reasons": [] if summaries else ["missing_manager_summary_step"],
        },
        {
            "phase": "follow_up",
            "status": "completed"
            if follow_up_cycles
            and all(cycle["status"] == "completed" for cycle in follow_up_cycles)
            else "pending"
            if follow_up_cycles
            else "not_required",
            "step_ids": [
                step_id for cycle in follow_up_cycles for step_id in cycle["follow_up_step_ids"]
            ],
            "blocked_reasons": follow_up_chain_blockers(
                acceptance_messages,
                follow_up_cycles,
            ),
        },
    ]


def acceptance_payload(
    message: TaskMessage,
    follow_up_messages: list[TaskMessage],
) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    decision = decision_from_message(message)
    revision_requests = dict_list(payload.get("revision_requests"))
    missing_work_packages = dict_list(payload.get("missing_work_packages"))
    follow_up = matching_follow_up_message(message, follow_up_messages)
    return {
        "message_id": message.id,
        "sequence": message.sequence,
        "task_step_id": message.task_step_id,
        "agent_run_id": message.agent_run_id,
        "agent_profile_id": message.agent_profile_id,
        "decision": decision,
        "status": acceptance_status(decision, follow_up),
        "summary": message_summary(message, decision),
        "reasons": string_list(payload.get("reasons")),
        "revision_requests": revision_requests,
        "missing_work_packages": missing_work_packages,
        "follow_up_message_id": follow_up.id if follow_up is not None else None,
        "metadata": {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "decision",
                "reasons",
                "revision_requests",
                "missing_work_packages",
            }
        },
        "blocked_reasons": acceptance_blockers(
            decision,
            revision_requests,
            missing_work_packages,
            follow_up,
        ),
        "created_at": message.created_at,
    }


def follow_up_cycles(
    steps: list[TaskStep],
    follow_up_messages: list[TaskMessage],
) -> list[dict[str, object]]:
    step_by_id = {step.id: step for step in steps}
    cycles: list[dict[str, object]] = []
    for message in follow_up_messages:
        payload = message.payload if isinstance(message.payload, dict) else {}
        raw_step_ids = payload.get("follow_up_step_ids")
        step_ids = (
            [uuid_or_none(item) for item in raw_step_ids] if isinstance(raw_step_ids, list) else []
        )
        follow_up_steps = [step_by_id[step_id] for step_id in step_ids if step_id in step_by_id]
        review_steps = [
            step
            for step in steps
            if (step.work_package_id or "")
            == f"manager-summary-revision-{payload.get('revision_cycle')}"
        ]
        cycles.append(
            {
                "message_id": message.id,
                "revision_cycle": payload.get("revision_cycle"),
                "decision": payload.get("decision"),
                "follow_up_step_ids": [step_id(step) for step in follow_up_steps],
                "follow_up_work_package_ids": [step.work_package_id for step in follow_up_steps],
                "review_step_ids": [step_id(step) for step in review_steps],
                "status": cycle_status(follow_up_steps, review_steps),
                "blocked_reasons": cycle_blockers(follow_up_steps, review_steps),
                "metadata": {
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "decision",
                        "revision_cycle",
                        "follow_up_step_ids",
                        "follow_up_work_package_ids",
                    }
                },
                "created_at": message.created_at,
            }
        )
    return cycles


def blocked_reasons(
    *,
    manager_agent_id: UUID | None,
    manager_steps: dict[str, object],
    specialist_steps: list[TaskStep],
    acceptance_messages: list[TaskMessage],
    follow_up_cycles: list[dict[str, object]],
) -> list[str]:
    reasons: list[str] = []
    if manager_agent_id is None:
        reasons.append("missing_manager")
    if manager_steps["planning"] is None and manager_agent_id is not None:
        reasons.append("missing_manager_planning_step")
    if not manager_steps["summaries"] and manager_agent_id is not None:
        reasons.append("missing_manager_summary_step")
    if specialist_steps and any(step.status != "completed" for step in specialist_steps):
        reasons.append("specialist_steps_incomplete")
    if manager_steps["summaries"] and not acceptance_messages:
        reasons.append("acceptance_decision_missing")
    decisions_requiring_follow_up = [
        message
        for message in acceptance_messages
        if decision_from_message(message) in {"request_revision", "add_missing_work"}
    ]
    if decisions_requiring_follow_up and not follow_up_cycles:
        reasons.append("follow_up_missing")
    if any(cycle["status"] != "completed" for cycle in follow_up_cycles):
        reasons.append("follow_up_incomplete")
    return reasons


def acceptance_blockers(
    decision: str,
    revision_requests: list[dict[str, object]],
    missing_work_packages: list[dict[str, object]],
    follow_up: TaskMessage | None,
) -> list[str]:
    reasons: list[str] = []
    if decision == "request_revision" and not revision_requests:
        reasons.append("revision_requests_missing")
    if decision == "add_missing_work" and not missing_work_packages:
        reasons.append("missing_work_packages_missing")
    if decision in {"request_revision", "add_missing_work"} and follow_up is None:
        reasons.append("follow_up_missing")
    return reasons


def cycle_blockers(
    follow_up_steps: list[TaskStep],
    review_steps: list[TaskStep],
) -> list[str]:
    reasons: list[str] = []
    if not follow_up_steps:
        reasons.append("follow_up_steps_missing")
    elif any(step.status != "completed" for step in follow_up_steps):
        reasons.append("follow_up_steps_incomplete")
    if not review_steps:
        reasons.append("follow_up_review_missing")
    elif any(step.status != "completed" for step in review_steps):
        reasons.append("follow_up_review_incomplete")
    return reasons


def follow_up_chain_blockers(
    acceptance_messages: list[TaskMessage],
    follow_up_cycles: list[dict[str, object]],
) -> list[str]:
    needs_follow_up = any(
        decision_from_message(message) in {"request_revision", "add_missing_work"}
        for message in acceptance_messages
    )
    if needs_follow_up and not follow_up_cycles:
        return ["follow_up_missing"]
    if any(cycle["status"] != "completed" for cycle in follow_up_cycles):
        return ["follow_up_incomplete"]
    return []


def matching_follow_up_message(
    acceptance: TaskMessage,
    follow_up_messages: list[TaskMessage],
) -> TaskMessage | None:
    return next(
        (message for message in follow_up_messages if message.sequence > acceptance.sequence),
        None,
    )


def overall_status(blocked_reasons: list[str]) -> str:
    if not blocked_reasons:
        return "healthy"
    if any(
        reason.endswith("_missing") or reason == "missing_manager" for reason in blocked_reasons
    ):
        return "blocked"
    return "attention"


def phase_status(steps: list[TaskStep]) -> str:
    if not steps:
        return "missing"
    if all(step.status == "completed" for step in steps):
        return "completed"
    if any(step.status in {"running", "queued", "waiting_approval"} for step in steps):
        return "in_progress"
    if any(step.status == "failed" for step in steps):
        return "failed"
    return "attention"


def cycle_status(follow_up_steps: list[TaskStep], review_steps: list[TaskStep]) -> str:
    if not follow_up_steps or not review_steps:
        return "blocked"
    if all(step.status == "completed" for step in [*follow_up_steps, *review_steps]):
        return "completed"
    if any(step.status == "failed" for step in [*follow_up_steps, *review_steps]):
        return "failed"
    return "in_progress"


def acceptance_status(decision: str, follow_up: TaskMessage | None) -> str:
    if decision == "approved":
        return "approved"
    return "follow_up_created" if follow_up is not None else "needs_follow_up"


def decision_from_message(message: TaskMessage) -> str:
    payload = message.payload if isinstance(message.payload, dict) else {}
    decision = payload.get("decision")
    return decision if isinstance(decision, str) and decision else "recorded"


def message_summary(message: TaskMessage, fallback: str) -> str:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary") or payload.get("status")
    return summary if isinstance(summary, str) and summary else fallback


def uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def step_id(step: object) -> UUID | None:
    return step.id if isinstance(step, TaskStep) else None
