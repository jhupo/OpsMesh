from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.orchestration.tasks.collaboration.contracts import (
    ManagerAgent,
    ManagerDiagnostics,
    ManagerQueueItem,
    ManagerSteps,
)
from opsmesh.orchestration.tasks.collaboration.manager_lifecycle import (
    acceptance_payload,
    blocked_reasons,
    decision_from_message,
    follow_up_cycles,
    handoff_chain,
    overall_status,
    step_id,
)
from opsmesh.orchestration.tasks.collaboration.manager_queue import (
    manager_queue_item,
    manager_queue_summary,
)
from opsmesh.orchestration.tasks.control.step_dependencies import (
    manager_agent_id as resolve_manager_agent_id,
)
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.teams.management.models import AgentTeam


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

        return self.diagnostics_for_tasks(workspace_id=workspace_id, tasks=[task])[task.id]

    def diagnostics_for_tasks(
        self,
        *,
        workspace_id: UUID,
        tasks: list[Task],
        steps: list[TaskStep] | None = None,
    ) -> dict[UUID, ManagerDiagnostics]:
        if any(task.workspace_id != workspace_id for task in tasks):
            raise ValueError("Diagnostic task outside workspace")
        task_ids = {task.id for task in tasks}
        if not task_ids:
            return {}
        if steps is None:
            steps = list(
                self._session.scalars(
                    select(TaskStep)
                    .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id.in_(task_ids))
                    .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
                )
            )
        if any(step.workspace_id != workspace_id or step.task_id not in task_ids for step in steps):
            raise ValueError("Diagnostic step outside task scope")
        steps = sorted(steps, key=lambda step: (step.order_index, step.created_at))
        messages = list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id.in_(task_ids))
                .order_by(TaskMessage.sequence.asc())
            )
        )
        agent_ids = {
            identifier
            for identifier in (
                [resolve_manager_agent_id(task) for task in tasks]
                + [step.assigned_agent_profile_id for step in steps]
                + [message.agent_profile_id for message in messages]
            )
            if identifier is not None
        }
        agents = (
            {
                agent.id: agent
                for agent in self._session.scalars(
                    select(AgentProfile).where(
                        AgentProfile.workspace_id == workspace_id, AgentProfile.id.in_(agent_ids)
                    )
                )
            }
            if agent_ids
            else {}
        )
        steps_by_task: dict[UUID, list[TaskStep]] = defaultdict(list)
        messages_by_task: dict[UUID, list[TaskMessage]] = defaultdict(list)
        for step in steps:
            steps_by_task[step.task_id].append(step)
        for message in messages:
            messages_by_task[message.task_id].append(message)
        return {
            task.id: _manager_diagnostics(
                workspace_id=workspace_id,
                task=task,
                steps=steps_by_task[task.id],
                messages=messages_by_task[task.id],
                agents=agents,
            )
            for task in tasks
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

        diagnostics_by_task = self.diagnostics_for_tasks(workspace_id=workspace_id, tasks=tasks)
        items: list[ManagerQueueItem] = []
        for task in tasks:
            diagnostics = diagnostics_by_task.get(task.id)
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


def _manager_diagnostics(
    *,
    workspace_id: UUID,
    task: Task,
    steps: list[TaskStep],
    messages: list[TaskMessage],
    agents: dict[UUID, AgentProfile],
) -> ManagerDiagnostics:
    manager_agent_id = resolve_manager_agent_id(task)
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
        "task_id": task.id,
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


def _manager_steps(steps: list[TaskStep]) -> ManagerSteps:
    planning = next((step for step in steps if _review_mode(step) == "agent_planning"), None)
    summaries = [step for step in steps if _review_mode(step) == "final_acceptance"]
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
