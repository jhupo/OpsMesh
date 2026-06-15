from __future__ import annotations

from collections import Counter
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.observation_domain import TaskObservationDomainCards
from backend.app.tasks.observation_utils import (
    review_event_status,
    risk_flags_from_payload,
    safe_message_payload,
    status_from_message_type,
    str_or_none,
)


class TaskObservationSectionBuilder:
    def __init__(self) -> None:
        self._domain_cards = TaskObservationDomainCards()

    def sections(
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
            self.overview_section(task, steps, runs, agents),
            self.timeline_section(messages, run_events),
            self.artifact_section(artifacts),
            self.review_section(task, messages),
            self.quality_section(steps, messages),
            self._domain_cards.domain_section(task, view_type, artifacts),
        ]

    def overview_section(
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
            self.step_card(step, agents)
            for step in steps
            if step.status in {"queued", "running", "waiting_approval", "blocked"}
        ]
        if not current_steps and steps:
            current_steps = [self.step_card(steps[-1], agents)]

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
        return {"key": "overview", "title": "Overview", "cards": cards}

    def timeline_section(
        self,
        messages: list[TaskMessage],
        run_events: list[RunEvent],
    ) -> dict[str, object]:
        cards = [
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
        return {"key": "timeline", "title": "Timeline", "cards": cards}

    def artifact_section(self, artifacts: list[Artifact]) -> dict[str, object]:
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
                        "agent_run_id": str_or_none(artifact.agent_run_id),
                        "task_step_id": str_or_none(artifact.task_step_id),
                        "agent_profile_id": str_or_none(artifact.agent_profile_id),
                        "work_package_id": artifact.work_package_id,
                        "version": artifact.version,
                        "supersedes_artifact_id": str_or_none(
                            artifact.supersedes_artifact_id
                        ),
                        "review_status": artifact.review_status,
                        "metadata": redact_sensitive_payload(artifact.artifact_metadata),
                        "created_at": artifact.created_at,
                    },
                }
                for artifact in artifacts
            ],
        }

    def review_section(self, task: Task, messages: list[TaskMessage]) -> dict[str, object]:
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
        return {"key": "review", "title": "Review", "cards": cards}

    def quality_section(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> dict[str, object]:
        return {
            "key": "quality",
            "title": "Quality",
            "cards": [
                *self.revision_history_cards(steps, messages),
                *self.risk_flag_cards(steps, messages),
            ],
        }

    def revision_history_cards(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = []
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            correction = dependencies.get("correction")
            if isinstance(correction, dict):
                cards.append(
                    {
                        "card_type": "revision_history",
                        "title": step.title,
                        "status": step.status,
                        "data": {
                            "source": "correction",
                            "task_step_id": str(step.id),
                            "work_package_id": step.work_package_id,
                            "mode": correction.get("mode"),
                            "target": redact_sensitive_payload(
                                correction.get("target")
                                if isinstance(correction.get("target"), dict)
                                else {}
                            ),
                            "instruction": correction.get("instruction"),
                            "metadata": redact_sensitive_payload(
                                correction.get("metadata")
                                if isinstance(correction.get("metadata"), dict)
                                else {}
                            ),
                            "created_at": step.created_at,
                        },
                    }
                )
            if "revision_of_work_package_id" in dependencies:
                cards.append(
                    {
                        "card_type": "revision_history",
                        "title": step.title,
                        "status": step.status,
                        "data": {
                            "source": "pm_revision",
                            "task_step_id": str(step.id),
                            "work_package_id": step.work_package_id,
                            "revision_of_work_package_id": dependencies.get(
                                "revision_of_work_package_id"
                            ),
                            "revision_cycle": dependencies.get("revision_cycle"),
                            "created_at": step.created_at,
                        },
                    }
                )
        for message in messages:
            if message.message_type != "pm.acceptance_decision":
                continue
            revision_requests = message.payload.get("revision_requests")
            if isinstance(revision_requests, list) and revision_requests:
                cards.append(
                    {
                        "card_type": "revision_history",
                        "title": "PM revision request",
                        "status": str(message.payload.get("decision") or "recorded"),
                        "data": {
                            "source": "pm_acceptance",
                            "sequence": message.sequence,
                            "revision_requests": redact_sensitive_payload(
                                {"items": revision_requests}
                            )["items"],
                            "created_at": message.created_at,
                        },
                    }
                )
        return cards

    def risk_flag_cards(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = []
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            reason = dependencies.get("blocked_reason")
            if isinstance(reason, str) and reason:
                cards.append(
                    {
                        "card_type": "risk_flag",
                        "title": step.title,
                        "status": "attention",
                        "data": {
                            "source": "scheduler",
                            "task_step_id": str(step.id),
                            "reason": reason,
                            "blocked_resource_keys": dependencies.get("blocked_resource_keys"),
                            "priority_score": dependencies.get("priority_score"),
                        },
                    }
                )
        for message in messages:
            if message.message_type not in {"pm.acceptance_decision", "approval.requested"}:
                continue
            payload = safe_message_payload(message.payload)
            risks = risk_flags_from_payload(payload)
            for risk in risks:
                cards.append(
                    {
                        "card_type": "risk_flag",
                        "title": str(risk.get("title") or message.message_type),
                        "status": str(risk.get("severity") or "attention"),
                        "data": {
                            "source": message.message_type,
                            "sequence": message.sequence,
                            "risk": risk,
                            "created_at": message.created_at,
                        },
                    }
                )
        return cards

    def summary(
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

    def step_card(
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
