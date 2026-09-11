from collections import Counter
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.observation_utils import (
    review_event_status,
    safe_message_payload,
    status_from_message_type,
    str_or_none,
)


class TaskObservationOverviewCards:
    def build(
        self,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        agents: dict[UUID, AgentProfile],
    ) -> list[dict[str, object]]:
        active_agent_ids = {
            run.agent_profile_id
            for run in runs
            if run.agent_profile_id is not None and run.status in {"queued", "running"}
        }
        current_steps = [
            step_card(step, agents)
            for step in steps
            if step.status in {"queued", "running", "waiting_approval", "blocked"}
        ]
        if not current_steps and steps:
            current_steps = [step_card(steps[-1], agents)]

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
                        agent_payload(agents[agent_id])
                        for agent_id in sorted(active_agent_ids, key=str)
                        if agent_id in agents
                    ],
                },
            },
        ]
        cards.extend(current_steps)
        return cards


class TaskObservationTimelineCards:
    def build(
        self,
        messages: list[TaskMessage],
        run_events: list[RunEvent],
    ) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = [
            {
                "card_type": "task_message",
                "title": message.message_type,
                "status": status_from_message_type(message.message_type),
                "data": {
                    "id": str(message.id),
                    "sequence": message.sequence,
                    "body": message.body,
                    "task_step_id": str_or_none(message.task_step_id),
                    "agent_run_id": str_or_none(message.agent_run_id),
                    "agent_profile_id": str_or_none(message.agent_profile_id),
                    "payload": safe_message_payload(message.payload),
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
        return cards


class TaskObservationArtifactCards:
    def build(self, artifacts: list[Artifact]) -> list[dict[str, object]]:
        return [
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
                    "agent_run_id": str_or_none(artifact.agent_run_id),
                    "task_step_id": str_or_none(artifact.task_step_id),
                    "agent_profile_id": str_or_none(artifact.agent_profile_id),
                    "work_package_id": artifact.work_package_id,
                    "version": artifact.version,
                    "supersedes_artifact_id": str_or_none(artifact.supersedes_artifact_id),
                    "review_status": artifact.review_status,
                    "metadata": redact_sensitive_payload(artifact.artifact_metadata),
                    "created_at": artifact.created_at,
                },
            }
            for artifact in artifacts
        ]


class TaskObservationReviewCards:
    def build(self, task: Task, messages: list[TaskMessage]) -> list[dict[str, object]]:
        review_messages = [
            message
            for message in messages
            if message.message_type
            in {"pm.acceptance_decision", "pm.follow_up_created", "approval.requested"}
        ]
        cards: list[dict[str, object]] = [
            {
                "card_type": "review_event",
                "title": message.message_type,
                "status": review_event_status(message),
                "data": {
                    "sequence": message.sequence,
                    "body": message.body,
                    "payload": safe_message_payload(message.payload),
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
                    "data": redact_sensitive_payload(task.final_output),
                }
            )
        return cards


def step_card(step: TaskStep, agents: dict[UUID, AgentProfile]) -> dict[str, object]:
    agent = agents.get(step.assigned_agent_profile_id) if step.assigned_agent_profile_id else None
    return {
        "card_type": "task_step",
        "title": step.title,
        "status": step.status,
        "data": redact_sensitive_payload(
            {
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
                "agent": agent_payload(agent) if agent is not None else None,
            }
        ),
    }


def agent_payload(agent: AgentProfile) -> dict[str, object]:
    return {"id": str(agent.id), "name": agent.name, "role": agent.role, "status": agent.status}
