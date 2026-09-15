from collections import Counter
from uuid import UUID

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import dict_or_empty, stringify_or_none
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.workspace.storage.artifact_models import Artifact


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
                    "task_step_id": stringify_or_none(message.task_step_id),
                    "agent_run_id": stringify_or_none(message.agent_run_id),
                    "agent_profile_id": stringify_or_none(message.agent_profile_id),
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
                    "agent_run_id": stringify_or_none(artifact.agent_run_id),
                    "task_step_id": stringify_or_none(artifact.task_step_id),
                    "agent_profile_id": stringify_or_none(artifact.agent_profile_id),
                    "work_package_id": artifact.work_package_id,
                    "version": artifact.version,
                    "supersedes_artifact_id": stringify_or_none(artifact.supersedes_artifact_id),
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


def safe_message_payload(payload: dict[str, object]) -> dict[str, object]:
    return redact_sensitive_payload(payload)


def status_from_message_type(message_type: str) -> str:
    if message_type.endswith(".failed") or message_type.endswith(".blocked"):
        return "attention"
    if message_type.endswith(".completed") or message_type == "pm.acceptance_decision":
        return "completed"
    return "recorded"


def review_event_status(message: TaskMessage) -> str:
    return str(message.payload.get("decision") or message.payload.get("status") or "recorded")


class TaskObservationQualityCards:
    def build(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        return [
            *self.revision_history_cards(steps, messages),
            *self.risk_flag_cards(steps, messages),
        ]

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
                                dict_or_empty(correction.get("target"))
                            ),
                            "instruction": correction.get("instruction"),
                            "metadata": redact_sensitive_payload(
                                dict_or_empty(correction.get("metadata"))
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


def risk_flags_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    risks: list[dict[str, object]] = []
    raw_risks = payload.get("risks")
    if isinstance(raw_risks, list):
        risks.extend(item for item in raw_risks if isinstance(item, dict))

    risk_level = payload.get("risk_level")
    if isinstance(risk_level, str) and risk_level:
        risks.append(
            {
                "title": str(payload.get("title") or "Risk flagged"),
                "severity": risk_level,
                "reason": payload.get("reason") or payload.get("summary"),
            }
        )

    decision = payload.get("decision")
    reasons = payload.get("reasons")
    if decision == "request_revision":
        risks.append(
            {
                "title": "Revision requested",
                "severity": "attention",
                "reason": reasons if isinstance(reasons, list) else payload.get("summary"),
            }
        )
    return risks
