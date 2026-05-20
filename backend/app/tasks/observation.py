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
from backend.app.tasks.models import Task, TaskMessage, TaskStep

SUPPORTED_VIEW_TYPES = {"generic", "aigc", "novel", "research", "software"}


class TaskObservationService:
    """Compose a task observation payload from existing durable task records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_observation(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        view_type: str | None = None,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        resolved_view_type = self._resolve_view_type(task, view_type)
        steps = self._list_steps(workspace_id, task.id)
        runs = self._list_runs(workspace_id, task.id)
        messages = self._list_messages(workspace_id, task.id)
        artifacts = self._list_artifacts(workspace_id, task.id)
        run_events = self._list_run_events(workspace_id, runs)
        agents = self._agent_map(workspace_id, steps, runs, messages)

        return {
            "workspace_id": task.workspace_id,
            "task_id": task.id,
            "view_type": resolved_view_type,
            "generated_at": datetime.now(UTC),
            "summary": self._summary(task, steps, runs, messages, artifacts),
            "sections": self._sections(
                task=task,
                view_type=resolved_view_type,
                steps=steps,
                runs=runs,
                messages=messages,
                artifacts=artifacts,
                run_events=run_events,
                agents=agents,
            ),
        }

    def _resolve_view_type(self, task: Task, requested: str | None) -> str:
        if requested is not None and requested != "auto":
            normalized = requested.strip().lower()
            if normalized not in SUPPORTED_VIEW_TYPES:
                raise ValueError("Unsupported observation view type")
            return normalized
        domain_type = (task.domain_type or "generic").strip().lower()
        aliases = {
            "image": "aigc",
            "video": "aigc",
            "content": "aigc",
            "novel_writing": "novel",
            "writing": "novel",
            "market_research": "research",
            "software_development": "software",
            "code": "software",
        }
        return aliases.get(
            domain_type,
            domain_type if domain_type in SUPPORTED_VIEW_TYPES else "generic",
        )

    def _sections(
        self,
        *,
        task: Task,
        view_type: str,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
        run_events: list[RunEvent],
        agents: dict[UUID, AgentProfile],
    ) -> list[dict[str, object]]:
        return [
            self._overview_section(task, steps, runs, agents),
            self._timeline_section(messages, run_events),
            self._artifact_section(artifacts),
            self._review_section(task, messages),
            self._domain_section(task, view_type, artifacts),
        ]

    def _overview_section(
        self,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        agents: dict[UUID, AgentProfile],
    ) -> dict[str, object]:
        active_agent_ids = {
            run.agent_profile_id
            for run in runs
            if run.agent_profile_id is not None and run.status in {"queued", "running"}
        }
        current_steps = [
            self._step_card(step, agents)
            for step in steps
            if step.status in {"queued", "running", "waiting_approval", "blocked"}
        ]
        if not current_steps and steps:
            current_steps = [self._step_card(steps[-1], agents)]

        cards: list[dict[str, object]] = [
            {
                "card_type": "task_status",
                "title": task.title,
                "status": task.status,
                "data": {
                    "domain_type": task.domain_type,
                    "priority": task.priority,
                    "description": task.description,
                    "completed_at": task.completed_at,
                },
            },
            {
                "card_type": "active_agents",
                "title": "Active agents",
                "status": "active" if active_agent_ids else "idle",
                "data": {
                    "agents": [
                        self._agent_payload(agents[agent_id])
                        for agent_id in sorted(active_agent_ids, key=str)
                        if agent_id in agents
                    ],
                },
            },
        ]
        cards.extend(current_steps)
        return {"key": "overview", "title": "Overview", "cards": cards}

    def _timeline_section(
        self,
        messages: list[TaskMessage],
        run_events: list[RunEvent],
    ) -> dict[str, object]:
        cards = [
            {
                "card_type": "task_message",
                "title": message.message_type,
                "status": self._status_from_message_type(message.message_type),
                "data": {
                    "id": str(message.id),
                    "sequence": message.sequence,
                    "body": message.body,
                    "task_step_id": self._str_or_none(message.task_step_id),
                    "agent_run_id": self._str_or_none(message.agent_run_id),
                    "agent_profile_id": self._str_or_none(message.agent_profile_id),
                    "payload": self._safe_message_payload(message.payload),
                    "created_at": message.created_at,
                },
            }
            for message in messages[-20:]
        ]
        event_counts = Counter(event.event_type for event in run_events)
        if event_counts:
            cards.append(
                {
                    "card_type": "run_event_summary",
                    "title": "Run events",
                    "status": "recorded",
                    "data": {"counts": dict(sorted(event_counts.items()))},
                }
            )
        return {"key": "timeline", "title": "Timeline", "cards": cards}

    def _artifact_section(self, artifacts: list[Artifact]) -> dict[str, object]:
        return {
            "key": "artifacts",
            "title": "Artifacts",
            "cards": [
                {
                    "card_type": "artifact",
                    "title": artifact.filename,
                    "status": "available",
                    "data": {
                        "id": str(artifact.id),
                        "artifact_type": artifact.artifact_type,
                        "content_type": artifact.content_type,
                        "size_bytes": artifact.size_bytes,
                        "checksum_sha256": artifact.checksum_sha256,
                        "agent_run_id": self._str_or_none(artifact.agent_run_id),
                        "task_step_id": self._str_or_none(artifact.task_step_id),
                        "agent_profile_id": self._str_or_none(artifact.agent_profile_id),
                        "work_package_id": artifact.work_package_id,
                        "version": artifact.version,
                        "supersedes_artifact_id": self._str_or_none(
                            artifact.supersedes_artifact_id
                        ),
                        "review_status": artifact.review_status,
                        "metadata": artifact.artifact_metadata,
                        "created_at": artifact.created_at,
                    },
                }
                for artifact in artifacts
            ],
        }

    def _review_section(self, task: Task, messages: list[TaskMessage]) -> dict[str, object]:
        review_messages = [
            message
            for message in messages
            if message.message_type
            in {"pm.acceptance_decision", "pm.follow_up_created", "approval.requested"}
        ]
        cards = [
            {
                "card_type": "review_event",
                "title": message.message_type,
                "status": str(
                    message.payload.get("decision")
                    or message.payload.get("status")
                    or "recorded"
                ),
                "data": {
                    "sequence": message.sequence,
                    "body": message.body,
                    "payload": self._safe_message_payload(message.payload),
                    "created_at": message.created_at,
                },
            }
            for message in review_messages
        ]
        if task.final_output is not None:
            cards.append(
                {
                    "card_type": "final_output",
                    "title": "Final output",
                    "status": task.status,
                    "data": task.final_output,
                }
            )
        return {"key": "review", "title": "Review", "cards": cards}

    def _domain_section(
        self,
        task: Task,
        view_type: str,
        artifacts: list[Artifact],
    ) -> dict[str, object]:
        builders = {
            "aigc": self._aigc_cards,
            "novel": self._novel_cards,
            "research": self._research_cards,
            "software": self._software_cards,
        }
        cards = builders.get(view_type, self._generic_domain_cards)(task, artifacts)
        return {"key": "domain", "title": f"{view_type.title()} View", "cards": cards}

    def _generic_domain_cards(
        self,
        task: Task,
        artifacts: list[Artifact],
    ) -> list[dict[str, object]]:
        return [
            {
                "card_type": "domain_state",
                "title": "Domain state",
                "status": "available" if task.domain_state else "empty",
                "data": {
                    "input": task.input,
                    "generic_state": task.generic_state,
                    "domain_state": task.domain_state,
                    "artifact_count": len(artifacts),
                },
            }
        ]

    def _aigc_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._domain_card("prompt", "Prompt", state, task.input),
            self._domain_card("variants", "Variants", state, task.generic_state),
            self._domain_card("selected_asset", "Selected asset", state, {}),
            self._domain_card("model_settings", "Model settings", state, task.input),
            self._domain_card("review_notes", "Review notes", state, task.generic_state),
            self._domain_artifact_card(artifacts),
        ]

    def _novel_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._domain_card("outline", "Outline", state, task.input),
            self._domain_card("chapters", "Chapters", state, task.generic_state),
            self._domain_card("scenes", "Scenes", state, task.generic_state),
            self._domain_card("characters", "Characters", state, task.generic_state),
            self._domain_card("continuity_notes", "Continuity notes", state, task.generic_state),
            self._domain_card("word_count", "Word count", state, task.generic_state),
            self._domain_card("editorial_review", "Editorial review", state, task.generic_state),
            self._domain_artifact_card(artifacts),
        ]

    def _research_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._domain_card("sources", "Sources", state, task.input),
            self._domain_card("claims", "Claims", state, task.generic_state),
            self._domain_card("confidence", "Confidence", state, task.generic_state),
            self._domain_card("citations", "Citations", state, task.generic_state),
            self._domain_card("report_sections", "Report sections", state, task.generic_state),
            self._domain_artifact_card(artifacts),
        ]

    def _software_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._domain_card("requirements", "Requirements", state, task.input),
            self._domain_card("design_tasks", "Design tasks", state, task.generic_state),
            self._domain_card("branches", "Branches", state, task.generic_state),
            self._domain_card("patches", "Patches", state, task.generic_state),
            self._domain_card("tests", "Tests", state, task.generic_state),
            self._domain_card("build_status", "Build status", state, task.generic_state),
            self._domain_card("review_comments", "Review comments", state, task.generic_state),
            self._domain_artifact_card(artifacts),
        ]

    def _domain_card(
        self,
        key: str,
        title: str,
        primary: dict[str, object],
        fallback: dict[str, object],
    ) -> dict[str, object]:
        value = primary.get(key, fallback.get(key))
        return {
            "card_type": key,
            "title": title,
            "status": "available" if value not in (None, "", [], {}) else "empty",
            "data": {"value": value},
        }

    def _domain_artifact_card(self, artifacts: list[Artifact]) -> dict[str, object]:
        return {
            "card_type": "domain_artifacts",
            "title": "Related artifacts",
            "status": "available" if artifacts else "empty",
            "data": {
                "items": [
                    {
                        "id": str(artifact.id),
                        "filename": artifact.filename,
                        "artifact_type": artifact.artifact_type,
                        "content_type": artifact.content_type,
                    }
                    for artifact in artifacts
                ]
            },
        }

    def _summary(
        self,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
    ) -> dict[str, object]:
        step_counts = Counter(step.status for step in steps)
        run_counts = Counter(run.status for run in runs)
        completed_steps = step_counts.get("completed", 0)
        progress = completed_steps / len(steps) if steps else 0.0
        return {
            "title": task.title,
            "status": task.status,
            "domain_type": task.domain_type,
            "priority": task.priority,
            "progress": round(progress, 4),
            "step_counts": dict(sorted(step_counts.items())),
            "run_counts": dict(sorted(run_counts.items())),
            "message_count": len(messages),
            "artifact_count": len(artifacts),
            "has_final_output": task.final_output is not None,
        }

    def _step_card(
        self,
        step: TaskStep,
        agents: dict[UUID, AgentProfile],
    ) -> dict[str, object]:
        agent = (
            agents.get(step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id
            else None
        )
        return {
            "card_type": "task_step",
            "title": step.title,
            "status": step.status,
            "data": {
                "id": str(step.id),
                "order_index": step.order_index,
                "work_package_id": step.work_package_id,
                "required_role": step.required_role,
                "required_skills": step.required_skills,
                "expected_artifacts": step.expected_artifacts,
                "acceptance_criteria": step.acceptance_criteria,
                "review_policy": step.review_policy,
                "dependencies": step.dependencies,
                "result_summary": step.result_summary,
                "agent": self._agent_payload(agent) if agent is not None else None,
            },
        }

    def _agent_payload(self, agent: AgentProfile) -> dict[str, object]:
        return {"id": str(agent.id), "name": agent.name, "role": agent.role, "status": agent.status}

    def _safe_message_payload(self, payload: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in payload.items() if key not in _SENSITIVE_PAYLOAD_KEYS}

    def _list_steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _list_runs(self, workspace_id: UUID, task_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc())
            )
        )

    def _list_messages(self, workspace_id: UUID, task_id: UUID) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
                .order_by(TaskMessage.sequence.asc())
            )
        )

    def _list_artifacts(self, workspace_id: UUID, task_id: UUID) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.asc())
            )
        )

    def _list_run_events(self, workspace_id: UUID, runs: list[AgentRun]) -> list[RunEvent]:
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

    def _agent_map(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            item
            for item in (
                [step.assigned_agent_profile_id for step in steps]
                + [run.agent_profile_id for run in runs]
                + [message.agent_profile_id for message in messages]
            )
            if item is not None
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

    def _status_from_message_type(self, message_type: str) -> str:
        if message_type.endswith(".failed") or message_type.endswith(".blocked"):
            return "attention"
        if message_type.endswith(".completed") or message_type == "pm.acceptance_decision":
            return "completed"
        return "recorded"

    def _str_or_none(self, value: Any) -> str | None:
        return str(value) if value is not None else None


_SENSITIVE_PAYLOAD_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "encrypted_api_key",
    "secret",
    "token",
}
