from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.tasks.models import Task, TaskMessage, TaskStep


class TaskManagerDiagnosticsService:
    """Explain PM planning, handoff, acceptance, and follow-up work for a task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(self, *, workspace_id: UUID, task_id: UUID) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = self._steps(workspace_id, task_id)
        messages = self._messages(workspace_id, task_id)
        manager_agent_id = _manager_agent_id(task)
        agents = self._agents(workspace_id, steps, messages, manager_agent_id)
        manager_agent = agents.get(manager_agent_id) if manager_agent_id is not None else None
        manager_steps = _manager_steps(steps)
        specialist_steps = [step for step in steps if step.id not in manager_steps["ids"]]
        acceptance_messages = [
            message for message in messages if message.message_type == "pm.acceptance_decision"
        ]
        follow_up_messages = [
            message for message in messages if message.message_type == "pm.follow_up_created"
        ]
        follow_up_cycles = _follow_up_cycles(steps, follow_up_messages)
        blocked_reasons = _blocked_reasons(
            manager_agent_id=manager_agent_id,
            manager_steps=manager_steps,
            specialist_steps=specialist_steps,
            acceptance_messages=acceptance_messages,
            follow_up_cycles=follow_up_cycles,
        )

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "manager": {
                "agent_profile_id": manager_agent_id,
                "agent": _agent_payload(manager_agent),
                "has_manager": manager_agent_id is not None,
                "planning_step_id": _step_id(manager_steps["planning"]),
                "summary_step_ids": [_step_id(step) for step in manager_steps["summaries"]],
                "revision_review_step_ids": [
                    _step_id(step) for step in manager_steps["revision_reviews"]
                ],
            },
            "summary": {
                "status": _overall_status(blocked_reasons),
                "total_steps": len(steps),
                "specialist_steps": len(specialist_steps),
                "manager_steps": len(manager_steps["all"]),
                "acceptance_decisions": len(acceptance_messages),
                "follow_up_cycles": len(follow_up_cycles),
                "step_status_counts": dict(sorted(Counter(step.status for step in steps).items())),
                "decision_counts": dict(
                    sorted(
                        Counter(
                            _decision_from_message(message)
                            for message in acceptance_messages
                        ).items()
                    )
                ),
            },
            "handoff_chain": _handoff_chain(
                manager_steps=manager_steps,
                specialist_steps=specialist_steps,
                acceptance_messages=acceptance_messages,
                follow_up_cycles=follow_up_cycles,
            ),
            "acceptance_decisions": [
                _acceptance_payload(message, follow_up_messages)
                for message in acceptance_messages
            ],
            "follow_up_cycles": follow_up_cycles,
            "blocked_reasons": blocked_reasons,
        }

    def list_manager_queue(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
        status: str | None = None,
        include_healthy: bool = False,
    ) -> dict[str, object]:
        statement = select(Task).where(Task.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Task.status == status)
        tasks = list(
            self._session.scalars(
                statement.order_by(Task.updated_at.desc(), Task.created_at.desc())
            )
        )

        items: list[dict[str, object]] = []
        for task in tasks:
            diagnostics = self.get_diagnostics(workspace_id=workspace_id, task_id=task.id)
            if diagnostics is None:
                continue
            item = _manager_queue_item(task, diagnostics)
            if not include_healthy and not item["needs_attention"]:
                continue
            items.append(item)

        total = len(items)
        paged_items = items[offset : offset + limit]
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": _manager_queue_summary(items),
            "items": paged_items,
        }

    def _steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _messages(self, workspace_id: UUID, task_id: UUID) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
                .order_by(TaskMessage.sequence.asc())
            )
        )

    def _agents(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
        messages: list[TaskMessage],
        manager_agent_id: UUID | None,
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            agent_id
            for agent_id in (
                [manager_agent_id]
                + [step.assigned_agent_profile_id for step in steps]
                + [message.agent_profile_id for message in messages]
            )
            if agent_id is not None
        }
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}


def _manager_queue_item(task: Task, diagnostics: dict[str, object]) -> dict[str, object]:
    manager = diagnostics.get("manager") if isinstance(diagnostics.get("manager"), dict) else {}
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    blocked_reasons = _string_list(diagnostics.get("blocked_reasons"))
    manager_agent = manager.get("agent") if isinstance(manager.get("agent"), dict) else None
    manager_status = _manager_status(manager)
    summary_status = str(summary.get("status") or "unknown")
    needs_attention = summary_status != "healthy" or bool(blocked_reasons)
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "domain_type": task.domain_type,
        "manager_agent_profile_id": manager.get("agent_profile_id"),
        "manager_agent_name": manager_agent.get("name") if manager_agent is not None else None,
        "manager_status": manager_status,
        "summary_status": summary_status,
        "pending_phase": _pending_manager_phase(diagnostics),
        "needs_attention": needs_attention,
        "blocked_reasons": blocked_reasons,
        "recommended_actions": _recommended_manager_actions(blocked_reasons),
        "acceptance_decisions": int(summary.get("acceptance_decisions") or 0),
        "follow_up_cycles": int(summary.get("follow_up_cycles") or 0),
        "step_status_counts": _int_dict(summary.get("step_status_counts")),
        "last_activity_at": task.updated_at,
    }


def _manager_queue_summary(items: list[dict[str, object]]) -> dict[str, object]:
    pending_phases = Counter(str(item["pending_phase"]) for item in items)
    manager_statuses = Counter(str(item["manager_status"]) for item in items)
    recommended_actions = Counter(
        action
        for item in items
        for action in item["recommended_actions"]
        if isinstance(action, str)
    )
    return {
        "needs_attention": len(items),
        "pending_phases": dict(sorted(pending_phases.items())),
        "manager_statuses": dict(sorted(manager_statuses.items())),
        "recommended_actions": dict(sorted(recommended_actions.items())),
    }


def _manager_status(manager: dict[str, object]) -> str:
    if not manager.get("has_manager"):
        return "missing"
    agent = manager.get("agent")
    if not isinstance(agent, dict):
        return "unknown"
    status = agent.get("status")
    return status if isinstance(status, str) else "unknown"


def _pending_manager_phase(diagnostics: dict[str, object]) -> str:
    blocked_reasons = set(_string_list(diagnostics.get("blocked_reasons")))
    if any(reason.startswith("missing_manager") for reason in blocked_reasons):
        return "manager_setup"
    if "specialist_steps_incomplete" in blocked_reasons:
        return "specialist_execution"
    if "acceptance_decision_missing" in blocked_reasons:
        return "manager_acceptance"
    if "follow_up_missing" in blocked_reasons or "follow_up_incomplete" in blocked_reasons:
        return "follow_up"

    handoff_chain = diagnostics.get("handoff_chain")
    if isinstance(handoff_chain, list):
        for phase in handoff_chain:
            if not isinstance(phase, dict):
                continue
            if phase.get("status") not in {"completed", "not_required"}:
                value = phase.get("phase")
                return value if isinstance(value, str) else "unknown"
    return "none"


def _recommended_manager_actions(blocked_reasons: list[str]) -> list[str]:
    actions: list[str] = []
    if "missing_manager" in blocked_reasons:
        actions.append("assign_manager")
    if any(reason.startswith("missing_manager_") for reason in blocked_reasons):
        actions.append("request_manager_review")
    if "acceptance_decision_missing" in blocked_reasons:
        actions.append("request_manager_review")
    if "follow_up_missing" in blocked_reasons:
        actions.append("request_manager_review")
    if "follow_up_incomplete" in blocked_reasons:
        actions.append("monitor_follow_up")
    if "specialist_steps_incomplete" in blocked_reasons:
        actions.append("monitor_specialists")
    return list(dict.fromkeys(actions))


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(item) for key, item in value.items() if isinstance(item, int)}


def _manager_agent_id(task: Task) -> UUID | None:
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else {}
    team = snapshot.get("team") if isinstance(snapshot.get("team"), dict) else {}
    manager_id = _uuid_or_none(team.get("manager_agent_profile_id"))
    if manager_id is not None:
        return manager_id
    plan = task.project_plan if isinstance(task.project_plan, dict) else {}
    return _uuid_or_none(plan.get("planner_agent_profile_id"))


def _manager_steps(steps: list[TaskStep]) -> dict[str, object]:
    planning = next((step for step in steps if step.work_package_id == "manager-planning"), None)
    summaries = [
        step
        for step in steps
        if step.work_package_id == "manager-summary" or _review_mode(step) == "final_acceptance"
    ]
    revision_reviews = [
        step
        for step in steps
        if (step.work_package_id or "").startswith("manager-summary-revision-")
    ]
    manager_like = [
        step
        for step in steps
        if step == planning or step in summaries or step in revision_reviews
    ]
    return {
        "planning": planning,
        "summaries": summaries,
        "revision_reviews": revision_reviews,
        "all": manager_like,
        "ids": {step.id for step in manager_like},
    }


def _handoff_chain(
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
            "status": _phase_status([planning_step] if isinstance(planning_step, TaskStep) else []),
            "step_ids": [_step_id(planning_step)] if isinstance(planning_step, TaskStep) else [],
            "blocked_reasons": (
                [] if planning_step is not None else ["missing_manager_planning_step"]
            ),
        },
        {
            "phase": "specialist_execution",
            "status": _phase_status(specialist_steps),
            "step_ids": [_step_id(step) for step in specialist_steps],
            "blocked_reasons": (
                []
                if all(step.status == "completed" for step in specialist_steps)
                else ["specialist_steps_incomplete"]
            ),
        },
        {
            "phase": "manager_acceptance",
            "status": _phase_status(summaries if isinstance(summaries, list) else []),
            "step_ids": [_step_id(step) for step in summaries if isinstance(step, TaskStep)],
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
                step_id
                for cycle in follow_up_cycles
                for step_id in cycle["follow_up_step_ids"]
            ],
            "blocked_reasons": _follow_up_chain_blockers(
                acceptance_messages,
                follow_up_cycles,
            ),
        },
    ]


def _acceptance_payload(
    message: TaskMessage,
    follow_up_messages: list[TaskMessage],
) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    decision = _decision_from_message(message)
    revision_requests = _dict_list(payload.get("revision_requests"))
    missing_work_packages = _dict_list(payload.get("missing_work_packages"))
    follow_up = _matching_follow_up_message(message, follow_up_messages)
    return {
        "message_id": message.id,
        "sequence": message.sequence,
        "task_step_id": message.task_step_id,
        "agent_run_id": message.agent_run_id,
        "agent_profile_id": message.agent_profile_id,
        "decision": decision,
        "status": _acceptance_status(decision, follow_up),
        "summary": _message_summary(message, decision),
        "reasons": _string_list(payload.get("reasons")),
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
        "blocked_reasons": _acceptance_blockers(
            decision,
            revision_requests,
            missing_work_packages,
            follow_up,
        ),
        "created_at": message.created_at,
    }


def _follow_up_cycles(
    steps: list[TaskStep],
    follow_up_messages: list[TaskMessage],
) -> list[dict[str, object]]:
    step_by_id = {step.id: step for step in steps}
    cycles: list[dict[str, object]] = []
    for message in follow_up_messages:
        payload = message.payload if isinstance(message.payload, dict) else {}
        raw_step_ids = payload.get("follow_up_step_ids")
        step_ids = (
            [_uuid_or_none(item) for item in raw_step_ids]
            if isinstance(raw_step_ids, list)
            else []
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
                "follow_up_step_ids": [_step_id(step) for step in follow_up_steps],
                "follow_up_work_package_ids": [
                    step.work_package_id for step in follow_up_steps
                ],
                "review_step_ids": [_step_id(step) for step in review_steps],
                "status": _cycle_status(follow_up_steps, review_steps),
                "blocked_reasons": _cycle_blockers(follow_up_steps, review_steps),
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


def _blocked_reasons(
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
        if _decision_from_message(message) in {"request_revision", "add_missing_work"}
    ]
    if decisions_requiring_follow_up and not follow_up_cycles:
        reasons.append("follow_up_missing")
    if any(cycle["status"] != "completed" for cycle in follow_up_cycles):
        reasons.append("follow_up_incomplete")
    return reasons


def _acceptance_blockers(
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


def _cycle_blockers(
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


def _follow_up_chain_blockers(
    acceptance_messages: list[TaskMessage],
    follow_up_cycles: list[dict[str, object]],
) -> list[str]:
    needs_follow_up = any(
        _decision_from_message(message) in {"request_revision", "add_missing_work"}
        for message in acceptance_messages
    )
    if needs_follow_up and not follow_up_cycles:
        return ["follow_up_missing"]
    if any(cycle["status"] != "completed" for cycle in follow_up_cycles):
        return ["follow_up_incomplete"]
    return []


def _matching_follow_up_message(
    acceptance: TaskMessage,
    follow_up_messages: list[TaskMessage],
) -> TaskMessage | None:
    return next(
        (
            message
            for message in follow_up_messages
            if message.sequence > acceptance.sequence
        ),
        None,
    )


def _overall_status(blocked_reasons: list[str]) -> str:
    if not blocked_reasons:
        return "healthy"
    if any(
        reason.endswith("_missing") or reason == "missing_manager"
        for reason in blocked_reasons
    ):
        return "blocked"
    return "attention"


def _phase_status(steps: list[TaskStep]) -> str:
    if not steps:
        return "missing"
    if all(step.status == "completed" for step in steps):
        return "completed"
    if any(step.status in {"running", "queued", "waiting_approval"} for step in steps):
        return "in_progress"
    if any(step.status == "failed" for step in steps):
        return "failed"
    return "attention"


def _cycle_status(follow_up_steps: list[TaskStep], review_steps: list[TaskStep]) -> str:
    if not follow_up_steps or not review_steps:
        return "blocked"
    if all(step.status == "completed" for step in [*follow_up_steps, *review_steps]):
        return "completed"
    if any(step.status == "failed" for step in [*follow_up_steps, *review_steps]):
        return "failed"
    return "in_progress"


def _acceptance_status(decision: str, follow_up: TaskMessage | None) -> str:
    if decision == "approved":
        return "approved"
    return "follow_up_created" if follow_up is not None else "needs_follow_up"


def _decision_from_message(message: TaskMessage) -> str:
    payload = message.payload if isinstance(message.payload, dict) else {}
    decision = payload.get("decision")
    return decision if isinstance(decision, str) and decision else "recorded"


def _message_summary(message: TaskMessage, fallback: str) -> str:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary") or payload.get("status")
    return summary if isinstance(summary, str) and summary else fallback


def _review_mode(step: TaskStep) -> str | None:
    policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    mode = policy.get("mode")
    return mode if isinstance(mode, str) else None


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _step_id(step: object) -> UUID | None:
    return step.id if isinstance(step, TaskStep) else None


def _agent_payload(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }
