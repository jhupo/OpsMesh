from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.typing import dict_list, string_list
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_contracts import ManagerDiagnostics
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService


class CollaborationParticipant(TypedDict):
    agent_profile_id: UUID
    name: object
    role: object
    collaboration_role: str
    assigned_step_count: int
    completed_step_count: int
    active_step_count: int


class TaskCollaborationStateService:
    """Assemble the task-level multi-agent collaboration protocol state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_state(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
    ) -> dict[str, object] | None:
        execution = TaskExecutionDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        if execution is None:
            return None
        manager = TaskManagerDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        if manager is None:
            return None

        steps = dict_list(execution.get("steps"))
        handoffs = _handoff_items(steps)
        manager_chain = dict_list(manager.get("handoff_chain"))
        phases = _collaboration_phases(manager_chain, handoffs)
        blocked_reasons = _blocked_reasons(manager, handoffs, steps)
        recommended_actions = _recommended_actions(manager, handoffs, blocked_reasons)
        protocol_status = _protocol_status(phases, blocked_reasons)
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "task": execution.get("task", {}),
            "summary": {
                "status": protocol_status,
                "phase_counts": dict(
                    sorted(Counter(str(phase["status"]) for phase in phases).items())
                ),
                "blocked_reasons": blocked_reasons,
                "recommended_actions": recommended_actions,
                "participant_count": len(_participants(manager, steps)),
                "handoff_count": len(handoffs),
                "attention_handoff_count": sum(
                    1 for item in handoffs if item["status"] in ATTENTION_HANDOFF_STATUSES
                ),
            },
            "participants": _participants(manager, steps),
            "phases": phases,
            "handoffs": handoffs,
            "manager": {
                "summary": manager["summary"],
                "blocked_reasons": string_list(manager.get("blocked_reasons")),
                "acceptance_decisions": manager.get("acceptance_decisions", []),
                "follow_up_cycles": manager.get("follow_up_cycles", []),
            },
        }


ATTENTION_HANDOFF_STATUSES = {
    "ready_for_downstream",
    "downstream_blocked",
    "handoff_in_progress",
}


def _collaboration_phases(
    manager_chain: list[dict[str, object]],
    handoffs: list[dict[str, object]],
) -> list[dict[str, object]]:
    phases = [
        _manager_phase_payload(phase)
        for phase in manager_chain
        if phase.get("phase") in {"manager_planning", "specialist_execution"}
    ]
    handoff_counts = Counter(str(item["status"]) for item in handoffs)
    phases.append(
        {
            "phase": "handoff",
            "status": _handoff_phase_status(handoff_counts),
            "step_ids": [item["task_step_id"] for item in handoffs],
            "blocked_reasons": _handoff_phase_blockers(handoff_counts),
            "recommended_actions": _handoff_phase_actions(handoff_counts),
            "metadata": {"handoff_status_counts": dict(sorted(handoff_counts.items()))},
        }
    )
    phases.extend(
        _manager_phase_payload(phase)
        for phase in manager_chain
        if phase.get("phase") in {"manager_acceptance", "follow_up"}
    )
    return phases


def _manager_phase_payload(phase: dict[str, object]) -> dict[str, object]:
    return {
        "phase": phase.get("phase"),
        "status": phase.get("status"),
        "step_ids": _uuid_list(phase.get("step_ids")),
        "blocked_reasons": string_list(phase.get("blocked_reasons")),
        "recommended_actions": _manager_phase_actions(phase),
        "metadata": {},
    }


def _handoff_phase_status(handoff_counts: Counter[str]) -> str:
    if handoff_counts.get("downstream_blocked", 0) > 0:
        return "blocked"
    if handoff_counts.get("ready_for_downstream", 0) > 0:
        return "ready"
    if handoff_counts.get("handoff_in_progress", 0) > 0:
        return "running"
    if not handoff_counts or set(handoff_counts) <= {"no_downstream", "source_incomplete"}:
        return "not_required"
    return "healthy"


def _handoff_phase_blockers(handoff_counts: Counter[str]) -> list[str]:
    blockers: list[str] = []
    if handoff_counts.get("downstream_blocked", 0) > 0:
        blockers.append("downstream_blocked")
    if handoff_counts.get("source_incomplete", 0) > 0:
        blockers.append("handoff_source_incomplete")
    return blockers


def _handoff_phase_actions(handoff_counts: Counter[str]) -> list[str]:
    actions: list[str] = []
    if handoff_counts.get("ready_for_downstream", 0) > 0:
        actions.append("schedule_downstream_steps")
    if handoff_counts.get("downstream_blocked", 0) > 0:
        actions.append("inspect_blocked_downstream")
    return actions


def _manager_phase_actions(phase: dict[str, object]) -> list[str]:
    blocked_reasons = string_list(phase.get("blocked_reasons"))
    phase_name = phase.get("phase")
    actions: list[str] = []
    if any(reason.startswith("missing_manager") for reason in blocked_reasons):
        actions.append("request_manager_review")
    if phase_name == "specialist_execution" and "specialist_steps_incomplete" in blocked_reasons:
        actions.append("monitor_specialists")
    if phase_name == "follow_up" and blocked_reasons:
        actions.append("monitor_follow_up")
    return actions


def _handoff_items(steps: list[dict[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for step in steps:
        handoff = step.get("handoff")
        if not isinstance(handoff, dict):
            continue
        if handoff.get("requires_handoff") is not True and handoff.get("status") not in {
            "downstream_blocked",
            "ready_for_downstream",
            "handoff_in_progress",
        }:
            continue
        items.append(
            {
                "task_step_id": step.get("task_step_id"),
                "work_package_id": step.get("work_package_id"),
                "title": step.get("title"),
                "status": handoff.get("status"),
                "requires_handoff": handoff.get("requires_handoff") is True,
                "upstream_step_ids": _uuid_list(handoff.get("upstream_step_ids")),
                "downstream_step_ids": _uuid_list(handoff.get("downstream_step_ids")),
                "runnable_downstream_step_ids": _uuid_list(
                    handoff.get("runnable_downstream_step_ids")
                ),
                "blocked_downstream_step_ids": _uuid_list(
                    handoff.get("blocked_downstream_step_ids")
                ),
                "recommended_actions": string_list(handoff.get("recommended_actions")),
            }
        )
    return items


def _participants(
    manager: ManagerDiagnostics,
    steps: list[dict[str, object]],
) -> list[CollaborationParticipant]:
    participants: dict[UUID, CollaborationParticipant] = {}
    manager_payload = manager["manager"]
    manager_agent = manager_payload["agent"]
    manager_id = manager_payload.get("agent_profile_id")
    if isinstance(manager_id, UUID):
        participants[manager_id] = {
            "agent_profile_id": manager_id,
            "name": manager_agent.get("name") if manager_agent is not None else None,
            "role": manager_agent.get("role") if manager_agent is not None else "manager",
            "collaboration_role": "manager",
            "assigned_step_count": 0,
            "completed_step_count": 0,
            "active_step_count": 0,
        }
    for step in steps:
        assigned_agent = step.get("assigned_agent")
        if not isinstance(assigned_agent, dict):
            continue
        agent_id = assigned_agent.get("id")
        if not isinstance(agent_id, UUID):
            continue
        participant = participants.setdefault(
            agent_id,
            {
                "agent_profile_id": agent_id,
                "name": assigned_agent.get("name"),
                "role": assigned_agent.get("role"),
                "collaboration_role": "specialist",
                "assigned_step_count": 0,
                "completed_step_count": 0,
                "active_step_count": 0,
            },
        )
        participant["assigned_step_count"] += 1
        if step.get("status") == "completed":
            participant["completed_step_count"] += 1
        elif step.get("status") in {"queued", "running"}:
            participant["active_step_count"] += 1
    return sorted(
        participants.values(),
        key=lambda item: (str(item.get("collaboration_role")), str(item.get("name") or "")),
    )


def _blocked_reasons(
    manager: ManagerDiagnostics,
    handoffs: list[dict[str, object]],
    steps: list[dict[str, object]],
) -> list[str]:
    reasons = string_list(manager.get("blocked_reasons"))
    for handoff in handoffs:
        status = handoff.get("status")
        if status == "downstream_blocked":
            reasons.append("downstream_blocked")
        elif status == "ready_for_downstream":
            reasons.append("handoff_ready_for_downstream")
    for step in steps:
        for reason in string_list(step.get("blocked_reasons")):
            reasons.append(f"step:{reason}")
    return list(dict.fromkeys(reasons))


def _recommended_actions(
    manager: ManagerDiagnostics,
    handoffs: list[dict[str, object]],
    blocked_reasons: list[str],
) -> list[str]:
    actions: list[str] = []
    if any(reason.startswith("missing_manager") for reason in blocked_reasons):
        actions.append("request_manager_review")
    if "acceptance_decision_missing" in blocked_reasons:
        actions.append("request_manager_review")
    if "handoff_ready_for_downstream" in blocked_reasons:
        actions.append("schedule_downstream_steps")
    if "downstream_blocked" in blocked_reasons:
        actions.append("inspect_blocked_downstream")
    for handoff in handoffs:
        actions.extend(string_list(handoff.get("recommended_actions")))
    summary = manager["summary"]
    if summary.get("status") == "healthy" and not actions:
        actions.append("monitor_delivery")
    return list(dict.fromkeys(actions))


def _protocol_status(phases: list[dict[str, object]], blocked_reasons: list[str]) -> str:
    if any(reason.startswith("missing_manager") for reason in blocked_reasons):
        return "blocked"
    if "downstream_blocked" in blocked_reasons:
        return "blocked"
    if any(phase.get("status") in {"blocked", "ready"} for phase in phases):
        return "needs_attention"
    if blocked_reasons:
        return "needs_attention"
    if phases and all(
        phase.get("status") in {"completed", "healthy", "not_required"} for phase in phases
    ):
        return "complete"
    return "running"


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]
