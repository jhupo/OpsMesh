from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskMessage, TaskStep


class TaskTimelineService:
    """Build a workspace-scoped execution timeline for a task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_timeline(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        limit: int = 200,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = self._steps(workspace_id, task_id)
        runs = self._runs(workspace_id, task_id)
        run_events = self._run_events(workspace_id, runs)
        messages = self._messages(workspace_id, task_id)
        artifacts = self._artifacts(workspace_id, task_id)
        agents = self._agents(workspace_id, steps, runs, messages, artifacts)
        events = self._events(
            task=task,
            steps=steps,
            runs=runs,
            run_events=run_events,
            messages=messages,
            artifacts=artifacts,
            agents=agents,
        )
        events.sort(key=_event_sort_key)
        truncated_count = max(0, len(events) - limit)
        if truncated_count:
            events = events[-limit:]

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "summary": {
                "task_status": task.status,
                "total_events": len(events) + truncated_count,
                "returned_events": len(events),
                "truncated_count": truncated_count,
                "source_counts": dict(
                    sorted(Counter(event["source_type"] for event in events).items())
                ),
                "phase_counts": dict(sorted(Counter(event["phase"] for event in events).items())),
                "active_run_count": len(
                    [run for run in runs if run.status in {"queued", "running", "waiting_runtime"}]
                ),
                "artifact_count": len(artifacts),
                "correction_count": len(
                    [
                        message
                        for message in messages
                        if message.message_type == "task.correction.created"
                    ]
                ),
            },
            "events": events,
        }

    def _events(
        self,
        *,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        run_events: list[RunEvent],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
        agents: dict[UUID, AgentProfile],
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = [
            {
                "occurred_at": task.created_at,
                "source_type": "task",
                "event_type": "task.created",
                "phase": "intake",
                "status": task.status,
                "title": task.title,
                "summary": "Task created",
                "task_step_id": None,
                "agent_run_id": None,
                "agent_profile_id": None,
                "artifact_id": None,
                "sequence": 0,
                "agent": None,
                "metadata": {
                    "domain_type": task.domain_type,
                    "priority": task.priority,
                    "agent_team_id": _str_or_none(task.agent_team_id),
                    "runtime_space_id": _str_or_none(task.runtime_space_id),
                    "has_project_plan": task.project_plan is not None,
                    "has_final_output": task.final_output is not None,
                },
            }
        ]
        if task.completed_at is not None:
            events.append(
                {
                    "occurred_at": task.completed_at,
                    "source_type": "task",
                    "event_type": "task.completed",
                    "phase": "review",
                    "status": task.status,
                    "title": task.title,
                    "summary": "Task completed",
                    "task_step_id": None,
                    "agent_run_id": None,
                    "agent_profile_id": None,
                    "artifact_id": None,
                    "sequence": 1,
                    "agent": None,
                    "metadata": {"has_final_output": task.final_output is not None},
                }
            )

        for step in steps:
            events.append(_step_event(step, agents))
        for run in runs:
            events.extend(_run_events(run, agents))
        for event in run_events:
            events.append(_run_event_event(event, runs, agents))
        for message in messages:
            events.append(_message_event(message, agents))
        for artifact in artifacts:
            events.append(_artifact_event(artifact, agents))
        return events

    def _steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _runs(self, workspace_id: UUID, task_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc())
            )
        )

    def _run_events(self, workspace_id: UUID, runs: list[AgentRun]) -> list[RunEvent]:
        run_ids = [run.id for run in runs]
        if not run_ids:
            return []
        return list(
            self._session.scalars(
                select(RunEvent)
                .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
                .order_by(RunEvent.created_at.asc(), RunEvent.sequence.asc())
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

    def _artifacts(self, workspace_id: UUID, task_id: UUID) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.asc())
            )
        )

    def _agents(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            agent_id
            for agent_id in (
                [step.assigned_agent_profile_id for step in steps]
                + [run.agent_profile_id for run in runs]
                + [message.agent_profile_id for message in messages]
                + [artifact.agent_profile_id for artifact in artifacts]
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


def _step_event(step: TaskStep, agents: dict[UUID, AgentProfile]) -> dict[str, object]:
    agent = agents.get(step.assigned_agent_profile_id) if step.assigned_agent_profile_id else None
    return {
        "occurred_at": step.created_at,
        "source_type": "step",
        "event_type": "step.created",
        "phase": "execution",
        "status": step.status,
        "title": step.title,
        "summary": step.result_summary or step.description or "Task step created",
        "task_step_id": step.id,
        "agent_run_id": None,
        "agent_profile_id": step.assigned_agent_profile_id,
        "artifact_id": None,
        "sequence": step.order_index,
        "agent": _agent_payload(agent),
        "metadata": redact_sensitive_payload(
            {
                "work_package_id": step.work_package_id,
                "required_role": step.required_role,
                "required_skills": step.required_skills,
                "expected_artifacts": step.expected_artifacts,
                "acceptance_criteria_count": len(step.acceptance_criteria),
                "review_policy": step.review_policy,
                "dependencies": step.dependencies,
                "runtime_space_id": _str_or_none(step.runtime_space_id),
            }
        ),
    }


def _run_events(run: AgentRun, agents: dict[UUID, AgentProfile]) -> list[dict[str, object]]:
    agent = agents.get(run.agent_profile_id) if run.agent_profile_id else None
    events = [
        {
            "occurred_at": run.created_at,
            "source_type": "run",
            "event_type": "run.created",
            "phase": "execution",
            "status": run.status,
            "title": "Agent run created",
            "summary": "Run queued for execution",
            "task_step_id": run.task_step_id,
            "agent_run_id": run.id,
            "agent_profile_id": run.agent_profile_id,
            "artifact_id": None,
            "sequence": 0,
            "agent": _agent_payload(agent),
            "metadata": _run_metadata(run),
        }
    ]
    if run.started_at is not None:
        events.append(
            {
                **events[0],
                "occurred_at": run.started_at,
                "event_type": "run.started",
                "title": "Agent run started",
                "summary": "Run started",
                "sequence": 1,
            }
        )
    if run.completed_at is not None:
        events.append(
            {
                **events[0],
                "occurred_at": run.completed_at,
                "event_type": f"run.{run.status}",
                "title": "Agent run finished",
                "summary": "Run completed" if run.status == "completed" else "Run finished",
                "sequence": 2,
            }
        )
    return events


def _run_event_event(
    event: RunEvent,
    runs: list[AgentRun],
    agents: dict[UUID, AgentProfile],
) -> dict[str, object]:
    run_by_id = {run.id: run for run in runs}
    run = run_by_id.get(event.agent_run_id)
    agent = (
        agents.get(run.agent_profile_id)
        if run is not None and run.agent_profile_id is not None
        else None
    )
    return {
        "occurred_at": event.created_at,
        "source_type": "run_event",
        "event_type": event.event_type,
        "phase": _phase_for_event_type(event.event_type),
        "status": _status_for_event_type(event.event_type),
        "title": event.event_type,
        "summary": event.message,
        "task_step_id": run.task_step_id if run is not None else None,
        "agent_run_id": event.agent_run_id,
        "agent_profile_id": run.agent_profile_id if run is not None else None,
        "artifact_id": None,
        "sequence": event.sequence,
        "agent": _agent_payload(agent),
        "metadata": redact_sensitive_payload(event.event_metadata),
    }


def _message_event(
    message: TaskMessage,
    agents: dict[UUID, AgentProfile],
) -> dict[str, object]:
    agent = agents.get(message.agent_profile_id) if message.agent_profile_id else None
    return {
        "occurred_at": message.created_at,
        "source_type": "message",
        "event_type": message.message_type,
        "phase": _phase_for_event_type(message.message_type),
        "status": _status_for_event_type(message.message_type),
        "title": message.message_type,
        "summary": _message_summary(message),
        "task_step_id": message.task_step_id,
        "agent_run_id": message.agent_run_id,
        "agent_profile_id": message.agent_profile_id,
        "artifact_id": None,
        "sequence": message.sequence,
        "agent": _agent_payload(agent),
        "metadata": redact_sensitive_payload(message.payload),
    }


def _artifact_event(
    artifact: Artifact,
    agents: dict[UUID, AgentProfile],
) -> dict[str, object]:
    agent = agents.get(artifact.agent_profile_id) if artifact.agent_profile_id else None
    return {
        "occurred_at": artifact.created_at,
        "source_type": "artifact",
        "event_type": "artifact.created",
        "phase": "artifact",
        "status": artifact.review_status,
        "title": artifact.filename,
        "summary": "Artifact recorded",
        "task_step_id": artifact.task_step_id,
        "agent_run_id": artifact.agent_run_id,
        "agent_profile_id": artifact.agent_profile_id,
        "artifact_id": artifact.id,
        "sequence": artifact.version,
        "agent": _agent_payload(agent),
        "metadata": redact_sensitive_payload(
            {
                "artifact_type": artifact.artifact_type,
                "content_type": artifact.content_type,
                "size_bytes": artifact.size_bytes,
                "checksum_sha256": artifact.checksum_sha256,
                "work_package_id": artifact.work_package_id,
                "version": artifact.version,
                "supersedes_artifact_id": _str_or_none(artifact.supersedes_artifact_id),
                "review_status": artifact.review_status,
                "artifact_metadata": artifact.artifact_metadata,
            }
        ),
    }


def _run_metadata(run: AgentRun) -> dict[str, object]:
    run_input = run.input if isinstance(run.input, dict) else {}
    return redact_sensitive_payload(
        {
            "model": run.model,
            "runtime_id": _str_or_none(run.runtime_id),
            "runtime_space_id": _str_or_none(run.runtime_space_id),
            "input_keys": sorted(str(key) for key in run_input),
            "run_scope_snapshot": _run_scope_snapshot_summary(run_input),
            "has_output": run.output is not None,
            "error": run.error,
        }
    )


def _run_scope_snapshot_summary(run_input: dict[str, object]) -> dict[str, object] | None:
    snapshot = run_input.get("authorization_snapshot")
    if not isinstance(snapshot, dict):
        return None

    summary: dict[str, object] = {}
    for key in (
        "version",
        "workspace_id",
        "task_id",
        "task_step_id",
        "agent_profile_id",
        "runtime_space_id",
    ):
        value = snapshot.get(key)
        if value is not None:
            summary[key] = value

    allowed_tools = snapshot.get("allowed_tools")
    if isinstance(allowed_tools, list):
        summary["allowed_tools"] = [tool for tool in allowed_tools if isinstance(tool, str)]

    model_provider = snapshot.get("model_provider")
    if isinstance(model_provider, dict):
        summary["model_provider"] = {
            key: model_provider.get(key)
            for key in (
                "provider",
                "selected_model",
                "model_api",
                "readiness_status",
                "reasons",
                "warnings",
            )
            if model_provider.get(key) is not None
        }

    return summary or None


def _message_summary(message: TaskMessage) -> str:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary") or payload.get("status") or payload.get("decision")
    if isinstance(summary, str) and summary:
        return summary
    if message.message_type == "task.correction.created":
        return "Correction requested"
    return "Task message recorded"


def _phase_for_event_type(event_type: str) -> str:
    if event_type.startswith("planning.") or event_type.startswith("task.plan"):
        return "planning"
    if event_type.startswith("task.correction") or "revision" in event_type:
        return "correction"
    if event_type.startswith("pm.") or "approval" in event_type or "review" in event_type:
        return "review"
    if event_type.startswith("artifact."):
        return "artifact"
    if event_type.startswith("run.") or event_type.startswith("worker."):
        return "execution"
    return "communication"


def _status_for_event_type(event_type: str) -> str:
    if event_type.endswith(".failed") or event_type.endswith(".blocked"):
        return "attention"
    if event_type.endswith(".completed") or event_type.endswith(".approved"):
        return "completed"
    return "recorded"


def _agent_payload(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _event_sort_key(event: dict[str, object]) -> tuple[float, int, int]:
    occurred_at = event["occurred_at"]
    if not isinstance(occurred_at, datetime):
        occurred_at = datetime.min.replace(tzinfo=UTC)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    source_order = {
        "task": 0,
        "step": 1,
        "run": 2,
        "run_event": 3,
        "message": 4,
        "artifact": 5,
    }.get(str(event.get("source_type")), 99)
    sequence = event.get("sequence")
    return (
        occurred_at.timestamp(),
        source_order,
        sequence if isinstance(sequence, int) else 0,
    )


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None
