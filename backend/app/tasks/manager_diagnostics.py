from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.tasks.manager_contracts import (
    ManagerAgent,
    ManagerDiagnostics,
    ManagerQueueItem,
    ManagerSteps,
)
from backend.app.tasks.manager_lifecycle import (
    acceptance_payload,
    blocked_reasons,
    decision_from_message,
    follow_up_cycles,
    handoff_chain,
    overall_status,
    step_id,
)
from backend.app.tasks.manager_queue import manager_queue_item, manager_queue_summary
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.operator_dependencies import manager_agent_id as resolve_manager_agent_id
from backend.app.teams.models import AgentTeam


class TaskManagerDiagnosticsService:
    """Explain PM planning, handoff, acceptance, and follow-up work for a task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_diagnostics(self, *, workspace_id: UUID, task_id: UUID) -> ManagerDiagnostics | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = self._steps(workspace_id, task_id)
        messages = self._messages(workspace_id, task_id)
        manager_agent_id = resolve_manager_agent_id(task)
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
        follow_up_cycle_payloads = follow_up_cycles(steps, follow_up_messages)
        blocked_reason_values = blocked_reasons(
            manager_agent_id=manager_agent_id,
            manager_steps=manager_steps,
            specialist_steps=specialist_steps,
            acceptance_messages=acceptance_messages,
            follow_up_cycles=follow_up_cycle_payloads,
        )

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "manager": {
                "agent_profile_id": manager_agent_id,
                "agent": _agent_payload(manager_agent),
                "has_manager": manager_agent_id is not None,
                "planning_step_id": step_id(manager_steps["planning"]),
                "summary_step_ids": [step_id(step) for step in manager_steps["summaries"]],
                "revision_review_step_ids": [
                    step_id(step) for step in manager_steps["revision_reviews"]
                ],
            },
            "summary": {
                "status": overall_status(blocked_reason_values),
                "total_steps": len(steps),
                "specialist_steps": len(specialist_steps),
                "manager_steps": len(manager_steps["all"]),
                "acceptance_decisions": len(acceptance_messages),
                "follow_up_cycles": len(follow_up_cycle_payloads),
                "step_status_counts": dict(sorted(Counter(step.status for step in steps).items())),
                "decision_counts": dict(
                    sorted(
                        Counter(
                            decision_from_message(message) for message in acceptance_messages
                        ).items()
                    )
                ),
            },
            "handoff_chain": handoff_chain(
                manager_steps=manager_steps,
                specialist_steps=specialist_steps,
                acceptance_messages=acceptance_messages,
                follow_up_cycles=follow_up_cycle_payloads,
            ),
            "acceptance_decisions": [
                acceptance_payload(message, follow_up_messages) for message in acceptance_messages
            ],
            "follow_up_cycles": follow_up_cycle_payloads,
            "blocked_reasons": blocked_reason_values,
        }

    def list_manager_queue(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
        status: str | None = None,
        team_id: UUID | None = None,
        include_healthy: bool = False,
    ) -> dict[str, object]:
        statement = select(Task).where(Task.workspace_id == workspace_id)
        if team_id is not None:
            team = self._session.get(AgentTeam, team_id)
            if team is None or team.workspace_id != workspace_id:
                raise ValueError("Team not found")
            statement = statement.where(Task.agent_team_id == team_id)
        if status is not None:
            statement = statement.where(Task.status == status)
        tasks = list(
            self._session.scalars(
                statement.order_by(Task.updated_at.desc(), Task.created_at.desc())
            )
        )

        items: list[ManagerQueueItem] = []
        for task in tasks:
            diagnostics = self.get_diagnostics(workspace_id=workspace_id, task_id=task.id)
            if diagnostics is None:
                continue
            item = manager_queue_item(task, diagnostics)
            if not include_healthy and not item["needs_attention"]:
                continue
            items.append(item)

        total = len(items)
        paged_items = items[offset : offset + limit]
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": manager_queue_summary(items),
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


def _manager_steps(steps: list[TaskStep]) -> ManagerSteps:
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
        step for step in steps if step == planning or step in summaries or step in revision_reviews
    ]
    return {
        "planning": planning,
        "summaries": summaries,
        "revision_reviews": revision_reviews,
        "all": manager_like,
        "ids": {step.id for step in manager_like},
    }


def _review_mode(step: TaskStep) -> str | None:
    policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    mode = policy.get("mode")
    return mode if isinstance(mode, str) else None


def _agent_payload(agent: AgentProfile | None) -> ManagerAgent | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }
